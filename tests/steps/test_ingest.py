"""The ingest pod, with BigQuery and the storage service as fakes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from integrations.calls import Caller, MemorySink, Response
from integrations.ingest import main  # noqa: F401 - what uvicorn runs must import
from integrations.ingest.app import create_app
from integrations.ingest.avrofile import decode, encode
from integrations.ingest.bigquery import BigQueryLoader, Outcome
from integrations.ingest.google_token import GoogleToken
from integrations.ingest.overflow import PENDING, REJECTED, Overflow, OverflowUnavailable, tenant_of
from integrations.ingest.spool import Spool
from integrations.ingest.worker import JOBS_PER_TABLE_PER_DAY, Worker

BASE = "https://storage.example"


def record(n=1, **fields):
    return {"at": "2026-11-02T10:00:00Z", "repo": "a-repo", "app": "web", "plugin": "github.edit-pr",
            "operation": "open-pr", "request_id": f"r{n}", "latency_ms": 12, "status": 201, "outcome": "ok",
            "retries": 0, "bytes_in": 3, "bytes_out": 4, "mode": "required", "route": "routed", **fields}


class FakeBigQuery:
    """Load jobs: `done` is the job's final state, or a status to answer the upload with."""

    def __init__(self):
        self.jobs: dict[str, bytes] = {}
        self.rows: list[dict] = []
        self.answer = "ok"
        self.posts = 0

    def send(self, request, timeout):
        if request.method == "POST":
            self.posts += 1
            if self.answer == "silent":
                return Response(None, error="no answer")
            if self.answer == "busy":
                return Response(503, b"{}")
            if self.answer == "forbidden":
                return Response(403, b"{}")
            if self.answer == "bad-request":
                return Response(400, b"{}")
            head, _, tail = request.body.partition(b"application/octet-stream\r\n\r\n")
            job_id = json.loads(re.search(rb"(\{.*\})", head, re.S).group(1))["jobReference"]["jobId"]
            if job_id in self.jobs:
                return Response(409, b"{}")
            data = tail[:tail.rindex(b"\r\n--")]
            self.jobs[job_id] = data
            if self.answer == "ok":
                self.rows += decode(data)
            return Response(200, b"{}")
        job = request.url.rsplit("/", 1)[1]
        state = {"state": "DONE"}
        if self.answer == "invalid":
            state["errorResult"] = {"reason": "invalid", "message": "bad schema"}
        if self.answer == "job-busy":
            state["errorResult"] = {"reason": "backendError"}
        if self.answer == "running":
            state = {"state": "RUNNING"}
        assert job
        return Response(200, json.dumps({"status": state}).encode())


class FakeStorage:
    """The storage service's files and heads."""

    def __init__(self):
        self.files: dict[tuple[str, str], bytes] = {}
        self.heads: dict[tuple[str, str], str] = {}
        self.down = False
        self.kept = []

    def send(self, request, timeout):
        if self.down:
            return Response(None, error="no answer")
        assert request.headers["Authorization"] == "Bearer pod-token"
        tenant, _, path = request.url.removeprefix(f"{BASE}/").partition("/")
        if request.method == "POST" and path == "files":
            content = re.search(rb'filename="[^"]+"\r\nContent-Type: [^\r]+\r\n\r\n(.*?)\r\n--', request.body, re.S).group(1)
            self.kept.append(b'name="kept"\r\n\r\ntrue' in request.body)
            file_id = hashlib.sha256(content).hexdigest()
            self.files[tenant, file_id] = content
            return Response(200, json.dumps({"id": file_id}).encode())
        if path.startswith("head/"):
            name = tenant, path.removeprefix("head/")
            current = self.heads.get(name)
            if request.method == "GET":
                if current is None:
                    return Response(404, b"{}")
                return Response(200, self.files[tenant, current], etag=f'"{current}"')
            if current is None:
                moves = request.headers.get("If-None-Match") == "*"
            else:
                moves = request.headers.get("If-Match", "").strip('"') == current
            if not moves:
                return Response(412, b"{}")
            self.heads[name] = json.loads(request.body)["hash"]
            return Response(200, b"{}")
        if path.startswith("f/"):
            return Response(200, self.files[tenant, path.split("/")[1]])
        return Response(404, b"{}")

    def manifest(self, kind, dataset="ds"):
        tenant = tenant_of(dataset)
        head = self.heads.get((tenant, kind))
        return json.loads(self.files[tenant, head]) if head else []


@pytest.fixture
def now():
    return [1_800_000_000.0]


