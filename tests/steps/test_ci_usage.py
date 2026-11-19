"""CI's wait times and compute load: the ci-jobs poller and the cluster sampler.

GitHub is FakeGitHub, the transport of a Caller, since its runs are found by the
day a query names; storage is FakeStore, which keeps what it is given in the
scenario's scratch directory, so pubtest's file steps read it."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pubtest.core import dig, matches, same, show
from pubtest.steps.standins import standins
from pytest_bdd import given, parsers, scenarios, then, when

from integrations.avrofile import decode_ci_jobs, decode_cluster_usage, encode_ci_jobs, encode_cluster_usage
from integrations.calls import Caller, MemorySink, Response
from integrations.ci_usage.cluster import Cgroups, Sampler, millicores, octets
from integrations.ci_usage.jobs import GitHub, Poller, State, board, installed, row

scenarios("ci-usage.feature")

NOW = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
REPO = "example-org/app"
LIMIT = 1048576


def at(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def job(n: int, *, status="completed", queued=-30, started=-20, done=-10, runner="sustenaut-ci-runner-a", **more):
    return {"id": n, "run_id": 7, "run_attempt": 1, "name": f"job {n}", "workflow_name": "ci", "status": status,
            "conclusion": "success" if status == "completed" else None, "created_at": at(queued),
            "started_at": at(started) if started is not None else None,
            "completed_at": at(done) if status == "completed" else None, "labels": ["sustenaut-ci"],
            "runner_name": runner, "runner_group_name": "private" if runner else None, "head_branch": "main", **more}


class FakeGitHub:
    """GitHub's REST API for one repository: its runs by day, each run's jobs."""

    def __init__(self):
        self.runs: list[dict] = []
        self.jobs: dict[int, list[dict]] = {}
        self.asked: list[str] = []
        self.refuse: list[Response] = []

    def send(self, request, timeout):
        url = urlparse(request.url)
        assert request.headers["Authorization"] == "Bearer app-token"
        self.asked.append(url.path)
        if self.refuse:
            return self.refuse.pop(0)
        query = parse_qs(url.query)
        if url.path == "/repos/example-org/release-state/contents/state.json":
            state = {"owner": "example-org", "repos": {"app": {"board": True}, "notes": {"board": False}}}
            return Response(200, json.dumps({"content": base64.b64encode(json.dumps(state).encode()).decode()}).encode())
        if url.path == "/installation/repositories":
            names = [("example-org/app", False), ("example-org/infra", False), ("example-org/old", True)]
            return Response(200, json.dumps({"repositories": [{"full_name": n, "archived": a} for n, a in names]}).encode())
        if url.path.endswith("/actions/runs"):
            begin, end = (datetime.fromisoformat(t.replace("Z", "+00:00")) for t in query["created"][0].split(".."))
            runs = [r for r in self.runs if begin <= datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) <= end]
            return Response(200, json.dumps({"workflow_runs": runs}).encode())
        run = int(url.path.split("/")[-2])
        assert query["filter"] == ["all"]
        return Response(200, json.dumps({"jobs": self.jobs[run]}).encode())

    def run(self, run_id=7, *, status="completed", created=-40, updated=-5, **more):
        self.runs.append({"id": run_id, "name": "ci", "event": "pull_request", "head_branch": "feature", "status": status,
                          "run_attempt": 1, "created_at": at(created), "updated_at": at(updated), **more})


class FakeStore:
    """The ci-jobs tenant's head and the ci-usage tenant's buffer, kept in `root`:
    uploads/ what was uploaded, state.json what the head holds, buffer/ the buffer's
    files, and rows.json the rows they hold, in name order."""

    def __init__(self, root: Path, decode=decode_ci_jobs):
        self.root, self.decode = root, decode
        self.files: dict[str, bytes] = {}
        self.head: str | None = None
        self.buffer: dict[str, bytes] = {}

    def _keep(self, path: str, data: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_head(self, name):
        from integrations.clients.storage import NotFound

        if self.head is None:
            raise NotFound("read", 404, "no head")
        return self.files[self.head], self.head

    def upload_file(self, data, *, name):
        from integrations.clients.storage import StorageError

        if len(data) > LIMIT:
            raise StorageError("write", 413, f"{name} is over {LIMIT} bytes")
        key = f"h{len(self.files)}"
        self.files[key] = data
        self._keep(f"uploads/{key}", data)
        return {"id": key}

    def move_head(self, name, hash, *, if_match=None):  # noqa: A002
        from integrations.clients.storage import StorageError

        if if_match != self.head:
            raise StorageError("write", 412, "written moved just now")
        self.head = hash
        self._keep("state.json", self.files[hash])
        return {"hash": hash}

    def put_buffer(self, place, data):
        self.buffer[place] = data
        self._keep(f"buffer/{place}", data)
        self._keep("rows.json", json.dumps(self.rows(), default=str).encode())
        return {}

    def rows(self):
        return [r for name in sorted(self.buffer) for r in self.decode(self.buffer[name])]


def poller(ctx, now=NOW):
    caller = Caller("integrations", "ci-jobs", sink=MemorySink(), transport=ctx.github, sleep=lambda _: None)
    gh = GitHub(caller, lambda: "app-token", sleep=ctx.sleeps.append)
    return Poller(gh, ctx.store, ctx.store, [REPO], clock=lambda: now, report=lambda _: None)


@given("storage for CI's usage")
def storage(ctx):
    ctx.github, ctx.store, ctx.sleeps = FakeGitHub(), FakeStore(ctx.directory), []


def cell(text: str):
    return None if text == "none" else text


def jobs_from(datatable) -> list[dict]:
    keys, rows = datatable[0], datatable[1:]
    made = []
    for values in rows:
        given_ = {k: cell(v) for k, v in zip(keys, values, strict=True)}
        n = int(given_.pop("id"))
        for k in ("queued", "started", "done"):
            if k in given_ and given_[k] is not None:
                given_[k] = int(given_[k])
        if "labels" in given_:
            given_["labels"] = given_["labels"].split(",")
        made.append(job(n, **given_))
    return made


@given(parsers.parse("a finished run {run:d}, created {created:d} minutes ago and updated {updated:d} minutes ago, "
                     "with the jobs:"))
def finished_run(ctx, run, created, updated, datatable):
    ctx.github.run(run, created=-created, updated=-updated)
    ctx.github.jobs[run] = jobs_from(datatable)


@given(parsers.parse("a run {run:d} in progress, created {created:d} minutes ago and updated {updated:d} minutes ago, "
                     "with the jobs:"))
def run_in_progress(ctx, run, created, updated, datatable):
    ctx.github.run(run, status="in_progress", created=-created, updated=-updated)
    ctx.github.jobs[run] = jobs_from(datatable)


@given("the poller has made a pass")
def has_made_a_pass(ctx):
    poller(ctx).poll()


@given(parsers.parse("run {run:d} has since finished, updated {updated:d} minutes from now, "
                     "with its job {n:d} done {done:d} minutes from now"))
def run_since_finished(ctx, run, updated, n, done):
    found = next(r for r in ctx.github.runs if r["id"] == run)
    found.update(status="completed", updated_at=at(updated))
    jobs = ctx.github.jobs[run]
    jobs[next(i for i, j in enumerate(jobs) if j["id"] == n)] = job(n, done=done)


@given(parsers.parse("run {run:d} has since been re-run, updated {updated:d} minutes from now, with the new job:"))
def run_rerun(ctx, run, updated, datatable):
    next(r for r in ctx.github.runs if r["id"] == run)["updated_at"] = at(updated)
    ctx.github.jobs[run] += jobs_from(datatable)


@given(parsers.parse("GitHub first answers {status:d} with:"))
def github_first_answers(ctx, status, docstring):
    ctx.github.refuse = [Response(status, docstring.encode())]


def other_pass_saves(store, job_id, move=None):
    """The ten-minute CronJob's pass, saving its state while the backfill runs."""
    theirs = json.loads(store.files[store.head]) if store.head else {"written": {}, "finished": {}}
    theirs["written"][job_id] = at(-1)
    stored = store.upload_file(json.dumps(theirs).encode(), name="written.json")
    (move or store.move_head)("written", stored["id"], if_match=store.head)


