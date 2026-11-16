"""CI's wait times and compute load: the ci-jobs poller and the cluster sampler."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_bdd import scenarios, then

from integrations.avrofile import decode_ci_jobs, decode_cluster_usage, encode_ci_jobs, encode_cluster_usage
from integrations.calls import Caller, MemorySink, Refused, Response
from integrations.ci_usage.cluster import Cgroups, Sampler, millicores, octets
from integrations.ci_usage.jobs import GitHub, Poller, board, installed

scenarios("ci-usage.feature")

NOW = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
REPO = "example-org/app"


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
    """The ci-jobs tenant's head and the ci-usage tenant's buffer."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.head: str | None = None
        self.buffer: dict[str, bytes] = {}
        self.moves = 0

    def get_head(self, name):
        from integrations.clients.storage import NotFound

        if self.head is None:
            raise NotFound("read", 404, "no head")
        return self.files[self.head], self.head

    def upload_file(self, data, *, name):
        key = f"h{len(self.files)}"
        self.files[key] = data
        return {"id": key}

    def move_head(self, name, hash, *, if_match=None):  # noqa: A002
        from integrations.clients.storage import StorageError

        if if_match != self.head:
            raise StorageError("write", 412, "written moved just now")
        self.head = hash
        self.moves += 1
        return {"hash": hash}

    def put_buffer(self, place, data):
        self.buffer[place] = data
        return {}

    def rows(self):
        return [r for name in sorted(self.buffer) for r in decode_ci_jobs(self.buffer[name])]


def poller(github, store, *, now=NOW, sleeps=None):
    caller = Caller("integrations", "ci-jobs", sink=MemorySink(), transport=github, sleep=lambda _: None)
    gh = GitHub(caller, lambda: "app-token", sleep=(sleeps.append if sleeps is not None else lambda _: None))
    return Poller(gh, store, store, [REPO], clock=lambda: now, report=lambda _: None)


@then("the poller writes each finished job once, with its wait, and saves what it wrote")
def the_poller_writes_each_finished_job_once():
    github, store = FakeGitHub(), FakeStore()
    github.run()
    github.jobs[7] = [job(1), job(2, queued=-60, started=-25, done=-12, labels=["ubuntu-latest"],
                                 runner="GitHub Actions 3", runner_group_name="GitHub Actions")]
    assert poller(github, store).poll() == 2
    rows = {r["job_id"]: r for r in store.rows()}
    assert rows[1]["wait_seconds"] == 600 and rows[1]["run_seconds"] == 600 and rows[1]["runner_group"] == "private"
    assert rows[2]["wait_seconds"] == 2100 and rows[2]["labels"] == ["ubuntu-latest"]
    assert rows[1]["repository"] == REPO and rows[1]["event"] == "pull_request" and rows[1]["head_branch"] == "main"
    assert all(name.startswith("inbox/ci-jobs/2027-01-15/120000-ci-jobs-20270115-") for name in store.buffer)
    saved = json.loads(store.files[store.head])
    assert sorted(saved["written"]) == ["1", "2"] and list(saved["finished"]) == ["7"]


@then("a second pass writes nothing and saves nothing")
def a_second_pass_writes_nothing_and_saves_nothing():
    github, store = FakeGitHub(), FakeStore()
    github.run()
    github.jobs[7] = [job(1)]
    poller(github, store).poll()
    moves, asked = store.moves, len(github.asked)
    assert poller(github, store, now=NOW + timedelta(minutes=10)).poll() == 0
    assert store.moves == moves and len(store.buffer) == 1
    assert not any(path.endswith("/jobs") for path in github.asked[asked:]), "a finished run isn't read again"


@then("a job still running is written once it completes")
def a_job_still_running_is_written_once_it_completes():
    github, store = FakeGitHub(), FakeStore()
    github.run(status="in_progress")
    github.jobs[7] = [job(1), job(2, status="in_progress", done=None)]
    assert poller(github, store).poll() == 1
    assert json.loads(store.files[store.head])["finished"] == {}
    github.runs[0].update(status="completed", updated_at=at(5))
    github.jobs[7][1] = job(2, done=4)
    assert poller(github, store, now=NOW + timedelta(minutes=10)).poll() == 1
    assert sorted(r["job_id"] for r in store.rows()) == [1, 2]


@then("a job no runner took has no wait")
def a_job_no_runner_took_has_no_wait():
    github, store = FakeGitHub(), FakeStore()
    github.run()
    github.jobs[7] = [job(1, runner=None, started=None, conclusion="cancelled")]
    poller(github, store).poll()
    (only,) = store.rows()
    assert only["wait_seconds"] is None and only["started_at"] is None and only["runner_name"] is None
    assert only["conclusion"] == "cancelled"