@pytest.fixture
def world(tmp_path, now):
    bigquery, storage = FakeBigQuery(), FakeStorage()
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    spool = Spool(tmp_path / "spool")
    alerts: list[str] = []
    worker = Worker(spool, BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2),
                    Overflow(storage, lambda: "pod-token", BASE), alerts.append, clock=lambda: now[0])
    return worker, spool, bigquery, storage, alerts


def batches(spool):
    return spool.batches()


def test_records_reach_bigquery_as_one_load_and_leave_the_spool(world):
    worker, spool, bigquery, _, _ = world
    spool.append("ds", [record(1), record(2)])
    spool.append("ds", [record(3)])
    worker.tick()
    assert [r["request_id"] for r in bigquery.rows] == ["r1", "r2", "r3"]
    assert bigquery.posts == 1
    assert not batches(spool)


def test_each_dataset_loads_its_own_records(world):
    worker, spool, bigquery, _, _ = world
    spool.append("one", [record(1)])
    spool.append("two", [record(2)])
    worker.tick()
    assert bigquery.posts == 2 and len(bigquery.rows) == 2


def test_an_empty_window_sends_nothing(world):
    worker, _, bigquery, _, _ = world
    worker.tick()
    assert bigquery.posts == 0


def test_records_survive_a_restart(world, tmp_path):
    _, spool, _, _, _ = world
    spool.append("ds", [record(1)])
    again = Spool(tmp_path / "spool")
    again.close_open_files()
    assert [r["request_id"] for b in again.batches() for r in b.records()] == ["r1"]


def test_avro_is_the_same_bytes_for_the_same_records_and_keeps_every_field():
    rows = [record(1), record(2, status=None)]
    assert encode(rows) == encode(rows)
    assert decode(encode(rows)) == rows


def test_a_lost_answer_cant_load_a_file_twice(world):
    worker, spool, bigquery, _, _ = world
    spool.append("ds", [record(1)])
    spool.close_open_files()
    batch = spool.batches()[0]
    data = encode(batch.records())
    assert worker.loader.load("ds", data, "calls_same").outcome is Outcome.LOADED
    assert worker.loader.load("ds", data, "calls_same").outcome is Outcome.LOADED
    assert len(bigquery.rows) == 1


@pytest.mark.parametrize("answer", ["silent", "busy", "job-busy", "running", "forbidden"])
def test_a_problem_that_may_pass_keeps_the_batch_and_tries_again(world, answer):
    worker, spool, bigquery, storage, alerts = world
    spool.append("ds", [record(1)])
    bigquery.answer = answer
    worker.tick()
    assert len(batches(spool)) == 1 and not storage.files and not alerts
    bigquery.jobs.clear()
    bigquery.answer = "ok"
    worker.tick()
    assert [r["request_id"] for r in bigquery.rows] == ["r1"] and not batches(spool)


def test_an_outage_past_the_limit_moves_the_batch_to_pending_and_a_good_load_replays_it(world, now):
    worker, spool, bigquery, storage, alerts = world
    spool.append("ds", [record(1)])
    bigquery.answer = "busy"
    worker.tick()
    assert not storage.manifest(PENDING)
    now[0] += worker.overflow_after_seconds
    worker.tick()
    assert len(storage.manifest(PENDING)) == 1 and not batches(spool) and all(storage.kept)
    assert not alerts
    bigquery.answer = "ok"
    spool.append("ds", [record(2)])
    worker.tick()
    assert sorted(r["request_id"] for r in bigquery.rows) == ["r1", "r2"]
    assert storage.manifest(PENDING) == []


def test_a_full_spool_moves_batches_to_overflow_sooner(world):
    worker, spool, bigquery, storage, _ = world
    worker.overflow_above_bytes = 1
    spool.append("ds", [record(1)])
    bigquery.answer = "busy"
    worker.tick()
    assert len(storage.manifest(PENDING)) == 1


def test_many_waiting_files_replay_as_one_merged_load(world, now):
    worker, spool, bigquery, storage, _ = world
    bigquery.answer = "busy"
    worker.overflow_after_seconds = 0
    for n in range(3):
        spool.append("ds", [record(n)])
        worker.tick()
    assert len(storage.manifest(PENDING)) == 3
    bigquery.answer = "ok"
    bigquery.posts = 0
    spool.append("ds", [record(9)])
    worker.tick()
    assert bigquery.posts == 2  # the new batch, and one for everything waiting
    assert sorted(r["request_id"] for r in bigquery.rows) == ["r0", "r1", "r2", "r9"]