@given(parsers.parse("the ten-minute CronJob saves job {job_id} each time the pass writes a file"))
def cronjob_between_files(ctx, job_id):
    store, put = ctx.store, ctx.store.put_buffer

    def cronjob_between(place, data):
        put(place, data)
        other_pass_saves(store, job_id)
    store.put_buffer = cronjob_between


@given("the ten-minute CronJob saves before each move of the head")
def cronjob_before_each_move(ctx):
    store, move = ctx.store, ctx.store.move_head
    ctx.moves = []

    def always_moved(name, hash, *, if_match=None):  # noqa: A002
        ctx.moves.append(hash)
        other_pass_saves(store, f"x{len(ctx.moves)}", move)
        return move(name, hash, if_match=if_match)
    store.move_head = always_moved


@given(parsers.parse("storage refuses to move the head with {status:d}"))
def refuses_to_move(ctx, status):
    from integrations.clients.storage import StorageError

    def forbidden(name, hash, *, if_match=None):  # noqa: A002
        raise StorageError("write", status, "not on the tenant's write list")
    ctx.store.move_head = forbidden


@given("the head holds 90 days of jobs, one each 10 minutes, three times over")
def head_of_90_days(ctx):
    store = ctx.store
    old = State({str(n): (NOW - timedelta(minutes=10 * n)).strftime("%Y-%m-%dT%H:%M:%SZ")
                 for n in range(90 * 144 * 3)})
    assert len(old.encode()) > LIMIT, "too big to store as it is"
    seeded = store.upload_file(json.dumps({"written": {}, "finished": {}}).encode(), name="written.json")
    store.head = seeded["id"]
    store.files[seeded["id"]] = old.encode()  # as a backfill left it, before the limit


