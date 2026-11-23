"""The scanner: files in storage reach BigQuery once and leave, whatever stops partway.

Storage and BigQuery are in-memory fakes (FakeBuffer, PickyBigQuery): a scanner
lists, reads, writes and deletes, and BigQuery loads and reports jobs, so each
is state a Given sets up, not a list of recorded answers. A pass leaves, as its
result, what the world then holds (`seen`), which pubtest's result steps check.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from integrations.avrofile import (decode_need_states,
                                   decode_pubrules_checks, decode_signals, encode, encode_ci_jobs,
                                   encode_cluster_usage, encode_need_states, encode_pubrules_checks, encode_signals)
from integrations.calls import Caller, MemorySink
from integrations.ingest.bigquery import JOBS_PER_TABLE_PER_DAY, STATUS_TABLE, BigQueryLoader, JobBudget
from integrations.ingest.inbox import KINDS, Inbox, Scanner, Waiting
from integrations.ingest.main import buffers, named_buffers, need_states, pubrules_checks, scan, scanned, seconds, signals
from pubtest.core import dig, show
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import providers
from .stores import BASE, FakeBuffer, PickyBigQuery, record

scenarios("inbox.feature")

AVRO = b"Obj\x01"
MOMENT = datetime(2027, 1, 15, 7, 50, tzinfo=timezone.utc)
CHECKED = datetime(2027, 1, 15, 2, 41, tzinfo=timezone.utc)


def report(sender="web-1", calls=1, **fields):
    return {"kind": "sender", "sender": sender, "version": "1.0", "at": "2027-01-15T07:50:00Z",
            "cycle_start": "2027-01-15T07:48:00Z", "cycle_end": "2027-01-15T07:50:00Z", "calls": calls, "drops": 0,
            "buffer_size": 1, "storage_failures": 0, **fields}


def signal(n: int, **fields) -> dict:
    return {"rule_id": "drops", "rule_version": "1", "subject": f"example/app{n}", "severity": "warning",
            "started_at": MOMENT, "ended_at": None, "evidence": '{"dropped": 3}', "emitted_at": MOMENT, **fields}


def need_state(n: int, **fields) -> dict:
    return {"observed_at": MOMENT, "repository": "example-org/app", "unit": "app", "need": f"k8s.secret:s{n}",
            "type": "k8s.secret", "name": f"s{n}", "grade": "auto", "state": "ready", "walked_from": "plane",
            "walk_started_at": MOMENT, **fields}


def pubrules_check(n: int, **fields) -> dict:
    return {"observed_at": CHECKED, "repository": f"example-org/repo{n}", "check": "pin", "status": "pass",
            "pinned": "0.37.0", "latest": "0.37.0", "main_commit": "abc123", "run_url": "", "fix": "",
            "run_started_at": CHECKED, **fields}


def ci_job() -> bytes:
    from integrations.ci_usage.jobs import row  # noqa: PLC0415
    job = {"id": 1, "run_id": 2, "name": "test", "created_at": "2027-01-15T02:30:00Z",
           "started_at": "2027-01-15T02:35:00Z", "completed_at": "2027-01-15T02:40:00Z", "runner_name": "r"}
    return encode_ci_jobs([row("o/r", {"event": "push"}, job, CHECKED)])


# What a file in storage may hold, by the words a Given uses for it.
CONTENTS = {
    "call record 1": lambda: encode([record(1)]),
    "no records": lambda: encode([]),
    "no records and an idle sender's report": lambda: encode([], report(calls=0)),
    "no Avro": lambda: AVRO + b"junk",
    "signal 1": lambda: encode_signals([signal(1)]),
    "signal 2": lambda: encode_signals([signal(2)]),
    "need state 1 ready and need state 2 missing": lambda: encode_need_states(
        [need_state(1), need_state(2, state="missing")]),
    "pubrules check 1 passing and pubrules check 2 failing": lambda: encode_pubrules_checks(
        [pubrules_check(1), pubrules_check(2, status="fail", pinned="0.35.1",
                                          fix="`git latest && ./pubrules bump`, then open its PR")]),
    "a CI job that waited 5 minutes": ci_job,
    "a node using 400 millicores": lambda: encode_cluster_usage(
        [{"observed_at": CHECKED, "kind": "node", "node": "n", "cpu_millicores": 400}]),
}

# The rows a scenario writes twice and reads back, by their words.
ROWS = {
    "signals": (encode_signals, decode_signals,
                lambda: [signal(1), signal(2, ended_at=datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc))]),
    "need states": (encode_need_states, decode_need_states, lambda: [need_state(1), need_state(2, state="drift")]),
    "pubrules checks": (encode_pubrules_checks, decode_pubrules_checks,
                        lambda: [pubrules_check(1), pubrules_check(2, check="sync", status="fail")]),
}

VARIABLES = {"INGEST_SIGNALS": signals, "INGEST_NEED_STATES": need_states,
             "INGEST_PUBRULES_CHECKS": pubrules_checks, "INGEST_BUFFERS": buffers}



def world(ctx, buffers_to_scan, tenants=None, kinds=None):
    ctx.buffer, ctx.bigquery, ctx.statuses = FakeBuffer(), PickyBigQuery(), []
    ctx.buffers = buffers_to_scan
    ctx.put, ctx.own_before = {}, []
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=ctx.bigquery, sleep=lambda _: None)
    ctx.now = [1_800_000_000.0]  # a moment after the files' upload time, which "seconds pass" moves on
    clock = lambda: ctx.now[0]  # noqa: E731
    ctx.scanner = Scanner(Inbox(ctx.buffer, lambda: "pod-token", BASE, tenants),
                          BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                          JobBudget(clock), ctx.statuses.append, clock, kinds=kinds or {})


def seen(ctx) -> dict:
    """What storage, BigQuery and the scanner's reports hold now."""
    buffer, bigquery = ctx.buffer, ctx.bigquery
    batches, own_files = buffer.under("batches/"), sorted(n for n in buffer.files if "-ingest-" in n)
    dataset = ctx.buffers[-1].partition(".")[0]
    own = ctx.scanner.pending.get(ctx.buffers[-1], [])
    return {
        "loaded": sorted(r["request_id"] for r in bigquery.rows),
        "posts": bigquery.posts,
        "calls jobs": len(bigquery.call_jobs),
        "status jobs": len(bigquery.status_jobs),
        "status rows": bigquery.status_rows,
        "status kinds": [r["kind"] for r in bigquery.status_rows],
        "senders": [r["sender"] for r in bigquery.status_rows if r["kind"] == "sender"],
        "tables": bigquery.tables,
        "datasets": bigquery.datasets,
        "signals": sorted(r["subject"] for r in bigquery.rows_of["signals"]),
        "signal evidence": [r["evidence"] for r in bigquery.rows_of["signals"]],
        "need states": sorted([r["need"], r["state"]] for r in bigquery.rows_of["need_states"]),
        "pubrules checks": sorted([r["repository"], r["status"]] for r in bigquery.rows_of["pubrules_checks"]),
        "CI job waits": [r["wait_seconds"] for r in bigquery.rows_of["ci_jobs"]],
        "cluster millicores": [r["cpu_millicores"] for r in bigquery.rows_of["cluster_usage"]],
        "files": sorted(buffer.files),
        "inbox": buffer.inbox(),
        "batches": batches,
        "batch count": len(batches),
        "batch": json.loads(buffer.files[batches[0]]) if batches else None,
        "rejected": buffer.under("rejected/"),
        "rejected as they came": [n for n in buffer.under("rejected/")
                                  if ctx.put.get(n.replace("rejected/", "inbox/", 1)) == buffer.files[n]],
        "left": buffer.left(),
        "own report files": own_files,
        "own report count": len(own),
        "own report": own[0] if own else None,
        "own report fields": sorted(own[0]) if own else None,
        "storage folders asked": sorted({path.split("?")[0].split("/")[0] for _, path in buffer.sent}),
        "lists": sum(path == "list" for _, path in buffer.sent),
        "queued": sorted(ctx.scanner.queued.get(ctx.buffers[-1], {})),
        "report count": len(ctx.statuses),
        "reports": ctx.statuses,
        "reported": sorted({f"{s['dataset']}.{s['table']}" if "table" in s else s["dataset"] for s in ctx.statuses}),
        "jobs used": ctx.scanner.jobs.used(dataset),
        "status jobs used": ctx.scanner.jobs.used(dataset, STATUS_TABLE),
    }