@then("a rate limit is waited out")
def a_rate_limit_is_waited_out():
    github, store, sleeps = FakeGitHub(), FakeStore(), []
    github.run()
    github.jobs[7] = [job(1)]
    github.refuse = [Response(403, b'{"message": "API rate limit exceeded for installation"}')]
    assert poller(github, store, sleeps=sleeps).poll() == 1
    assert sleeps == [60]


@then("a refusal that is no rate limit stops the pass before anything is saved")
def a_refusal_that_is_no_rate_limit_stops_the_pass():
    github, store = FakeGitHub(), FakeStore()
    github.refuse = [Response(404, b'{"message": "Not Found"}')]
    with pytest.raises(Refused, match="HTTP 404"):
        poller(github, store).poll()
    assert store.head is None and not store.buffer


@then("a backfill writes a file for each day of runs")
def a_backfill_writes_a_file_for_each_day_of_runs():
    github, store = FakeGitHub(), FakeStore()
    for n, days_ago in ((7, 0), (8, 2), (9, 40)):
        created = -days_ago * 24 * 60 - 40
        github.run(n, created=created, updated=created + 30)
        github.jobs[n] = [job(n, queued=created + 1, started=created + 2, done=created + 20)]
    assert poller(github, store).poll(since_days=90) == 3
    days = sorted(name.split("/")[3].split("-")[3] for name in store.buffer)
    assert days == ["20261206", "20270113", "20270115"]
    assert sorted(json.loads(store.files[store.head])["written"]) == ["7", "8"], "only the look back's are kept"


@then("the board's repositories come from state.json")
def the_boards_repositories_come_from_state_json():
    github = FakeGitHub()
    caller = Caller("integrations", "ci-jobs", sink=MemorySink(), transport=github)
    state = json.loads(GitHub(caller, lambda: "app-token").file("example-org/release-state", "state.json"))
    assert board(state) == ["example-org/app"]


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


@then("the sampler writes node, namespace, ci and runner rows")
def the_sampler_writes_node_namespace_ci_and_runner_rows(tmp_path):
    for cid, peak in ((RUNNER_ID, 734003200), (DIND_ID, 52428800)):
        where = tmp_path / "kubepods" / "burstable" / "podx" / cid
        where.mkdir(parents=True)
        (where / "memory.peak").write_text(f"{peak}\n")
    store = FakeStore()
    sampler = Sampler(kube(), Cgroups(tmp_path), store, clock=lambda: NOW, report=lambda _: None)
    assert sampler.sample() == 5
    (name,) = store.buffer
    assert name.startswith("inbox/cluster-usage/2027-01-15/120000-usage-")
    rows = {(r["kind"], r["namespace"]): r for r in decode_cluster_usage(store.buffer[name])}
    node = rows[("node", None)]
    assert node["cpu_millicores"] == 472 and node["cpu_capacity_millicores"] == 4000
    assert node["memory_capacity_bytes"] == 8073416 * 1024
    auth = rows[("namespace", "auth")]
    assert auth["memory_requests_bytes"] == 1300 * 2**20 and auth["cpu_requests_millicores"] == 100, "a finished pod asks nothing"
    assert rows[("namespace", "ci")]["cpu_requests_millicores"] == 250 and rows[("namespace", "ci")]["memory_bytes"] == 125 * 2**20
    ci = rows[("ci", "ci")]
    assert (ci["quota_memory_used"], ci["quota_memory_hard"]) == (768 * 2**20, 3584 * 2**20)
    assert (ci["runners_running"], ci["runners_pending"], ci["runners_max"]) == (1, 1, 4)
    runner = rows[("runner", "ci")]
    assert runner["runner_name"] == "sustenaut-ci-runner-a" and runner["node"] == "sustenaut"
    assert (runner["runner_memory_peak_bytes"], runner["dind_memory_peak_bytes"]) == (734003200, 52428800)


@then("kubernetes quantities read as millicores and bytes")
def kubernetes_quantities_read_as_millicores_and_bytes(tmp_path):
    assert [millicores(q) for q in ("2", "250m", "1.5", "123456789n", "3600u", None)] == [2000, 250, 1500, 123, 4, None]
    assert [octets(q) for q in ("512Mi", "1Gi", "123Ki", "1e3", "2G", "100", None)] == [
        512 * 2**20, 2**30, 123 * 1024, 1000, 2 * 10**9, 100, None]
    assert Cgroups(tmp_path / "none").peak("containerd://" + "c" * 64) is None
    assert Cgroups(tmp_path).peak(None) is None


@then("ci jobs and cluster usage are the same bytes for the same rows")
def ci_jobs_and_cluster_usage_are_the_same_bytes_for_the_same_rows():
    from integrations.ci_usage.jobs import row

    rows = [row(REPO, {"event": "push"}, job(1), NOW), row(REPO, {"event": "push"}, job(2, runner=None, started=None), NOW)]
    assert encode_ci_jobs(rows) == encode_ci_jobs([dict(r) for r in rows])
    assert decode_ci_jobs(encode_ci_jobs(rows)) == rows
    usage = [{"observed_at": NOW, "kind": "node", "node": "n", "cpu_millicores": 5}]
    decoded = decode_cluster_usage(encode_cluster_usage(usage))
    assert decoded[0]["cpu_millicores"] == 5 and decoded[0]["quota_cpu_hard"] is None
    assert encode_cluster_usage(usage) == encode_cluster_usage([dict(u) for u in usage])