@when("the poller makes a pass")
def makes_a_pass(ctx):
    ctx.asked_before = len(ctx.github.asked)
    ctx.attempt(lambda: poller(ctx).poll())


@when(parsers.parse("the poller makes a pass {minutes:d} minutes later"))
def makes_a_pass_later(ctx, minutes):
    ctx.asked_before = len(ctx.github.asked)
    ctx.attempt(lambda: poller(ctx, NOW + timedelta(minutes=minutes)).poll())


@when(parsers.parse("the poller makes a backfill pass of {days:d} days"))
def makes_a_backfill(ctx, days):
    ctx.attempt(lambda: poller(ctx).poll(since_days=days))


@when(parsers.parse("the poller saves the state of job {n}"))
def saves_state(ctx, n):
    ctx.attempt(lambda: poller(ctx).save(State({n: at(0)}), None))


def reader():
    return GitHub(Caller("integrations", "ci-jobs", sink=MemorySink(), transport=FakeGitHub()), lambda: "app-token")


@when("the board is read from release-state's state.json")
def board_is_read(ctx):
    ctx.attempt(lambda: board(json.loads(reader().file("example-org/release-state", "state.json"))))


@when("the installation's repositories are read")
def installation_read(ctx):
    ctx.attempt(lambda: installed(reader()))


@then(parsers.parse("GitHub was asked for no run's jobs since the pass began"))
def no_jobs_read_again(ctx):
    since = ctx.github.asked[ctx.asked_before:]
    assert not any(path.endswith("/jobs") for path in since), f"a finished run was read again: {since}"


@then(parsers.parse("it slept {seconds:d} seconds, once"))
def slept_once(ctx, seconds):
    assert ctx.sleeps == [seconds], f"it slept {ctx.sleeps}"


@then(parsers.parse("the head was asked to move {count:d} times"))
def head_moves(ctx, count):
    assert len(ctx.moves) == count, f"the head was asked to move {len(ctx.moves)} times"


