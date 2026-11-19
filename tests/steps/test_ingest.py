"""The ingest pod's parts: its BigQuery loads, its Google token, its Avro and its health.
BigQuery is an in-memory fake of its load jobs (FakeBigQuery, which test_inbox.py builds on);
Google's token services are fake services (providers.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import pubtest.http
from integrations.avrofile import SCHEMA, decode, encode
from integrations.calls import CallRecord, Caller, MemorySink, Response
from integrations.ingest import main  # noqa: F401 - what uvicorn runs must import
from integrations.ingest.app import create_app
from integrations.ingest.bigquery import BigQueryLoader
from integrations.ingest.google_token import FederatedToken, GoogleToken, google_token, project_of
from integrations.ingest.main import scanning
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import caller
from .stores import record

scenarios("ingest.feature")

ROOT = Path(__file__).resolve().parents[2]


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


EXTERNAL = {"type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/providers/k3s",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {"file": "/var/run/secrets/google/token"},
            "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                                                 "reader@a-project.iam.gserviceaccount.com:generateAccessToken"}


def now(ctx) -> float:
    if not hasattr(ctx, "now"):
        ctx.now = 1000.0
    return ctx.now


def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@pytest.fixture
def http_client(ctx):
    """The ingest pod's app, with the scanner's check a Given set."""
    def send(method, path, headers, body):
        checks = getattr(ctx, "checks", list)
        return pubtest.http.asgi(create_app(checks))(method, path, headers, body)
    return send


@given("the call records 1 and 2, the second with no status")
def records_one_and_two(ctx):
    ctx.rows = [record(1), record(2, status=None)]


@when("I encode them, and again from copies")
def encode_twice(ctx):
    ctx.attempt(lambda: encode(ctx.rows) == encode([dict(r) for r in ctx.rows]))


@when("I encode them and read them back")
def encode_and_decode(ctx):
    ctx.attempt(lambda: decode(encode(ctx.rows)))


def loader(ctx) -> BigQueryLoader:
    if not hasattr(ctx, "loader"):
        ctx.bigquery = FakeBigQuery()
        made = Caller("integrations", "ingest", sink=MemorySink(), transport=ctx.bigquery, sleep=lambda _: None)
        ctx.loader = BigQueryLoader(made, lambda: "gcp-token", "proj", sleep=lambda _: None, polls=2)
    return ctx.loader


@given(parsers.parse('BigQuery answers a load "{answer}"'))
def bigquery_answers(ctx, answer):
    loader(ctx)
    ctx.bigquery.answer = answer


def load(ctx, n, dataset, table):
    result = loader(ctx).load(dataset, encode([record(n)]), table)
    return {"outcome": result.outcome.value, "ended": result.ended, "rows loaded": len(ctx.bigquery.rows)}


@given(parsers.parse('call record {n:d} was loaded into "{dataset}" as "{table}"'))
def was_loaded(ctx, n, dataset, table):
    load(ctx, n, dataset, table)


@when(parsers.parse('I load call record {n:d} into "{dataset}" as "{table}"'))
def i_load(ctx, n, dataset, table):
    ctx.attempt(lambda: load(ctx, n, dataset, table))


@when(parsers.parse("I check a scanner whose last pass was {seconds:d} seconds ago"))
def check_scanner(ctx, seconds):
    ctx.attempt(lambda: scanning(seconds))


@given(parsers.parse("the scanner's last pass was {seconds:d} seconds ago"))
def scanner_stopped(ctx, seconds):
    ctx.checks = lambda: [scanning(seconds)]


@when("I compare the call record's fields with its JSON schema's and its Avro schema's")
def compare_fields(ctx):
    def differences():
        schema = json.loads((ROOT / "src/integrations/schemas/call-record.schema.json").read_text())
        sets = [set(schema["required"]), set(CallRecord.__dataclass_fields__), {f["name"] for f in SCHEMA["fields"]}]
        return sorted(set.union(*sets) - set.intersection(*sets))
    ctx.attempt(differences)


@given(parsers.parse('a service account "{email}" whose key trades at "{uri}"'))
def service_account(ctx, email, uri):
    key = pem()
    ctx.secrets.append(key)
    ctx.token = GoogleToken(caller(ctx, "r"), {"client_email": email, "token_uri": uri, "private_key": key},
                            clock=lambda: now(ctx))


@given(parsers.parse('the pod\'s external account credential, its token "{token}"'))
def external_account(ctx, token):
    ctx.reads = []
    ctx.token = FederatedToken(caller(ctx, "r"), EXTERNAL, clock=lambda: now(ctx),
                               read=lambda path: ctx.reads.append(path) or f"{token}\n")


@given("its token was asked for")
def token_was_asked(ctx):
    ctx.token()


@given(parsers.parse("{seconds:d} seconds pass"))
def seconds_pass(ctx, seconds):
    ctx.now = now(ctx) + seconds


@when("I ask for its token")
def ask_token(ctx):
    ctx.attempt(ctx.token)


@when("I ask for its token twice")
def ask_token_twice(ctx):
    ctx.attempt(lambda: [ctx.token(), ctx.token()])


@then(parsers.parse('the "{name}" service was sent a form with "{field}" set to "{value}"'))
def sent_a_form_with(ctx, name, field, value):
    """Some application/x-www-form-urlencoded request carried the field with that value."""
    from urllib.parse import parse_qs
    forms = [parse_qs(r["body"]) for r in ctx.fakes[name].requests]
    assert any(form.get(field) == [value] for form in forms), f"{name} was sent {forms}"


@then(parsers.parse('the pod\'s token was read once, from "{path}"'))
def token_read_once(ctx, path):
    assert ctx.reads == [path], f"the pod's token was read from {ctx.reads}"


CREDENTIALS = {
    "the pod's external account credential": EXTERNAL,
    'a key whose project_id is "p"': {"project_id": "p"},
    "an external account credential with no account": {"type": "external_account"},
    'a key of "w"': {"client_email": "w"},
}


@when(parsers.parse("I read the project of {credential}"))
def project(ctx, credential):
    ctx.attempt(lambda: project_of(CREDENTIALS[credential]))


@when(parsers.parse("I make the Google token for {credential}"))
def make_google_token(ctx, credential):
    ctx.attempt(lambda: type(google_token(caller(ctx, "r"), CREDENTIALS[credential])).__name__)


@when("I make a federated token of a service account credential")
def federated_of_key(ctx):
    ctx.attempt(lambda: FederatedToken(Caller("r"), {"type": "service_account"}))


@when("I read the ingest's Google credential and its Deployment in k8s/")
def read_k8s(ctx):
    def facts():
        configmap = yaml.safe_load((ROOT / "k8s" / "google-credential.yaml").read_text(encoding="utf-8"))
        credential = json.loads(configmap["data"]["credential.json"])
        deployment = next(d for d in yaml.safe_load_all((ROOT / "k8s" / "ingest.yaml").read_text(encoding="utf-8"))
                          if d and d["kind"] == "Deployment")
        spec = deployment["spec"]["template"]["spec"]
        (volume,) = [v for v in spec["volumes"] if v["name"] == "google-token"]
        (source,) = volume["projected"]["sources"]
        (container,) = spec["containers"]
        mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
        env = {e["name"]: e.get("value") for e in container["env"]}
        return {
            "audience": credential["audience"], "project": project_of(credential),
            "token": type(google_token(Caller("integrations", "ingest"), credential)).__name__,
            "service account": spec["serviceAccountName"], "automounts a token": spec["automountServiceAccountToken"],
            "projected token's audience": source["serviceAccountToken"]["audience"],
            "projected token's file": f"{mounts['google-token']}/{source['serviceAccountToken']['path']}",
            "credential's token file": credential["credential_source"]["file"],
            "INGEST_KEY_FILE": env["INGEST_KEY_FILE"],
            "credential's file": f"{mounts['google-credential']}/credential.json",
        }
    ctx.attempt(facts)