def test_a_failed_replay_leaves_the_files_waiting(world, monkeypatch):
    worker, spool, bigquery, storage, _ = world
    bigquery.answer = "busy"
    worker.overflow_after_seconds = 0
    spool.append("ds", [record(1)])
    worker.tick()
    bigquery.answer = "ok"
    real = worker.loader.load
    monkeypatch.setattr(worker.loader, "load", lambda ds, data, job: real(ds, data, job) if job.startswith("calls") else
                        type("R", (), {"outcome": Outcome.RETRY})())
    spool.append("ds", [record(2)])
    worker.tick()
    assert len(storage.manifest(PENDING)) == 1


def test_a_rejected_file_goes_to_rejected_is_alerted_and_is_never_retried(world):
    worker, spool, bigquery, storage, alerts = world
    spool.append("ds", [record(1)])
    bigquery.answer = "invalid"
    worker.tick()
    assert len(storage.manifest(REJECTED)) == 1 and not storage.manifest(PENDING)
    assert not batches(spool) and "rejected" in alerts[0] and "invalid" in alerts[0]
    posts = bigquery.posts
    worker.tick()
    assert bigquery.posts == posts


def test_a_refused_upload_is_rejected(world):
    worker, spool, bigquery, storage, _ = world
    spool.append("ds", [record(1)])
    bigquery.answer = "bad-request"
    worker.tick()
    assert len(storage.manifest(REJECTED)) == 1


def test_with_storage_down_too_the_batch_stays_and_alerts(world, now):
    worker, spool, bigquery, storage, alerts = world
    spool.append("ds", [record(1)])
    bigquery.answer = "busy"
    storage.down = True
    worker.overflow_after_seconds = 0
    worker.tick()
    assert len(batches(spool)) == 1 and "stays in the spool" in alerts[0]


def test_a_replay_that_cant_reach_storage_alerts_and_waits(world):
    worker, spool, bigquery, storage, alerts = world
    bigquery.answer = "busy"
    worker.overflow_after_seconds = 0
    spool.append("ds", [record(1)])
    worker.tick()
    bigquery.answer = "ok"
    spool.append("ds", [record(2)])
    real = storage.send

    def down_for_heads(request, timeout):
        return Response(None, error="no answer") if "/head/" in request.url else real(request, timeout)

    storage.send = down_for_heads
    worker.tick()
    assert "replay" in alerts[0]


def test_the_days_job_limit_holds_batches_back_until_tomorrow(world, now):
    worker, spool, bigquery, _, _ = world
    worker.jobs_today[worker._key("ds")] = JOBS_PER_TABLE_PER_DAY
    spool.append("ds", [record(1)])
    worker.tick()
    assert bigquery.posts == 0 and len(batches(spool)) == 1
    now[0] += 86400
    worker.tick()
    assert bigquery.posts == 1


def test_at_the_hard_cap_records_are_refused_and_counted(tmp_path):
    spool = Spool(tmp_path / "s", max_bytes=10)
    spool.append("ds", [record(1), record(2)])
    assert spool.dropped == 2 and not spool.batches()


@pytest.mark.parametrize(("status", "problem"), [(500, "upload"), (None, "no answer")])
def test_overflow_says_when_storage_refuses(status, problem):
    class Refusing:
        def send(self, request, timeout):
            return Response(status, error=None if status else "no answer")

    overflow = Overflow(Refusing(), lambda: "t", BASE)
    with pytest.raises(OverflowUnavailable, match=problem):
        overflow.put(PENDING, "ds", b"x", "j")


def test_overflow_says_when_a_manifest_cant_be_read_moved_or_a_file_read():
    class Script:
        def __init__(self, *answers):
            self.answers = list(answers)

        def send(self, request, timeout):
            return self.answers.pop(0)

    uploaded = Response(200, b'{"id": "abc"}')
    with pytest.raises(OverflowUnavailable, match="manifest: HTTP 500"):
        Overflow(Script(uploaded, Response(500)), lambda: "t", BASE).put(PENDING, "ds", b"x", "j")
    with pytest.raises(OverflowUnavailable, match="manifest move: HTTP 412"):
        Overflow(Script(uploaded, Response(404), uploaded, Response(412)), lambda: "t", BASE).put(PENDING, "ds", b"x", "j")
    from integrations.ingest.overflow import Entry
    with pytest.raises(OverflowUnavailable, match="read: HTTP 404"):
        Overflow(Script(Response(404)), lambda: "t", BASE).read("ds", Entry("i", "n", "j"))