# --- the world --------------------------------------------------------------------------------------------------

@given(parsers.parse('a scanner of the calls of "{dataset}"'))
def calls_scanner(ctx, dataset):
    world(ctx, [dataset])


@given(parsers.parse('a scanner of the {table} of "{dataset}", in the tenant "{tenant}"'))
def kind_scanner(ctx, table, dataset, tenant):
    world(ctx, [dataset], {dataset: tenant}, {dataset: KINDS[table.replace(" ", "_")]})


@given(parsers.parse("a scanner of the buffers INGEST_PUBRULES_CHECKS and INGEST_BUFFERS name:"))
def named_scanner(ctx, datatable):
    given_ = dict(datatable)
    tenants, kinds = named_buffers("", "", given_.get("INGEST_PUBRULES_CHECKS", ""), given_.get("INGEST_BUFFERS", ""))
    world(ctx, list(tenants), tenants, kinds)


def numbers(text: str) -> list[int]:
    found = re.fullmatch(r"(\d+) to (\d+)", text)
    if found:
        return list(range(int(found[1]), int(found[2]) + 1))
    return [int(n) for n in re.split(r", | and ", text)]


def waiting(ctx, n, status=None, **fields):
    name = f"inbox/calls/2027-01-15/0750{n:02d}-web-{n}.avro"
    ctx.buffer.files[name] = ctx.put[name] = encode([record(n, **fields)], status)
    return name


