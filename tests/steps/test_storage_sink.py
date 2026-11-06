"""A sender's files: one every cycle, even with nothing in it, and nothing lost or doubled."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from integrations import __version__
from integrations.avrofile import decode, file_status
from integrations.calls import CallRecord, Caller, MemorySink, Response
from integrations.ingest.bigquery import BigQueryLoader, JobBudget
from integrations.ingest.inbox import Inbox, Scanner
from integrations.storage_sink import StorageSink, first_wait

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
        "buffer_size": 0, "storage_failures": 0, "page_count": 0, "page_ms_low": None, "page_ms_median": None,
        "page_ms_high": None, "cold_start": True}


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


def test_with_flush_on_write_a_request_that_finds_the_file_overdue_writes_it():
    buffer = FakeBuffer()
    now = [START]
    sink = StorageSink({"a-repo": "ds"}, transport=buffer, token=lambda: "pod-token", base_url=BASE, sender="run-1",
                       clock=lambda: now[0], start=False, flush_on_write=True)
    sink.write(call(1))
    assert files(buffer) == []
    now[0] += 119
    sink.write(call(2))
    assert files(buffer) == []
    now[0] += 1
    sink.write(call(3))
    name, = files(buffer)
    assert [r["request_id"] for r in decode(buffer.files[name])] == ["r1", "r2", "r3"]
    assert file_status(buffer.files[name])["cycle_start"] == "2027-01-15T08:00:00Z"


def test_without_it_writing_never_waits_on_storage(world):
    sink, buffer, now = world
    now[0] += 600
    sink.write(call(1))
    assert files(buffer) == []


def test_a_write_doesnt_flush_while_another_flush_is_under_way():
    buffer = FakeBuffer()
    now = [START + 200]
    sink = StorageSink({"a-repo": "ds"}, transport=buffer, token=lambda: "pod-token", base_url=BASE, sender="run-1",
                       clock=lambda: now[0], start=False, flush_on_write=True)
    sink.started = START
    sink.flushing.acquire()
    sink.write(call(1))
    assert files(buffer) == [] and len(sink.waiting["ds"]) == 1
    sink.flushing.release()
    sink.write(call(2))
    assert len(files(buffer)) == 1


def test_a_write_storage_didnt_take_is_said_in_the_log(world, capsys):
    sink, buffer, _ = world
    buffer.down_for = {"PUT"}
    sink.flush()
    assert "storage didn't take inbox/calls/" in capsys.readouterr().err


def test_a_refusal_says_storages_reason_and_a_missing_token_says_why(world, capsys):
    sink, buffer, _ = world
    buffer.send = lambda request, timeout: Response(401, b'{"error": "refused: a token for another audience"}')

    def no_token():
        raise OSError("metadata server: timed out")

    sink.token = no_token
    sink.flush()
    err = capsys.readouterr().err
    assert "HTTP 401 refused: a token for another audience" in err and "no token to send: metadata server: timed out" in err


def test_a_refusal_without_a_reason_still_says_its_status(world, capsys):
    sink, buffer, _ = world
    buffer.send = lambda request, timeout: Response(500, b"not json")
    sink.flush()
    assert "HTTP 500\n" in capsys.readouterr().err


def test_page_times_become_the_windows_low_median_and_high(world):
    sink, buffer, now = world
    for ms in (120.4, 80, 3400, 95.6, 101):
        sink.page(ms)
    now[0] += 120
    sink.flush()
    report = file_status(buffer.files[files(buffer)[0]])
    assert (report["page_count"], report["page_ms_low"], report["page_ms_median"], report["page_ms_high"]) == (5, 80, 101, 3400)


def test_only_the_first_window_is_a_cold_start_and_a_quiet_one_has_no_times(world):
    sink, buffer, now = world
    sink.page(50)
    now[0] += 120
    sink.flush()
    now[0] += 120
    sink.flush()
    first, second = (file_status(buffer.files[n]) for n in files(buffer))
    assert first["cold_start"] is True and second["cold_start"] is False
    assert second["page_count"] == 0 and second["page_ms_median"] is None


def test_past_the_limit_page_times_are_still_counted(world, monkeypatch):
    import integrations.storage_sink as storage_sink
    monkeypatch.setattr(storage_sink, "PAGE_LIMIT", 3)
    sink, buffer, _ = world
    for ms in (10, 20, 30, 40, 50):
        sink.page(ms)
    sink.flush()
    report = file_status(buffer.files[files(buffer)[0]])
    assert report["page_count"] == 5 and report["page_ms_high"] == 30


def test_page_times_reach_the_status_table(world):
    sink, buffer, now = world
    bigquery = PickyBigQuery()
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    scanner = Scanner(Inbox(buffer, lambda: "pod-token", BASE),
                      BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                      JobBudget(lambda: now[0]), lambda _: None, lambda: now[0])
    sink.page(250)
    now[0] += 120
    sink.flush()
    scanner.tick(["ds"])
    row, = [r for r in bigquery.status_rows if r["kind"] == "sender"]
    assert (row["page_count"], row["page_ms_median"], row["cold_start"]) == (1, 250, True)


def test_the_threads_first_file_comes_a_random_part_of_a_cycle_in():
    assert first_wait(120, lambda: 0.25) == 30
    assert 0 <= first_wait(120) < 120
