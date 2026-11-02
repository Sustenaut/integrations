"""The scanner: files in storage reach BigQuery once and leave, whatever stops partway."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from integrations.avrofile import decode, encode, file_status
from integrations.calls import Caller, MemorySink, Response
from integrations.ingest.bigquery import JOBS_PER_TABLE_PER_DAY, STATUS_TABLE, BigQueryLoader, JobBudget
from integrations.ingest.inbox import MAX_BATCH_BYTES, MAX_BATCH_FILES, Inbox, Scanner, StorageDown, Waiting

from .test_ingest import BASE, FakeBigQuery, record

DATASET = "ds"
AVRO = b"Obj\x01"


class FakeBuffer:
    """A tenant's folders, as the storage service lists them."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.uploaded = "2027-01-15T07:50:00.000Z"
        self.down_for: set[str] = set()
        self.sent: list[tuple[str, str]] = []

    def send(self, request, timeout):
        url = urlparse(request.url)
        tenant, _, path = url.path.lstrip("/").partition("/")
        assert tenant and request.headers["Authorization"] == "Bearer pod-token"
        self.sent.append((request.method, path))
        if request.method in self.down_for:
            return Response(None, error="no answer")
        if path == "list":
            prefix = parse_qs(url.query)["prefix"][0]
            names = sorted(n for n in self.files if n.startswith(prefix))
            return Response(200, json.dumps({
                "files": [{"name": n, "size": len(self.files[n]), "uploaded": self.uploaded} for n in names],
                "cursor": None}).encode())
        if request.method == "PUT":
            self.files[path] = request.body
            return Response(200, b"{}")
        if request.method == "DELETE":
            self.files.pop(path, None)
            return Response(204)
        return Response(200, self.files[path]) if path in self.files else Response(404, b"{}")

    def inbox(self):
        """What senders left: the pod's own report is left out."""
        return sorted(n for n in self.files if n.startswith("inbox/") and "-ingest-" not in n)

    def under(self, folder):
        return sorted(n for n in self.files if n.startswith(folder))


class PickyBigQuery(FakeBigQuery):
    """Rejects any job holding a row marked bad; `fail_jobs` jobs end in a failure that may pass."""

    def __init__(self):
        super().__init__()
        self.ended: dict[str, dict] = {}
        self.fail_jobs = 0
        self.fail_status = 0
        self.refuse_status = False
        self.call_jobs: list[str] = []
        self.status_jobs: list[str] = []
        self.status_rows: list[dict] = []

    def send(self, request, timeout):
        if request.method == "POST" and self.answer == "ok":
            head, _, tail = request.body.partition(b"application/octet-stream\r\n\r\n")
            config = json.loads(head[head.index(b"{"):head.rindex(b"}") + 1])
            job = config["jobReference"]["jobId"]
            table = config["configuration"]["load"]["destinationTable"]["tableId"]
            if job in self.jobs:
                return Response(409, b"{}")
            data = tail[:tail.rindex(b"\r\n--")]
            self.jobs[job] = data
            rows = decode(data)
            if table == STATUS_TABLE:
                self.status_jobs.append(job)
                if self.refuse_status:
                    self.ended[job] = {"reason": "invalid"}
                elif self.fail_status:
                    self.fail_status -= 1
                    self.ended[job] = {"reason": "backendError"}
                else:
                    self.status_rows += rows
                return Response(200, b"{}")
            self.posts += 1
            self.call_jobs.append(job)
            if any(r["operation"] == "bad" for r in rows):
                self.ended[job] = {"reason": "invalid"}
            elif self.fail_jobs:
                self.fail_jobs -= 1
                self.ended[job] = {"reason": "backendError"}
            else:
                self.rows += rows
            return Response(200, b"{}")
        if request.method == "GET" and self.answer == "ok":
            error = self.ended.get(request.url.rsplit("/", 1)[1])
            return Response(200, json.dumps({"status": {"state": "DONE", **({"errorResult": error} if error else {})}}).encode())
        return super().send(request, timeout)


@pytest.fixture
def world():
    buffer, bigquery = FakeBuffer(), PickyBigQuery()
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    statuses: list[dict] = []
    clock = lambda: 1_800_000_000.0  # noqa: E731 - a moment after the files' upload time
    scanner = Scanner(Inbox(buffer, lambda: "pod-token", BASE),
                      BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                      JobBudget(clock), statuses.append, clock)
    return scanner, buffer, bigquery, statuses


