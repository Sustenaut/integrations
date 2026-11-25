"""The vectors in tests/vectors/, which clients/js/caller.js runs too, so both callers
call, retry and record the same way; the cassette vectors in tests/vectors/cassettes/,
which clients/js/transports.js runs too, so both record and replay the same way; and a
file the JS sink wrote, read here."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict
from pathlib import Path


from integrations import registry
from integrations.avrofile import decode, file_name, file_status
from integrations.calls import Caller, Request, Response, Unavailable
from integrations.transports import Cassette, Faulty, Recorder, promote
from pubtest.core import dig, show
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import records

scenarios("vectors.feature")

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
CASSETTES = VECTORS / "cassettes"
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
    transport = Cassette({"responses": vector["responses"]})
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


# --- cassettes ------------------------------------------------------------------

@given(parsers.parse('the cassette vector "{name}"'))
def the_cassette_vector(ctx, name):
    ctx.vector = json.loads((CASSETTES / name).read_text(encoding="utf-8"))


class Scripted:
    """The provider of a recording vector: its answers in turn, each taking its elapsed_ms."""

    def __init__(self, answers, clock):
        self.answers, self.clock = list(answers), clock

    def send(self, request, timeout):
        answer = self.answers.pop(0)
        self.clock[0] += answer.get("elapsed_ms", 0) / 1000
        if answer.get("status") is None:
            return Response(None, error=answer.get("error"))
        return Response(answer["status"], body_bytes(answer.get("body")) or b"",
                        headers=dict(answer.get("headers", {})))


def body_bytes(body):
    if body is None:
        return None
    if "repeat" in body:
        return (body["repeat"] * body["times"]).encode("utf-8")
    if "json" in body:
        return json.dumps(body["json"]).encode("utf-8")
    if "text" in body:
        return body["text"].encode("utf-8")
    return base64.b64decode(body["base64"])


def answered(response):
    """An answer as the vector expects it: status, headers (names in lower case), body."""
    if response.status is None:
        return {"error": response.error}
    out = {"status": response.status, "headers": {k.lower(): v for k, v in response.headers.items()}}
    try:
        out["json"] = json.loads(response.body)
    except ValueError:
        try:
            out["text"] = response.body.decode("utf-8")
        except UnicodeDecodeError:
            out["base64"] = base64.b64encode(response.body).decode("ascii")
    return out


def fill(value, results):
    """{{N.key}} in a call's text, as the value at the dotted key of the Nth result."""
    if isinstance(value, dict):
        return {k: fill(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, results) for v in value]
    if isinstance(value, str):
        whole = re.fullmatch(r"\{\{(\d+)\.([^}]+)\}\}", value)
        if whole:
            return dig(results[int(whole.group(1))], whole.group(2))
        return re.sub(r"\{\{(\d+)\.([^}]+)\}\}", lambda m: str(dig(results[int(m.group(1))], m.group(2))), value)
    return value


def refused_as(error, wanted):
    """A refusal as the vector expects it: its class, and which of the expected texts its message holds."""
    wanted = wanted if isinstance(wanted, dict) else {}
    return {"raises": type(error).__name__, "mentions": [m for m in wanted.get("mentions", []) if m in str(error)]}


@when("the transports run it")
def run_cassette_vector(ctx):
    """Each step of the vector on a Cassette, a Recorder or Faulty, as the vector says;
    what came of each, in the shape of the vector's "expect"."""
    vector = ctx.vector

    def run():
        clock, slept = [0.0], [0.0]
        recorder = Recorder(Scripted(vector["record"], clock), scenario=vector.get("scenario"),
                            clock=lambda: clock[0], names=vector.get("names")) if "record" in vector else None
        saved = None

        def chain(base):
            if "faults" not in vector:
                return base
            return Faulty(base, vector["faults"], advance=lambda s: slept.__setitem__(0, slept[0] + s))

        cassette = Cassette(vector["cassette"], mode="replay") if "cassette" in vector else None
        transport = chain(recorder or cassette)
        results: list = []
        for i, step in enumerate(vector["steps"]):
            wanted = vector["expect"]["results"][i] if i < len(vector["expect"]["results"]) else None
            try:
                if "call" in step:
                    asked = fill(step["call"], results)
                    request = Request(asked["method"], asked["url"], asked.get("headers", {}),
                                      body_bytes(asked.get("body")), plugin=asked["plugin"],
                                      operation=asked["operation"])
                    results.append(answered(transport.send(request, asked.get("timeout", 10))))
                elif "save" in step or "promote" in step:
                    path = ctx.directory / "cassette.json"
                    if saved is not None:
                        path.write_text(json.dumps(saved), encoding="utf-8")
                    if "save" in step:
                        recorder.save(path)
                    else:
                        promote(path)
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    shown = json.loads(json.dumps(saved))
                    for block in (shown["current"], shown.get("baseline") or {}):
                        block.pop("recorded", None)
                    results.append({"saved": shown,
                                    "leaked": [t for t in vector.get("absent", []) if t in path.read_text("utf-8")]})
                elif "replay" in step:
                    cassette = Cassette(saved, mode="replay")
                    transport = chain(cassette)
                    results.append({"replayed": True})
                elif "unused" in step:
                    results.append({"unused": cassette.unused()})
                elif "close" in step:
                    cassette.close()
                    results.append({"closed": True})
                elif "rules" in step:
                    results.append({"rules": registry.record(step["rules"] or None)})
            except Exception as e:  # noqa: BLE001 - each step's refusal is part of what the vector expects
                results.append(refused_as(e, wanted))
        out = {"results": results}
        if "faults" in vector:
            out["slept"] = slept[0]
        return out

    ctx.attempt(run)


@when("I list the vectors of cassettes")
def list_cassette_vectors(ctx):
    ctx.attempt(lambda: [p.name for p in sorted(CASSETTES.glob("*.json"))])


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
