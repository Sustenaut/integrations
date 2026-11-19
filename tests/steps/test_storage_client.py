"""The storage client (integrations.clients.storage), on the vectors in tests/vectors/storage/
that clients/js/storage.js runs too, and what they don't cover. Storage is a fake service
(providers.py), but for the vectors, which a Cassette answers in their old {"responses": ...} format."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from integrations.calls import Caller, MemorySink
from integrations.clients import storage as client
from integrations.clients.storage import NotFound, Storage, StorageError, StorageUnavailable
from integrations.clients.storage_routes import ROUTES, SPEC
from integrations.transports import Cassette
from pubtest.steps.fakes import fake
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import caller

scenarios("storage-client.feature")

HERE = Path(__file__).resolve().parents[1]
VECTORS = HERE / "vectors" / "storage"
BASE = "https://storage.example"


def value(arg):
    return arg["bytes"].encode() if isinstance(arg, dict) and "bytes" in arg else arg


def plain(result):
    """A result as the vectors write it: bytes as {"text"}, a head as {"body", "hash"}."""
    if isinstance(result, bytes):
        return {"text": result.decode()}
    if isinstance(result, tuple):
        return {"body": plain(result[0]), "hash": result[1]}
    return result


def storage(ctx, tenant, **token):
    ctx.storage = Storage(caller(ctx, "a-repo"), tenant, base_url=BASE, **token)


@given(parsers.parse('the storage vector "{name}"'))
def storage_vector(ctx, name):
    ctx.vector = json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))


@when("I make its call on its recorded answers")
def make_vector_call(ctx):
    vector = ctx.vector
    ctx.sink, ctx.naps, ctx.replay = MemorySink(), [], Cassette({"responses": vector["responses"]})
    made = Caller("a-repo", "web", sink=ctx.sink, transport=ctx.replay, sleep=ctx.naps.append, new_id=lambda: "id-1",
                  random=lambda: 1.0)
    options = vector["client"]
    made = Storage(made, options["tenant"], base_url=BASE, token=options["token"],
                   request_id=options.get("request_id"), user_agent=options.get("user_agent"))
    kwargs = {("for_" if k == "for" else k): v for k, v in vector["kwargs"].items()}
    ctx.attempt(lambda: plain(getattr(made, vector["call"])(*map(value, vector["args"]), **kwargs)))


@then("it does what the vector expects")
def does_what_the_vector_expects(ctx):
    """What it returned or raised, its sleeps, the requests it sent and the records it kept."""
    expect = ctx.vector["expect"]
    ctx.refusal_read = True
    raised = None
    if ctx.refused is not None:
        error = ctx.refused.error
        assert isinstance(error, (StorageError, StorageUnavailable)), f"it raised {error!r}"
        raised = {"type": type(error).__name__, "status": error.status, "error": error.error}
    assert (raised, ctx.result, ctx.naps) == (expect["raises"], expect["result"], expect["sleeps"])
    sent = ctx.replay.sent
    assert len(sent) == len(expect["sent"]), f"{len(sent)} requests were sent"
    for request, want in zip(sent, expect["sent"]):
        assert (request.method, request.url) == (want["method"], want["url"])
        assert {k: request.headers.get(k) for k in want.get("headers", {})} == want.get("headers", {})
        if "json" in want:
            assert json.loads(request.body) == want["json"]
    kept = [asdict(r) for r in ctx.sink.records]
    assert len(kept) == len(expect["records"]), f"{len(kept)} records were kept"
    assert [{k: r[k] for k in want} for r, want in zip(kept, expect["records"])] == expect["records"]


@when("I list the storage vectors")
def list_vectors(ctx):
    ctx.attempt(lambda: [p.stem for p in sorted(VECTORS.glob("*.json"))])


@when(parsers.parse("I compare the storage routes' spec with \"{lock}\""))
def compare_spec(ctx, lock):
    def differences():
        pinned = json.loads((HERE.parent / lock).read_text(encoding="utf-8"))
        keys = set(SPEC) | {"repository", "commit", "sha256"}
        return sorted(k for k in keys if SPEC.get(k) != pinned.get(k))
    ctx.attempt(differences)


@when(parsers.parse('I read the storage route "{name}"'))
def read_route(ctx, name):
    ctx.attempt(lambda: ROUTES[name])


@given(parsers.parse('a storage client for the tenant "{tenant}" with the token "{token}"'))
def client_with_token(ctx, tenant, token):
    storage(ctx, tenant, token=token)


@given(parsers.parse('a storage client for the tenant "{tenant}" with the token file "{path}"'))
def client_with_token_file(ctx, tenant, path):
    storage(ctx, tenant, token_file=ctx.directory / path)


@given(parsers.parse('a storage client for the tenant "{tenant}" whose token is a function giving "{token}"'))
def client_with_token_function(ctx, tenant, token):
    storage(ctx, tenant, token=lambda: token)


@given(parsers.re(r'the "(?P<name>[^"]+)" service answers "(?P<request>[^"]+)" with (?P<status>\d+), '
                  r'the header "(?P<header>[^"]+)" set to "(?P<header_value>.*)", and:'))
def answers_with_header_and_body(ctx, monkeypatch, name, request, status, header, header_value, docstring):
    """An answer with both a header and a body, which pubtest gives one or the other."""
    fake(ctx, monkeypatch, name).answer(request, int(status), docstring, {header: header_value})


@when(parsers.parse('I upload "{data}" as "{name}", not kept, for "{someone}"'))
def upload(ctx, data, name, someone):
    ctx.attempt(lambda: ctx.storage.upload_file(data.encode(), name=name, kept=False, for_=someone))


@then(parsers.parse('the "{name}" service was sent the form:'))
def sent_the_form(ctx, name, datatable):
    """Some multipart/form-data request: framed by the boundary its Content-Type names, its
    parts exactly the table's rows, in order (field, and for a file its filename and type)."""
    keys, rows = datatable[0], datatable[1:]
    want = [dict(zip(keys, row, strict=True)) for row in rows]
    seen = []
    for request in ctx.fakes[name].requests:
        kind = {k.lower(): v for k, v in request["headers"].items()}.get("content-type", "")
        if not kind.startswith("multipart/form-data; boundary="):
            continue
        boundary, body = kind.split("boundary=")[1], request["body"]
        assert body.startswith(f"--{boundary}\r\n") and body.endswith(f"--{boundary}--\r\n"), body
        parts = []
        for chunk in body[len(f"--{boundary}\r\n"):-len(f"--{boundary}--\r\n")].split(f"--{boundary}\r\n"):
            head, _, text = chunk.partition("\r\n\r\n")
            assert text.endswith("\r\n"), chunk
            lines = head.split("\r\n")
            disposition = lines[0].removeprefix("Content-Disposition: form-data; ")
            field = disposition.split('name="')[1].split('"')[0]
            filename = disposition.split('filename="')[1].split('"')[0] if 'filename="' in disposition else ""
            kind = lines[1].removeprefix("Content-Type: ") if len(lines) > 1 else ""
            parts.append({"field": field, "filename": filename, "type": kind, "value": text[:-2]})
        seen.append(parts)
        if [{k: p[k] for k in keys} for p in parts] == want:
            return
    raise AssertionError(f"{name} was sent the forms {seen}")