def waiting(buffer, n, status=None, **fields):
    name = f"inbox/calls/2027-01-15/0750{n:02d}-web-{n}.avro"
    buffer.files[name] = encode([record(n, **fields)], status)
    return name


def left(buffer):
    """Anything of the senders' still in the buffer, in any folder."""
    return [n for n in buffer.files if "-ingest-" not in n and not n.startswith("batches/")] + buffer.under("batches/")


def report(sender="web-1", calls=1, **fields):
    return {"kind": "sender", "sender": sender, "version": "1.0", "at": "2027-01-15T07:50:00Z",
            "cycle_start": "2027-01-15T07:48:00Z", "cycle_end": "2027-01-15T07:50:00Z", "calls": calls, "drops": 0,
            "buffer_size": 1, "storage_failures": 0, **fields}


def ids(bigquery):
    return sorted(r["request_id"] for r in bigquery.rows)


def test_waiting_files_load_as_one_job_and_leave(world):
    scanner, buffer, bigquery, statuses = world
    for n in (1, 2, 3):
        waiting(buffer, n)
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2", "r3"] and bigquery.posts == 1
    assert not left(buffer)
    status = statuses[0]
    assert status["kind"] == "ingest" and status["dataset"] == DATASET and status["loaded_files"] == 3 and status["jobs_today"] == 1
    assert status["inbox_files"] == 0 and status["inbox_oldest_seconds"] is None and status["storage_ok"]


def test_an_empty_inbox_spends_no_job_and_still_reports(world):
    scanner, _, bigquery, statuses = world
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and len(statuses) == 1 and statuses[0]["inbox_files"] == 0


def test_a_file_with_no_records_just_leaves(world):
    scanner, buffer, bigquery, _ = world
    buffer.files["inbox/calls/2027-01-15/empty.avro"] = encode([])
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and not left(buffer)


def test_a_crash_after_the_load_doesnt_load_again(world):
    scanner, buffer, bigquery, _ = world
    waiting(buffer, 1)
    waiting(buffer, 2)
    buffer.down_for = {"DELETE"}
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2"] and len(buffer.inbox()) == 2 and len(buffer.under("batches/")) == 1
    buffer.down_for = set()
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2"] and not left(buffer)


def test_a_crash_partway_through_deleting_finishes_with_what_is_left(world):
    scanner, buffer, bigquery, _ = world
    first = waiting(buffer, 1)
    waiting(buffer, 2)
    buffer.down_for = {"DELETE"}
    scanner.tick([DATASET])
    buffer.down_for = set()
    del buffer.files[first]
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2"] and not left(buffer)


def test_a_crash_while_splitting_a_rejected_batch_loads_nothing_twice(world):
    scanner, buffer, bigquery, _ = world
    for n in (1, 2, 3):
        waiting(buffer, n)
    bad = waiting(buffer, 4, operation="bad")
    buffer.down_for = {"DELETE"}
    scanner.tick([DATASET])
    buffer.down_for = set()
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2", "r3"] and buffer.inbox() == []
    assert buffer.under("batches/") == [] and bad.replace("inbox/", "rejected/") in buffer.files


def test_a_batch_whose_files_have_all_gone_only_loses_its_record(world):
    scanner, buffer, bigquery, _ = world
    buffer.files["batches/old.json"] = json.dumps({"files": ["inbox/calls/gone.avro"], "attempt": 0}).encode()
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and not left(buffer)