@then("the rows written are:")
def rows_written(ctx, datatable):
    """A row per row the buffer's files hold, in name order; the header names the
    fields, each cell compared as pubtest compares a step's value."""
    got = ctx.store.rows()
    keys, rows = datatable[0], datatable[1:]
    assert len(got) == len(rows), f"{len(got)} rows were written: {show(got)}"
    for item, values in zip(got, rows, strict=True):
        for key, wanted in zip(keys, values, strict=True):
            assert same(dig(item, key), wanted), f"a row's {key} is {dig(item, key)!r}, not {wanted}: {show(item)}"


@then(parsers.parse('the directory "{path}" holds files named:'))
def directory_holds_named(ctx, path, docstring):
    """The names in the directory, in name order, each the pattern on its line."""
    names = sorted(p.name for p in (ctx.directory / path).iterdir())
    patterns = [line.strip() for line in docstring.splitlines() if line.strip()]
    assert len(names) == len(patterns) and all(matches(p, n) for p, n in zip(patterns, names, strict=True)), \
        f"{path} holds {names}"


@then(parsers.parse('the file "{path}" is smaller than {size:d} bytes'))
def smaller_than(ctx, path, size):
    got = (ctx.directory / path).stat().st_size
    assert got < size, f"{path} is {got} bytes"


@then(parsers.parse('every job in "{path}" was written in the {days:d} days before now'))
def written_within(ctx, path, days):
    kept = json.loads((ctx.directory / path).read_text())["written"]
    old = {k: t for k, t in kept.items() if datetime.fromisoformat(t.replace("Z", "+00:00")) < NOW - timedelta(days=days)}
    assert not old, f"{len(old)} older jobs are kept"


# --- the backfill's wait -------------------------------------------------------

SSH = """#!/usr/bin/env bash
# ssh, as it treats its arguments: options aside, the host, then one line for a remote shell.
while [[ $1 == -* ]]; do shift 2; done
shift
exec bash -c "$*"
"""


@given(parsers.parse('an "{name}" that runs what it is sent in a local shell'))
def ssh_runs_locally(ctx, tmp_path, name):
    found = standins(ctx, tmp_path)
    (found.bin / name).write_text(SSH, encoding="utf-8")
    (found.bin / name).chmod(0o755)
    os.environ["PATH"] = found.path()


@then("stderr is empty")
def stderr_is_empty(ctx):
    assert ctx.outcome.stderr == "", ctx.outcome.describe()


# --- the sampler ---------------------------------------------------------------

class FakeKube:
    def __init__(self, answers):
        self.answers = answers

    def get(self, path):
        return self.answers[path]


RUNNER_ID, DIND_ID = "a" * 64, "b" * 64