@given(parsers.re(r"calls files? (?P<which>[\d, and to]+) (?:is|are) waiting"))
def calls_waiting(ctx, which):
    for n in numbers(which):
        waiting(ctx, n)


@when(parsers.re(r"calls file (?P<n>\d+) arrives"))
def calls_arrive(ctx, n):
    waiting(ctx, int(n))


@given(parsers.parse("calls file {n:d} is waiting with a bad record"))
def bad_waiting(ctx, n):
    waiting(ctx, n, operation="bad")


@given(parsers.parse('calls file {n:d} is waiting with a report from "{sender}"'))
@when(parsers.parse('calls file {n:d} arrives with a report from "{sender}"'))
def reported_waiting(ctx, n, sender):
    waiting(ctx, n, report(sender=sender))


@given(parsers.parse("calls file {n:d} is waiting with a report of {calls:d} call and {drops:d} drops"))
def counted_waiting(ctx, n, calls, drops):
    waiting(ctx, n, report(calls=calls, drops=drops))


@given(parsers.parse("calls file {n:d} is waiting with a report that is not one"))
def unreported_waiting(ctx, n):
    waiting(ctx, n, {"kind": "sender"})


@given(parsers.parse('storage holds "{path}", with {what}'))
def storage_holds(ctx, path, what):
    ctx.buffer.files[path] = CONTENTS[what]()


@given(parsers.parse('storage holds the batch record "{path}":'))
def batch_record(ctx, path, docstring):
    ctx.buffer.files[path] = json.dumps(json.loads(docstring)).encode()


@given(parsers.parse('storage does not answer a "{method}"'))
def storage_silent(ctx, method):
    ctx.buffer.down_for.add(method)


@when("storage answers again")
def storage_answers(ctx):
    ctx.buffer.down_for = set()


@when(parsers.parse('"{path}" is gone from storage'))
def gone(ctx, path):
    del ctx.buffer.files[path]