def test_new_files_wait_while_an_unfinished_batch_does(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    bigquery.answer = "running"
    scanner.tick([DATASET])
    assert len(buffer.under("batches/")) == 1
    waiting(buffer, 2)
    jobs = len(bigquery.call_jobs)
    scanner.tick([DATASET])
    assert len(bigquery.call_jobs) == jobs and len(buffer.inbox()) == 2
    assert "did not finish" in statuses[-1]["problems"] and statuses[-1]["inbox_files"] == 3  # the pod's own last report waits too


def test_a_job_that_failed_in_a_way_that_may_pass_goes_again_as_a_new_job(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    bigquery.fail_jobs = 1
    scanner.tick([DATASET])
    assert bigquery.rows == [] and len(buffer.inbox()) == 1
    assert json.loads(buffer.files[buffer.under("batches/")[0]])["attempt"] == 1
    assert "backendError" in statuses[0]["problems"]
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and not left(buffer) and len(bigquery.call_jobs) == 2


def test_bad_files_alone_move_to_rejected(world):
    scanner, buffer, bigquery, statuses = world
    for n in (1, 2, 4):
        waiting(buffer, n)
    bad = waiting(buffer, 3, operation="bad")
    held = buffer.files[bad]
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1", "r2", "r4"] and buffer.inbox() == []
    assert buffer.files["rejected/calls/2027-01-15/075003-web-3.avro"] == held
    assert buffer.under("batches/") == []
    assert statuses[0]["rejected_now"] == 1 and statuses[0]["rejected_files"] == 1
    assert statuses[0]["rejected_oldest_seconds"] > 0 and "invalid" in statuses[0]["problems"]


def test_finding_a_bad_file_takes_few_jobs(world):
    scanner, buffer, bigquery, _ = world
    for n in range(1, 17):
        waiting(buffer, n)
    waiting(buffer, 17, operation="bad")
    scanner.tick([DATASET])
    assert len(ids(bigquery)) == 16 and bigquery.posts <= 12


def test_a_file_that_isnt_avro_or_isnt_call_records_is_rejected_without_a_job(world):
    scanner, buffer, bigquery, statuses = world
    buffer.files["inbox/calls/2027-01-15/broken.avro"] = AVRO + b"junk"
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and buffer.under("rejected/") == ["rejected/calls/2027-01-15/broken.avro"]
    assert "can't be read" in statuses[0]["problems"]


def test_rows_that_cant_be_call_records_are_rejected(world, monkeypatch):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    monkeypatch.setattr("integrations.ingest.inbox.encode", lambda rows, status=None: (_ for _ in ()).throw(ValueError("no")))
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and len(buffer.under("rejected/")) == 1
    assert "not call records" in statuses[0]["problems"]


def test_the_days_job_limit_keeps_files_waiting(world):
    scanner, buffer, bigquery, statuses = world
    for _ in range(JOBS_PER_TABLE_PER_DAY):
        scanner.jobs.spend(DATASET)
    waiting(buffer, 1)
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and len(buffer.inbox()) == 1
    assert statuses[0]["jobs_today"] == JOBS_PER_TABLE_PER_DAY and "load jobs" in statuses[0]["problems"]


def test_a_batch_is_kept_to_what_fits_in_memory(world):
    scanner, _, _, _ = world
    many = [Waiting(f"f{n}", 1, 0.0) for n in range(MAX_BATCH_FILES + 5)]
    assert len(scanner._choose(many)) == MAX_BATCH_FILES
    big = [Waiting("a", MAX_BATCH_BYTES, 0.0), Waiting("b", 1, 0.0)]
    assert scanner._choose(big) == ["a"]
    assert scanner._choose([Waiting("huge", MAX_BATCH_BYTES + 1, 0.0)]) == ["huge"]


def test_storage_down_leaves_everything_and_says_so(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    buffer.down_for = {"GET"}
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and len(buffer.inbox()) == 1
    assert statuses[0]["storage_ok"] is False and "no answer" in statuses[0]["problems"]


def test_a_batch_record_that_cant_be_written_means_no_load(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    buffer.down_for = {"PUT"}
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and statuses[0]["storage_ok"] is False
    assert "no answer" in statuses[0]["problems"]


def test_a_refusal_says_what_was_refused():
    class Refusing:
        def send(self, request, timeout):
            return Response(403, b"{}")
    with pytest.raises(StorageDown, match="write: HTTP 403"):
        Inbox(Refusing(), lambda: "pod-token", BASE).put(DATASET, "inbox/calls/a.avro", AVRO)


def test_one_projects_bad_day_doesnt_stop_the_next(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    real = scanner._work
    def work(dataset, status, problems):
        if dataset == "broken":
            raise RuntimeError("boom")
        real(dataset, status, problems)
    scanner._work = work
    scanner.tick(["broken", DATASET])
    assert "RuntimeError: boom" in statuses[0]["problems"]
    assert ids(bigquery) == ["r1"] and statuses[1]["dataset"] == DATASET


def test_backlog_is_reported_with_its_age(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1)
    waiting(buffer, 2)
    bigquery.answer = "busy"
    scanner.tick([DATASET])
    status = statuses[0]
    assert status["inbox_files"] == 2 and status["inbox_bytes"] > 0 and status["inbox_more"] is False
    assert status["inbox_oldest_seconds"] == 600


def test_a_senders_report_reaches_the_status_table_and_not_calls(world):
    scanner, buffer, bigquery, _ = world
    waiting(buffer, 1, report(calls=1, drops=3))
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and not left(buffer)
    assert [(r["sender"], r["calls"], r["drops"]) for r in bigquery.status_rows] == [("web-1", 1, 3)]
    assert scanner.jobs.used(DATASET) == 1 and scanner.jobs.used(DATASET, STATUS_TABLE) == 1


def test_an_idle_senders_file_has_a_report_and_spends_no_calls_job(world):
    scanner, buffer, bigquery, _ = world
    buffer.files["inbox/calls/2027-01-15/075000-web-idle.avro"] = encode([], report(calls=0))
    scanner.tick([DATASET])
    assert bigquery.posts == 0 and not left(buffer)
    assert [r["calls"] for r in bigquery.status_rows] == [0]


def test_many_reports_are_one_job(world):
    scanner, buffer, bigquery, _ = world
    for n in (1, 2, 3):
        waiting(buffer, n, report(sender=f"web-{n}"))
    scanner.tick([DATASET])
    assert len(bigquery.status_jobs) == 1 and len(bigquery.status_rows) == 3


def test_the_pod_reports_each_cycle_and_it_loads_on_the_next(world):
    scanner, buffer, bigquery, _ = world
    scanner.tick([DATASET])
    own = [n for n in buffer.files if "-ingest-" in n]
    assert len(own) == 1
    held = file_status(buffer.files[own[0]])
    assert held["kind"] == "ingest" and held["inbox_files"] == 0 and "dataset" not in held
    scanner.tick([DATASET])
    assert [r["kind"] for r in bigquery.status_rows] == ["ingest"]
    assert own[0] not in buffer.files and bigquery.posts == 0


def test_the_pods_report_says_what_went_wrong(world):
    scanner, buffer, _, statuses = world
    buffer.files["inbox/calls/2027-01-15/broken.avro"] = AVRO + b"junk"
    scanner.tick([DATASET])
    own = [n for n in buffer.files if "-ingest-" in n]
    assert "can't be read" in file_status(buffer.files[own[0]])["problems"]
    assert "can't be read" in statuses[0]["problems"]


def test_reports_arent_loaded_twice_when_the_calls_go_again(world):
    scanner, buffer, bigquery, _ = world
    waiting(buffer, 1, report())
    bigquery.fail_jobs = 1
    scanner.tick([DATASET])
    assert len(bigquery.status_rows) == 1 and bigquery.rows == []
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and [r["sender"] for r in bigquery.status_rows if r["kind"] == "sender"] == ["web-1"]


def test_a_crash_after_the_reports_loaded_doesnt_load_them_again(world):
    scanner, buffer, bigquery, _ = world
    waiting(buffer, 1, report())
    buffer.down_for = {"DELETE"}
    scanner.tick([DATASET])
    buffer.down_for = set()
    scanner.tick([DATASET])
    assert [r["sender"] for r in bigquery.status_rows if r["kind"] == "sender"] == ["web-1"]
    assert ids(bigquery) == ["r1"] and not left(buffer)


def test_a_report_job_that_failed_goes_again_before_the_calls(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1, report())
    bigquery.fail_status = 1
    scanner.tick([DATASET])
    assert bigquery.rows == [] and bigquery.status_rows == [] and "backendError" in statuses[0]["problems"]
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and not left(buffer)
    assert len([r for r in bigquery.status_rows if r["kind"] == "sender"]) == 1


def test_rejected_reports_dont_hold_up_the_calls(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1, report())
    bigquery.refuse_status = True
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and not left(buffer) and "reports rejected" in statuses[0]["problems"]


def test_a_report_that_isnt_one_only_loses_the_report(world):
    scanner, buffer, bigquery, statuses = world
    waiting(buffer, 1, {"kind": "sender"})
    scanner.tick([DATASET])
    assert ids(bigquery) == ["r1"] and bigquery.status_rows == [] and "bad report" in statuses[0]["problems"]


def test_the_status_table_has_its_own_days_jobs(world):
    scanner, buffer, bigquery, statuses = world
    for _ in range(JOBS_PER_TABLE_PER_DAY):
        scanner.jobs.spend(DATASET, STATUS_TABLE)
    assert scanner.jobs.may_load(DATASET)
    waiting(buffer, 1, report())
    scanner.tick([DATASET])
    assert bigquery.rows == [] and len(buffer.inbox()) == 1 and "load jobs" in statuses[0]["problems"]
    assert statuses[0]["status_jobs_today"] == JOBS_PER_TABLE_PER_DAY
