"""A sender's files: one every cycle, even with nothing in it, and nothing lost or doubled.
Storage is the in-memory buffer of stores.py (FakeBuffer), which lists, takes and gives
files as the storage service does, and BigQuery its PickyBigQuery."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from integrations import __version__
from integrations.avrofile import decode, file_status
from integrations.calls import CallRecord, Caller, MemorySink, Response
from integrations.ingest.bigquery import BigQueryLoader, JobBudget
from integrations.ingest.inbox import Inbox, Scanner
from integrations.storage_sink import StorageSink, first_wait
from pytest_bdd import given, parsers, scenarios, then, when

from .stores import BASE, FakeBuffer, PickyBigQuery, record

scenarios("storage-sink.feature")


class Buffer(FakeBuffer):
    """The buffer, keeping the tenants it was asked for too."""

    def __init__(self):
        super().__init__()
        self.tenants: set[str] = set()

    def send(self, request, timeout):
        self.tenants.add(urlparse(request.url).path.lstrip("/").partition("/")[0])
        return super().send(request, timeout)


def view(ctx) -> dict:
    """What a sink's action came to: the buffer's inbox, and the sink's own state."""
    buffer, sink = ctx.buffer, ctx.sink
    names = buffer.under("inbox/")
    files = [{"name": n, "request_ids": [r["request_id"] for r in decode(buffer.files[n])],
              "report": file_status(buffer.files[n])} for n in names]
    return {"count": len(files), "files": files, "inbox request_ids": [i for f in files for i in f["request_ids"]],
            "held": sorted(sink.held), "failures": dict(sink.failures),
            "waiting": {k: len(v) for k, v in sink.waiting.items()}, "tenants": sorted(buffer.tenants)}


def act(ctx, action) -> None:
    """One step of the action: an earlier step's refusal no Then could read fails it."""
    unread = ctx.unread_refusal()
    if unread is not None:
        raise AssertionError(f"an earlier step of the action was refused: {unread!r}") from unread

    def done():
        action()
        return view(ctx)
    ctx.attempt(done)


def sink(ctx, datasets, sender, **options):
    ctx.buffer = Buffer()
    ctx.sink = StorageSink(datasets, transport=ctx.buffer, token=lambda: "pod-token", base_url=BASE, sender=sender,
                           clock=lambda: ctx.now, start=False, **options)


@given(parsers.parse("the clock reads {moment}"))
def clock_reads(ctx, moment):
    ctx.now = datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp()


@given(parsers.parse('the package\'s version is "{version}"'))
def package_version(monkeypatch, version):
    import integrations.storage_sink as storage_sink
    monkeypatch.setattr(storage_sink, "__version__", version)


@given(parsers.parse('a sink for the sender "{sender}" sending "{one}" and "{two}" to "{dataset}", keeping {limit:d} calls'))
def two_project_sink(ctx, sender, one, two, dataset, limit):
    sink(ctx, {one: dataset, two: dataset}, sender, limit=limit)


@given(parsers.parse('a sink for the sender "{sender}" sending "{one}" to "{first}" and "{two}" to "{second}"'))
def two_tenant_sink(ctx, sender, one, first, two, second):
    sink(ctx, {one: first, two: second}, sender)


@given(parsers.parse('a sink for the sender "{sender}" sending nothing'))
def empty_sink(ctx, sender):
    sink(ctx, {}, sender)


@given(parsers.parse('a sink for the sender "{sender}" sending "{repo}" to "{dataset}", flushing on write'))
def flushing_sink(ctx, sender, repo, dataset):
    sink(ctx, {repo: dataset}, sender, flush_on_write=True)


@given(parsers.parse('a sink for the sender "{sender}" sending "{repo}" to "{dataset}", flushing on write, '
                     "started {seconds:d} seconds ago"))
def started_sink(ctx, sender, repo, dataset, seconds):
    sink(ctx, {repo: dataset}, sender, flush_on_write=True)
    ctx.sink.started = ctx.now - seconds


def give(ctx, n, repo):
    ctx.sink.write(CallRecord(**record(n, repo=repo)))


@given(parsers.parse('the sink was given call {n:d} of "{repo}"'))
def was_given(ctx, n, repo):
    give(ctx, n, repo)


@given(parsers.parse('the sink was given calls {first:d} to {last:d} of "{repo}"'))
def was_given_calls(ctx, first, last, repo):
    for n in range(first, last + 1):
        give(ctx, n, repo)


@when(parsers.parse('the sink is given call {n:d} of "{repo}"'))
def is_given(ctx, n, repo):
    act(ctx, lambda: give(ctx, n, repo))