def test_each_project_has_its_own_tenant_and_never_sees_anothers_files():
    storage = FakeStorage()
    overflow = Overflow(storage, lambda: "pod-token", BASE)
    overflow.put(PENDING, "calls_visit_ee", b"theirs", "j1")
    overflow.put(PENDING, "calls_business_plan", b"others", "j2")
    assert {tenant for tenant, _ in storage.files} == {"calls-visit-ee", "calls-business-plan"}
    theirs = overflow.pending("calls_visit_ee")
    assert [e.job_id for e in theirs] == ["j1"]
    assert overflow.read("calls_visit_ee", theirs[0]) == b"theirs"


def test_a_tenant_is_named_like_its_dataset():
    assert tenant_of("calls_opportunity_site") == "calls-opportunity-site"


def test_the_same_file_put_twice_is_on_the_manifest_once():
    storage = FakeStorage()
    overflow = Overflow(storage, lambda: "pod-token", BASE)
    overflow.put(PENDING, "ds", b"x", "j")
    overflow.put(PENDING, "ds", b"x", "j")
    assert len(overflow.pending("ds")) == 1


def client(tmp_path, datasets=None):
    spool = Spool(tmp_path / "spool")
    return TestClient(create_app(spool, datasets if datasets is not None else {"a-repo": "ds"})), spool


def test_a_batch_is_on_disk_when_it_is_accepted(tmp_path):
    http, spool = client(tmp_path)
    assert http.post("/records", json={"records": [record(1), record(2)]}).status_code == 202
    spool.close_open_files()
    assert [r["request_id"] for r in spool.batches()[0].records()] == ["r1", "r2"]


def test_a_repository_with_no_dataset_is_refused_whole(tmp_path):
    http, spool = client(tmp_path)
    answer = http.post("/records", json={"records": [record(1), record(2, repo="other")]})
    assert answer.status_code == 403
    spool.close_open_files()
    assert not spool.batches()


@pytest.mark.parametrize("bad", [{"outcome": "fine"}, {"at": "yesterday"}, {"latency_ms": -1}])
def test_a_record_that_isnt_one_is_refused(tmp_path, bad):
    http, _ = client(tmp_path)
    assert http.post("/records", json={"records": [record(1, **bad)]}).status_code == 422


def test_a_field_a_record_doesnt_have_is_not_kept(tmp_path):
    http, spool = client(tmp_path)
    http.post("/records", json={"records": [record(1, body="a payload")]})
    spool.close_open_files()
    assert "body" not in spool.batches()[0].records()[0]


def test_health_says_how_many_were_dropped(tmp_path):
    http, _ = client(tmp_path)
    assert http.get("/healthz").json() == {"status": "ok", "dropped": 0}


def test_ingest_has_no_public_docs(tmp_path):
    http, _ = client(tmp_path)
    assert http.get("/docs").status_code == 404 and http.get("/openapi.json").status_code == 404


def test_the_record_fields_are_the_schemas():
    schema = json.loads((Path(__file__).parents[2] / "src/integrations/schemas/call-record.schema.json").read_text())
    from integrations.ingest.app import Record
    from integrations.ingest.avrofile import SCHEMA
    assert set(schema["required"]) == set(Record.model_fields) == {f["name"] for f in SCHEMA["fields"]}


def test_a_google_token_is_traded_in_once_and_reused():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    answers = [Response(200, b'{"access_token": "first"}'), Response(200, b'{"access_token": "second"}')]

    class Google:
        sent = 0

        def send(self, request, timeout):
            Google.sent += 1
            assert b"grant_type" in request.body and pem not in request.body.decode()
            return answers.pop(0)

    now = [1000.0]
    token = GoogleToken(Caller("r", transport=Google()), {"client_email": "w@x", "token_uri": "https://oauth.example/token",
                                                          "private_key": pem}, clock=lambda: now[0])
    assert token() == "first" and token() == "first" and Google.sent == 1
    now[0] += 3400
    assert token() == "second"


def test_a_refused_google_token_is_a_refusal():
    class Refused401:
        def send(self, request, timeout):
            return Response(401, b"{}")

    token = GoogleToken(Caller("r", transport=Refused401()), {"client_email": "w", "token_uri": "https://o.example/t",
                                                               "private_key": rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                                                                   serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
    from integrations.calls import Refused
    with pytest.raises(Refused):
        token()
