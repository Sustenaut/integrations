"""The vectors in tests/vectors/, which clients/js/caller.js runs too, so both callers
call, retry and record the same way; and a file the JS sink wrote, read here."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


from integrations import registry
from integrations.avrofile import decode, file_name, file_status
from integrations.calls import Caller, Replay, Request, Unavailable
from pubtest.core import dig, show
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import records

scenarios("vectors.feature")

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
RECORD_SCHEMA = registry.load(registry.SCHEMAS / "call-record.schema.json")


def refusal(rule):
    if rule is None:
        return None
    return lambda response: response.json().get(rule["key"]) == rule["equals"]


@given(parsers.parse('the vector "{name}"'))
def the_vector(ctx, name):
    ctx.vector = json.loads((VECTORS / name).read_text(encoding="utf-8"))


@when("the caller makes its call")
def vector_call(ctx):
    """The vector's call, on its recorded answers; what came of it, in the
    shape of the vector's "expect"."""
    vector = ctx.vector
    sink, naps = records(ctx), []
    transport = Replay(vector["responses"])
    made = Caller("a-repo", "web", sink=sink, transport=transport, modes=vector.get("modes"), sleep=naps.append,
                  new_id=lambda: "id-1", random=lambda: vector.get("random", 1.0))
    asked = vector["request"]
    body = asked.get("body")
    request = Request(asked["method"], asked["url"], asked.get("headers", {}),
                      body.encode("utf-8") if body is not None else None, asked.get("repeatable", False))

    def call():
        try:
            answer = made.call(vector["plugin"], vector["operation"], request, request_id=vector.get("request_id"),
                               refused=refusal(vector.get("refused")))
            raised, status = None, answer.status
        except Unavailable:
            raised, status = "Unavailable", sink.records[0].status
        record = asdict(sink.records[0])
        return {"status": status, "raises": raised, "sent": len(transport.sent), "sleeps": naps,
                "record": {k: record[k] for k in vector["expect"]["record"]}}

    ctx.attempt(call)


@when("I list the vectors of calls")
def list_vectors(ctx):
    ctx.attempt(lambda: [p.name for p in sorted(VECTORS.glob("*.json")) if p.name != "sink.json"])


@when(parsers.parse('I read "{name}", the file the JS sink wrote'))
def read_sink_file(ctx, name):
    def read():
        data = (VECTORS / name).read_bytes()
        return {"records": decode(data), "report": file_status(data),
                "name": file_name(ctx.vector["at"], ctx.vector["sender"], data)}
    ctx.attempt(read)


def _result(ctx):
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    return ctx.result


@then("the result is what the vector expects")
def is_expected(ctx):
    got, want = _result(ctx), ctx.vector["expect"]
    assert got == want, f"the result is {show(got)}, not {show(want)}"


@then(parsers.parse('the result\'s "{key}" is the vector\'s "{path}"'))
def key_is_vectors(ctx, key, path):
    got, want = dig(_result(ctx), key), dig(ctx.vector, path)
    assert got == want, f"the result's {key} is {show(got)}, not {show(want)}"
