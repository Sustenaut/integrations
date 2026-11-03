"""The ingest pod's parts: its BigQuery loads, its Google token, its Avro and its health."""

from __future__ import annotations

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
from integrations.avrofile import decode, encode
from integrations.ingest.bigquery import BigQueryLoader, Outcome
from integrations.ingest.google_token import GoogleToken

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


def test_avro_is_the_same_bytes_for_the_same_records_and_keeps_every_field():
    rows = [record(1), record(2, status=None)]
    first, again = encode(rows), encode([dict(r) for r in rows])
    assert first == again
    assert decode(encode(rows)) == rows


@pytest.mark.parametrize(("answer", "outcome"), [
    ("silent", Outcome.RETRY), ("busy", Outcome.RETRY), ("job-busy", Outcome.RETRY), ("running", Outcome.RETRY),
    ("forbidden", Outcome.RETRY), ("bad-request", Outcome.REJECTED), ("invalid", Outcome.REJECTED), ("ok", Outcome.LOADED)])
def test_a_load_says_whether_another_try_may_work(answer, outcome):
    bigquery = FakeBigQuery()
    bigquery.answer = answer
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    loader = BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2)
    result = loader.load("ds", encode([record(1)]), "calls_x")
    assert result.outcome is outcome
    assert result.ended == (answer in ("job-busy", "invalid"))


def test_a_lost_answer_finds_the_job_already_there():
    bigquery = FakeBigQuery()
    caller = Caller("integrations", "ingest", sink=MemorySink(), transport=bigquery, sleep=lambda _: None)
    loader = BigQueryLoader(caller, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2)
    data = encode([record(1)])
    assert loader.load("ds", data, "calls_x").outcome is Outcome.LOADED
    assert loader.load("ds", data, "calls_x").outcome is Outcome.LOADED and len(bigquery.rows) == 1


def test_ingest_answers_health_and_has_no_public_docs():
    http = TestClient(create_app())
    assert http.get("/healthz").json() == {"status": "ok"}
    assert http.get("/docs").status_code == 404 and http.get("/openapi.json").status_code == 404
    assert http.post("/records", json={}).status_code in (404, 405)


def test_the_record_fields_are_the_schemas():
    schema = json.loads((Path(__file__).parents[2] / "src/integrations/schemas/call-record.schema.json").read_text())
    from integrations.avrofile import SCHEMA
    from integrations.calls import CallRecord
    assert set(schema["required"]) == set(CallRecord.__dataclass_fields__) == {f["name"] for f in SCHEMA["fields"]}


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