@when(parsers.parse('I read the log of the head "{name}"'))
def read_head_log(ctx, name):
    ctx.attempt(lambda: ctx.storage.head_log(name))


@when(parsers.parse('I read the log of the head "{name}" as the tenant "{tenant}"'))
def read_head_log_as(ctx, name, tenant):
    ctx.attempt(lambda: ctx.storage.for_tenant(tenant).head_log(name))


@when(parsers.parse('I make a storage client for the tenant "{tenant}" with no token'))
def no_token(ctx, tenant):
    ctx.attempt(lambda: Storage(Caller("a-repo"), tenant))


@when(parsers.parse('I get the buffer file "{place}"'))
def get_buffer(ctx, monkeypatch, place):
    fake(ctx, monkeypatch, "storage.example")
    ctx.attempt(lambda: ctx.storage.get_buffer(place))


@when(parsers.re(r'I make the storage error (?P<kind>\w+) of "(?P<operation>[^"]+)" with HTTP (?P<status>\d+) '
                 r'and the reason "(?P<reason>[^"]*)"'))
def make_error(ctx, kind, operation, status, reason):
    def made():
        error = {"NotFound": NotFound, "StorageError": StorageError}[kind](operation, int(status), reason)
        return {"says": str(error), "is a StorageError": isinstance(error, StorageError)}
    ctx.attempt(made)


@when("I read storage's reason in no answer")
def reason_of_nothing(ctx):
    ctx.attempt(lambda: client.reason(None))


@when(parsers.parse('I read storage\'s reason in an answer of {status:d} with the body "{body}"'))
def reason_of(ctx, status, body):
    ctx.attempt(lambda: client.reason(client.Response(status, body.encode())))


@when(parsers.parse('I get the head "{name}"'))
def get_head(ctx, name):
    ctx.attempt(lambda: plain(ctx.storage.get_head(name)))
