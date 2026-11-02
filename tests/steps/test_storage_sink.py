"""A sender's files: one every cycle, even with nothing in it, and nothing lost or doubled."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from integrations import __version__
from integrations.avrofile import decode, file_status
from integrations.calls import CallRecord, Caller, MemorySink, Response
from integrations.ingest.bigquery import BigQueryLoader, JobBudget
from integrations.ingest.inbox import Inbox, Scanner
from integrations.storage_sink import StorageSink

from .test_inbox import FakeBuffer, PickyBigQuery
from .test_ingest import BASE, record

START = 1_800_000_000.0


def call(n=1, repo="a-repo"):
    return CallRecord(**record(n, repo=repo))


@pytest.fixture
def world():
    buffer = FakeBuffer()
    now = [START]
    sink = StorageSink({"a-repo": "ds", "b-repo": "ds"}, transport=buffer, token=lambda: "pod-token", base_url=BASE,
                       sender="app-1_x", clock=lambda: now[0], start=False, limit=3)
    return sink, buffer, now


def files(buffer):
    return buffer.under("inbox/")


def test_an_idle_cycle_still_writes_a_report(world):
    sink, buffer, now = world
    now[0] += 120
    sink.flush()
    name, = files(buffer)
    assert name.startswith("inbox/calls/2027-01-15/080200-app-1-x-") and name.endswith(".avro")
    assert decode(buffer.files[name]) == []
    assert file_status(buffer.files[name]) == {
        "kind": "sender", "sender": "app-1_x", "version": __version__, "at": "2027-01-15T08:02:00Z",
        "cycle_start": "2027-01-15T08:00:00Z", "cycle_end": "2027-01-15T08:02:00Z", "calls": 0, "drops": 0,
        "buffer_size": 0, "storage_failures": 0}


def test_a_cycles_calls_are_one_file(world):
    sink, buffer, now = world
    sink.write(call(1))
    sink.write(call(2, repo="b-repo"))
    now[0] += 120
    sink.flush()
    name, = files(buffer)
    assert [r["request_id"] for r in decode(buffer.files[name])] == ["r1", "r2"]
    report = file_status(buffer.files[name])
    assert report["calls"] == 2 and report["buffer_size"] == 2
    now[0] += 120
    sink.flush()
    assert file_status(buffer.files[files(buffer)[1]])["calls"] == 0
    assert file_status(buffer.files[files(buffer)[1]])["cycle_start"] == "2027-01-15T08:02:00Z"


def test_each_project_has_its_own_tenant():
    sent = []

    class Taking:
        def send(self, request, timeout):
            sent.append(urlparse(request.url).path)
            return Response(200, b"{}")

    sink = StorageSink({"a": "calls_one", "b": "calls_two"}, transport=Taking(), token=lambda: "t", base_url=BASE,
                       sender="s", clock=lambda: START, start=False)
    sink.flush()
    assert sorted(p.split("/")[1] for p in sent) == ["calls-one", "calls-two"]


def test_past_the_limit_the_oldest_go_and_are_counted(world):
    sink, buffer, _ = world
    for n in range(1, 6):
        sink.write(call(n))
    sink.flush()
    data = buffer.files[files(buffer)[0]]
    assert [r["request_id"] for r in decode(data)] == ["r3", "r4", "r5"]
    assert file_status(data)["drops"] == 2 and file_status(data)["buffer_size"] == 3


def test_a_record_for_a_project_with_no_buffer_is_counted(world):
    sink, buffer, _ = world
    sink.write(call(1, repo="elsewhere"))
    sink.flush()
    assert file_status(buffer.files[files(buffer)[0]])["drops"] == 1


def test_writing_a_record_never_fails_with_no_projects():
    sink = StorageSink({}, transport=FakeBuffer(), token=lambda: "t", base_url=BASE, sender="s", start=False)
    sink.write(call(1))
    sink.flush()


def test_a_refused_file_goes_again_as_it_was_and_the_failures_are_reported(world):
    sink, buffer, now = world
    sink.write(call(1))
    buffer.down_for = {"PUT"}
    now[0] += 120
    sink.flush()
    assert files(buffer) == []
    held_name, held_data = sink.held["ds"]
    sink.write(call(2))
    now[0] += 120
    sink.flush()
    assert files(buffer) == [] and sink.failures["ds"] == 2
    buffer.down_for = set()
    now[0] += 120
    sink.flush()
    assert buffer.files[held_name] == held_data and not sink.held
    assert [r["request_id"] for r in decode(held_data)] == ["r1"]
    newer = next(n for n in files(buffer) if n != held_name)
    assert [r["request_id"] for r in decode(buffer.files[newer])] == ["r2"]
    assert file_status(buffer.files[newer])["storage_failures"] == 2


def test_a_lost_answer_cant_double_a_files_calls(world):
    sink, buffer, now = world
    real = buffer.send
    lost = [True]

    def send(request, timeout):
        answer = real(request, timeout)
        if lost and request.method == "PUT":
            lost.clear()
            return Response(None, error="no answer")
        return answer

    buffer.send = send
    sink.write(call(1))
    now[0] += 120
    sink.flush()
    now[0] += 120
    sink.flush()
    rows = [r["request_id"] for n in files(buffer) for r in decode(buffer.files[n])]
    assert rows == ["r1"]


def test_a_token_that_cant_be_read_is_a_failed_write(world):
    sink, buffer, _ = world

    def no_token():
        raise OSError("gone")

    sink.token = no_token
    buffer.send = lambda request, timeout: Response(401, b"{}")
    sink.flush()
    assert sink.failures["ds"] == 1 and "ds" in sink.held


def test_from_a_call_to_the_tables(world):
    sink, buffer, now = world
    bigquery = PickyBigQuery()
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    scanner = Scanner(Inbox(buffer, lambda: "pod-token", BASE),
                      BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                      JobBudget(lambda: now[0]), lambda _: None, lambda: now[0])
    sink.write(call(1))
    now[0] += 120
    sink.flush()
    scanner.tick(["ds"])
    assert [r["request_id"] for r in bigquery.rows] == ["r1"]
    assert [(r["kind"], r["sender"], r["calls"]) for r in bigquery.status_rows] == [("sender", "app-1_x", 1)]
    assert buffer.inbox() == []