@given("BigQuery leaves its load jobs running")
def jobs_running(ctx):
    ctx.bigquery.answer = "running"


@given("BigQuery is busy")
def bigquery_busy(ctx):
    ctx.bigquery.answer = "busy"


@given("BigQuery's next calls job fails in a way that may pass")
def calls_job_fails(ctx):
    ctx.bigquery.fail_jobs = 1


@given("BigQuery's next report job fails in a way that may pass")
def report_job_fails(ctx):
    ctx.bigquery.fail_status = 1


@given("BigQuery rejects the reports")
def reports_rejected(ctx):
    ctx.bigquery.refuse_status = True


@given("the day's load jobs of the calls are used")
def calls_jobs_used(ctx):
    for _ in range(JOBS_PER_TABLE_PER_DAY):
        ctx.scanner.jobs.spend(ctx.buffers[0])


@given("the day's load jobs of the status table are used")
def status_jobs_used(ctx):
    for _ in range(JOBS_PER_TABLE_PER_DAY):
        ctx.scanner.jobs.spend(ctx.buffers[0], STATUS_TABLE)


@given("call records can't be encoded")
def no_encoding(monkeypatch):
    monkeypatch.setattr("integrations.ingest.inbox.encode",
                        lambda rows, status=None: (_ for _ in ()).throw(ValueError("no")))


@given(parsers.parse('the work on "{dataset}" fails with "{message}"'))
def work_fails(ctx, dataset, message):
    real = ctx.scanner._work

    def work(name, status, problems, done):
        if name == dataset:
            raise RuntimeError(message)
        real(name, status, problems, done)

    ctx.scanner._work = work


@given(parsers.parse("{seconds:d} seconds pass"))
@when(parsers.parse("{seconds:d} seconds pass"))
def seconds_pass(ctx, seconds):
    ctx.now[0] += seconds


@given(parsers.parse("storage tells of calls file {n:d}"))
@when(parsers.parse("storage tells of calls file {n:d}"))
def told_of(ctx, n):
    name = f"inbox/calls/2027-01-15/0750{n:02d}-web-{n}.avro"
    ctx.scanner.notified(ctx.buffers[-1], name, len(ctx.buffer.files.get(name, b"")))


# --- the actions ------------------------------------------------------------------------------------------------

def passes(ctx, keys=None):
    ctx.own_before = sorted(n for n in ctx.buffer.files if "-ingest-" in n)
    ctx.attempt(lambda: (ctx.scanner.tick(keys or ctx.buffers), seen(ctx))[1])


@when("the scanner passes")
def scanner_passes(ctx):
    passes(ctx)


@when("the scanner takes what it was told of")
def scanner_drains(ctx):
    ctx.attempt(lambda: (ctx.scanner.drain(), seen(ctx))[1])


@when(parsers.parse('the buffers scanned for INGEST_DATASETS {datasets} and the named ones of INGEST_BUFFERS {named}'))
def buffers_scanned(ctx, datasets, named):
    def found():
        tenants, kinds = buffers(named)
        return {key: [tenant, kind.folder] for key, (tenant, kind) in scanned(json.loads(datasets), tenants, kinds).items()}
    ctx.attempt(found)


@when("the scanner passes again")
def scanner_passes_again(ctx):
    passes(ctx)


@when(parsers.parse('the scanner passes over "{keys}"'))
def scanner_passes_over(ctx, keys):
    passes(ctx, [k.strip() for k in keys.split(",")])


@when("I ask whether the calls may load")
def may_load(ctx):
    ctx.attempt(lambda: ctx.scanner.jobs.may_load(ctx.buffers[0]))


@when(parsers.parse("the scanner chooses a batch from {n:d} files of {size:d} byte"))
def choose_many(ctx, n, size):
    scanner = Scanner(None, None, None, None)
    ctx.attempt(lambda: scanner._choose([Waiting(f"f{i}", size, 0.0) for i in range(n)]))