def kube():
    runner_pod = {"metadata": {"namespace": "ci", "name": "sustenaut-ci-runner-a",
                               "labels": {"actions.github.com/scale-set-name": "sustenaut-ci"}},
                  "spec": {"nodeName": "sustenaut",
                           "containers": [{"name": "runner", "resources": {"requests": {"cpu": "200m", "memory": "512Mi"}}}],
                           "initContainers": [
                               {"name": "init-dind-externals", "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}}},
                               {"name": "dind", "restartPolicy": "Always",
                                "resources": {"requests": {"cpu": "50m", "memory": "256Mi"}}}]},
                  "status": {"phase": "Running", "containerStatuses": [{"name": "runner", "containerID": f"containerd://{RUNNER_ID}"}],
                             "initContainerStatuses": [{"name": "dind", "containerID": f"containerd://{DIND_ID}"}]}}
    app_pod = {"metadata": {"namespace": "auth", "name": "auth-0", "labels": {}},
               "spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "100m", "memory": "1300Mi"}}}]},
               "status": {"phase": "Running"}}
    done_pod = {"metadata": {"namespace": "auth", "name": "old", "labels": {}},
                "spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}]},
                "status": {"phase": "Succeeded"}}
    return FakeKube({
        "/api/v1/nodes": {"items": [{"metadata": {"name": "sustenaut"},
                                     "status": {"allocatable": {"cpu": "4", "memory": "8073416Ki"}}}]},
        "/apis/metrics.k8s.io/v1beta1/nodes": {"items": [{"metadata": {"name": "sustenaut"},
                                                          "usage": {"cpu": "472123456n", "memory": "4915200Ki"}}]},
        "/api/v1/pods": {"items": [runner_pod, app_pod, done_pod]},
        "/apis/metrics.k8s.io/v1beta1/pods": {"items": [
            {"metadata": {"namespace": "ci", "name": "sustenaut-ci-runner-a"},
             "containers": [{"name": "runner", "usage": {"cpu": "150m", "memory": "95Mi"}},
                            {"name": "dind", "usage": {"cpu": "5m", "memory": "30Mi"}}]},
            {"metadata": {"namespace": "auth", "name": "auth-0"},
             "containers": [{"name": "app", "usage": {"cpu": "3m", "memory": "800Mi"}}]}]},
        "/api/v1/namespaces/ci/resourcequotas/ci": {"status": {"hard": {"requests.cpu": "1500m", "requests.memory": "3584Mi"},
                                                               "used": {"requests.cpu": "250m", "requests.memory": "768Mi"}}},
        "/apis/actions.github.com/v1alpha1/namespaces/ci/autoscalingrunnersets": {"items": [{"spec": {"maxRunners": 4}}]},
        "/apis/actions.github.com/v1alpha1/namespaces/ci/ephemeralrunners": {"items": [
            {"status": {"phase": "Running"}}, {"status": {"phase": "Pending"}}]},
    })


@given("a cluster of one node, with a runner pod, an app pod and a finished pod")
def cluster(ctx, tmp_path):
    ctx.kube, ctx.cgroups = kube(), tmp_path / "cgroups"
    ctx.store = FakeStore(ctx.directory, decode_cluster_usage)


@given(parsers.parse("the runner's container peaked at {runner:d} bytes and its dind at {dind:d}"))
def peaks(ctx, runner, dind):
    for cid, peak in ((RUNNER_ID, runner), (DIND_ID, dind)):
        where = ctx.cgroups / "kubepods" / "burstable" / "podx" / cid
        where.mkdir(parents=True)
        (where / "memory.peak").write_text(f"{peak}\n")


@when("the sampler takes a sample")
def takes_a_sample(ctx):
    ctx.attempt(lambda: Sampler(ctx.kube, Cgroups(ctx.cgroups), ctx.store, clock=lambda: NOW,
                                report=lambda _: None).sample())


@when(parsers.parse('the CPU quantity "{quantity}" is read'))
def cpu_quantity(ctx, quantity):
    ctx.attempt(lambda: millicores(quantity))


@when(parsers.parse('the memory quantity "{quantity}" is read'))
def memory_quantity(ctx, quantity):
    ctx.attempt(lambda: octets(quantity))


@when(parsers.parse("no {kind} quantity is read"))
def no_quantity(ctx, kind):
    ctx.attempt(lambda: (millicores if kind == "CPU" else octets)(None))


@when("the memory peak of a container with no cgroup is read")
def peak_without_cgroup(ctx, tmp_path):
    ctx.attempt(lambda: Cgroups(tmp_path / "none").peak("containerd://" + "c" * 64))


@when("the memory peak of no container is read")
def peak_of_none(ctx, tmp_path):
    ctx.attempt(lambda: Cgroups(tmp_path).peak(None))


@when("two CI job rows are encoded, as they are and as copies, and read back")
def ci_rows_encoded(ctx):
    rows = [row(REPO, {"event": "push"}, job(1), NOW), row(REPO, {"event": "push"}, job(2, runner=None, started=None), NOW)]
    ctx.attempt(lambda: {"same bytes": encode_ci_jobs(rows) == encode_ci_jobs([dict(r) for r in rows]),
                         "read back as they were": decode_ci_jobs(encode_ci_jobs(rows)) == rows})


@when("a cluster usage row is encoded, as it is and as a copy, and read back")
def usage_encoded(ctx):
    usage = [{"observed_at": NOW, "kind": "node", "node": "n", "cpu_millicores": 5}]
    ctx.attempt(lambda: {"same bytes": encode_cluster_usage(usage) == encode_cluster_usage([dict(u) for u in usage]),
                         "read back": decode_cluster_usage(encode_cluster_usage(usage))})