@then("the poller reads every repository the installation sees, archived ones aside")
def the_poller_reads_every_repository_the_installation_sees():
    caller = Caller("integrations", "ci-jobs", sink=MemorySink(), transport=FakeGitHub())
    assert installed(GitHub(caller, lambda: "app-token")) == ["example-org/app", "example-org/infra"]


def other_pass_saves(store, job_id, move=None):
    """The ten-minute CronJob's pass, saving its state while the backfill runs."""
    theirs = json.loads(store.files[store.head]) if store.head else {"written": {}, "finished": {}}
    theirs["written"][job_id] = at(-1)
    stored = store.upload_file(json.dumps(theirs).encode(), name="written.json")
    (move or store.move_head)("written", stored["id"], if_match=store.head)


@then("a save the CronJob got in first is merged with it and tried again")
def a_save_the_cronjob_got_in_first_is_merged_with_it():
    github, store = FakeGitHub(), FakeStore()
    for n, days_ago in ((7, 0), (8, 1)):
        created = -days_ago * 24 * 60 - 40
        github.run(n, created=created, updated=created + 30)
        github.jobs[n] = [job(n, queued=created + 1, started=created + 2, done=created + 20)]
    put = store.put_buffer

    def cronjob_between(place, data):
        put(place, data)
        other_pass_saves(store, "99")
    store.put_buffer = cronjob_between
    assert poller(github, store).poll() == 2
    assert sorted(json.loads(store.files[store.head])["written"]) == ["7", "8", "99"], "both passes' jobs stay written"


@then("a save refused every time stops after a few tries")
def a_save_refused_every_time_stops_after_a_few_tries():
    from integrations.ci_usage.jobs import SAVE_TRIES, State
    from integrations.clients.storage import StorageError

    store, moves = FakeStore(), []
    move = store.move_head

    def always_moved(name, hash, *, if_match=None):  # noqa: A002
        moves.append(hash)
        other_pass_saves(store, f"x{len(moves)}", move)
        return move(name, hash, if_match=if_match)
    store.move_head = always_moved
    with pytest.raises(StorageError, match="412"):
        poller(FakeGitHub(), store).save(State({"1": at(0)}), None)
    assert len(moves) == SAVE_TRIES


@then("a refusal that is no conflict is not tried again")
def a_refusal_that_is_no_conflict_is_not_tried_again():
    from integrations.ci_usage.jobs import State
    from integrations.clients.storage import StorageError

    store = FakeStore()

    def forbidden(name, hash, *, if_match=None):  # noqa: A002
        raise StorageError("write", 403, "not on the tenant's write list")
    store.move_head = forbidden
    with pytest.raises(StorageError, match="403"):
        poller(FakeGitHub(), store).save(State({"1": at(0)}), None)
    assert len(store.files) == 1


def wait_for_job(tmp_path, conditions: list[str]):
    """Runs scripts/wait-for-job.sh with an ssh that answers each look with the next of
    `conditions`, and the log once asked."""
    import os
    import subprocess
    from pathlib import Path

    answers = tmp_path / "answers"
    answers.write_text("\n".join(conditions) + "\n")
    ssh = tmp_path / "ssh"
    ssh.write_text("""#!/usr/bin/env bash
case "$*" in
  *" logs "*) printf '{"call":"record"}\\nsaved 40 jobs\\nstorage.api write: HTTP 412 written moved just now\\n' ;;
  *) head -n 1 "$ANSWERS"; sed -i 1d "$ANSWERS" ;;
esac
""")
    ssh.chmod(0o755)
    script = Path(__file__).resolve().parents[2] / "scripts" / "wait-for-job.sh"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "ANSWERS": str(answers), "WAIT_SECONDS": "0"}
    return subprocess.run([str(script), "integrations", "ci-jobs-backfill"], env=env, capture_output=True, text=True,
                          timeout=30)


@then("the backfill's wait ends when the Job completes")
def the_backfills_wait_ends_when_the_job_completes(tmp_path):
    ran = wait_for_job(tmp_path, ["", "", "Complete "])
    assert ran.returncode == 0 and not ran.stderr


@then("the backfill's wait fails with the end of its log when the Job fails")
def the_backfills_wait_fails_when_the_job_fails(tmp_path):
    ran = wait_for_job(tmp_path, ["", "Failed "])
    assert ran.returncode == 1
    assert "Job ci-jobs-backfill failed" in ran.stderr and "HTTP 412" in ran.stderr
    assert '{"call"' not in ran.stderr, "call records stay out"