@when("the scanner chooses a batch from:")
def choose_from(ctx, datatable):
    scanner = Scanner(None, None, None, None)
    ctx.attempt(lambda: scanner._choose([Waiting(name, int(size), 0.0) for name, size in datatable[1:]]))


@when(parsers.parse('the inbox writes "{name}" for "{dataset}"'))
def inbox_writes(ctx, name, dataset):
    ctx.attempt(lambda: Inbox(providers(ctx), lambda: "pod-token", BASE).put(dataset, name, AVRO))


def kinds_by_table(found):
    tenants, kinds = found
    return {"tenants": tenants, "kinds": {key: kind.table for key, kind in kinds.items()}}


@when(parsers.re(r'(?P<variable>INGEST_[A-Z_]+) is "(?P<text>.*)"'))
def variable_is(ctx, variable, text):
    if variable.endswith("_SECONDS"):
        ctx.attempt(lambda: seconds(variable, text, 120))
    else:
        ctx.attempt(lambda: kinds_by_table(VARIABLES[variable](text)))


@when("the ingest buffers are read from:")
def buffers_read(ctx, datatable):
    given_ = dict(datatable)
    ctx.attempt(lambda: kinds_by_table(named_buffers(
        given_.get("INGEST_SIGNALS", ""), given_.get("INGEST_NEED_STATES", ""),
        given_.get("INGEST_PUBRULES_CHECKS", ""), given_.get("INGEST_BUFFERS", ""))))


@when(parsers.parse("INGEST_BUFFERS names the ci_jobs buffer of platform twice"))
def buffers_twice(ctx):
    given_ = [{"tenant": "ci-usage", "dataset": "platform", "table": "ci_jobs"},
              {"tenant": "ci-usage", "dataset": "platform", "table": "cluster_usage"}]
    ctx.attempt(lambda: kinds_by_table(buffers(json.dumps(given_ + given_[:1]))))


@when(parsers.parse("{what} are written twice and read back"))
def written_twice(ctx, what):
    write, read, rows = ROWS[what]
    ctx.attempt(lambda: {"the same bytes": write(rows()) == write([dict(r) for r in rows()]),
                         "read back unchanged": read(write(rows())) == rows()})


@when(parsers.parse('the scan runs over "{keys}", sweeping every {sweep:g} seconds, for {sleeps:d} sleeps from the moment {moment:g}'))
def scan_runs(ctx, keys, sweep, sleeps, moment):
    events: list[str] = []
    ticked = [0.0]

    class Stop(Exception):
        pass

    clock = [moment]

    class Ticking:
        def tick(self, buffers_):
            events.append(f"tick {','.join(buffers_)}")

        def drain(self):
            events.append("drain")

    def sleep(seconds):
        events.append(f"sleep {seconds:g}")
        clock[0] += seconds
        if sum(e.startswith("sleep") for e in events) == sleeps:
            raise Stop

    def run():
        try:
            scan(Ticking(), keys.split(","), ticked, sleep=sleep, clock=lambda: clock[0], sweep_seconds=sweep)
        except Stop:
            return {"events": events, "ticked": ticked}
        raise AssertionError("the scan stopped by itself")

    ctx.attempt(run)


# --- checks pubtest has no step for ----------------------------------------------------------------------------

@then(parsers.re(r'the result\'s "(?P<key>[^"]+)" includes "(?P<text>[^"]*)"'))
def result_key_includes(ctx, key, text):
    assert ctx.refused is None, f"it was refused: {ctx.refused.message!r}"
    got = dig(ctx.result, key)
    assert got is not None and text in got, f"the result's {key} is {show(got)}, which does not include {text!r}"


@then(parsers.parse('the refusal is a "{name}"'))
def refusal_is_a(ctx, name):
    ctx.refusal_read = True
    assert ctx.refused is not None, "it was not refused"
    kinds = [k.__name__ for k in type(ctx.refused.error).__mro__]
    assert name in kinds, f"the refusal is a {type(ctx.refused.error).__name__}"