@given(parsers.parse("{seconds:d} seconds pass"))
@when(parsers.parse("{seconds:d} seconds pass"))
def seconds_pass(ctx, seconds):
    ctx.now += seconds


def flush(ctx):
    ctx.sink.flush()
    if ctx.sink.held and not hasattr(ctx, "first_held"):
        ctx.first_held = next(iter(ctx.sink.held.values()))


@given("the sink flushed")
def flushed(ctx):
    flush(ctx)


@when("the sink flushes")
def flushes(ctx):
    act(ctx, lambda: flush(ctx))


@given("storage takes no PUT")
def no_put(ctx):
    ctx.buffer.down_for = {"PUT"}


@given("storage takes files again")
def takes_again(ctx):
    ctx.buffer.down_for = set()


@given("storage loses its answer to the first PUT")
def loses_answer(ctx):
    real, lost = ctx.buffer.send, [True]

    def send(request, timeout):
        answer = real(request, timeout)
        if lost and request.method == "PUT":
            lost.clear()
            return Response(None, error="no answer")
        return answer
    ctx.buffer.send = send


@given(parsers.parse('the sink\'s token can\'t be read, saying "{why}"'))
def no_token(ctx, why):
    def token():
        raise OSError(why)
    ctx.sink.token = token


@given(parsers.parse('storage answers every request with {status:d} and the body "{body}"'))
def answers_every(ctx, status, body):
    ctx.buffer.send = lambda request, timeout: Response(status, body.encode())


@then("the file held back went as it was")
def held_went_as_it_was(ctx):
    """The file the first refusal held back is in the buffer, under its name, byte for byte."""
    name, data = ctx.first_held
    assert ctx.buffer.files.get(name) == data, f"{name} is not in the buffer as it was held"


@when(parsers.parse('the scanner scans "{dataset}"'))
def scanner_scans(ctx, dataset):
    bigquery = PickyBigQuery()
    made = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    scanner = Scanner(Inbox(ctx.buffer, lambda: "pod-token", BASE),
                      BigQueryLoader(made, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                      JobBudget(lambda: ctx.now), lambda _: None, lambda: ctx.now)

    def scanned():
        scanner.tick([dataset])
        return {"rows": [r["request_id"] for r in bigquery.rows],
                "status rows": [{k: r[k] for k in ("kind", "sender", "calls")} for r in bigquery.status_rows],
                "sender reports": [{k: r[k] for k in ("page_count", "page_ms_median", "cold_start")}
                                   for r in bigquery.status_rows if r["kind"] == "sender"],
                "inbox": ctx.buffer.inbox()}
    ctx.attempt(scanned)


@given(parsers.parse("the sink was given the page times {times}"))
def page_times(ctx, times):
    for ms in times.replace(" and ", ", ").split(", "):
        ctx.sink.page(float(ms))


@given(parsers.parse("a sink keeps up to {limit:d} page times"))
def page_limit(monkeypatch, limit):
    import integrations.storage_sink as storage_sink
    monkeypatch.setattr(storage_sink, "PAGE_LIMIT", limit)


@given("another flush is under way")
def flush_under_way(ctx):
    ctx.sink.flushing.acquire()


@given("the other flush is over")
def flush_over(ctx):
    ctx.sink.flushing.release()


@then(parsers.parse('the result\'s "{key}" matches "{pattern}"'))
def result_key_matches(ctx, key, pattern):
    """The value at the dotted key is text, and all of it is the pattern (`*` for anything)."""
    from pubtest.core import dig, matches
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    got = dig(ctx.result, key)
    assert isinstance(got, str) and matches(pattern, got), f"the result's {key} is {got!r}"


@then(parsers.parse('the result\'s "{key}" is the package\'s version'))
def result_key_is_version(ctx, key):
    from pubtest.core import dig
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    got = dig(ctx.result, key)
    assert got == __version__, f"the result's {key} is {got!r}, not {__version__}"


@when(parsers.parse("I ask how long a {seconds:d} second cycle's first wait is, the random number being {r:g}"))
def first_wait_given(ctx, seconds, r):
    ctx.attempt(lambda: first_wait(seconds, lambda: r))


@when(parsers.parse("I ask how long a {seconds:d} second cycle's first wait is"))
def first_wait_random(ctx, seconds):
    ctx.attempt(lambda: first_wait(seconds))


@then(parsers.parse("the result is at least {low:g} and less than {high:g}"))
def result_between(ctx, low, high):
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    assert low <= ctx.result < high, f"the result is {ctx.result}"
