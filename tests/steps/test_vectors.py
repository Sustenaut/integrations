"""The vectors in tests/vectors/, which clients/js/caller.js runs too, so both callers
call, retry and record the same way; and a file the JS sink wrote, read here."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import jsonschema
import pytest

from integrations import registry
from integrations.avrofile import decode, file_name, file_status
from integrations.calls import Caller, MemorySink, Replay, Request, Unavailable

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
RECORD_SCHEMA = registry.load(registry.SCHEMAS / "call-record.schema.json")


def refusal(rule):
    if rule is None:
        return None
    return lambda response: response.json().get(rule["key"]) == rule["equals"]


@pytest.mark.parametrize("path", [p for p in sorted(VECTORS.glob("*.json")) if p.name != "sink.json"],
                         ids=lambda p: p.stem)
def test_a_vector(path):
    vector = json.loads(path.read_text(encoding="utf-8"))
    sink, naps = MemorySink(), []
    transport = Replay(vector["responses"])
    made = Caller("a-repo", "web", sink=sink, transport=transport, modes=vector.get("modes"), sleep=naps.append,
                  new_id=lambda: "id-1")
    asked = vector["request"]
    body = asked.get("body")
    request = Request(asked["method"], asked["url"], asked.get("headers", {}),
                      body.encode("utf-8") if body is not None else None, asked.get("repeatable", False))
    expect = vector["expect"]
    try:
        answer = made.call(vector["plugin"], vector["operation"], request, request_id=vector.get("request_id"),
                           refused=refusal(vector.get("refused")))
        raised, status = None, answer.status
    except Unavailable:
        raised, status = "Unavailable", sink.records[0].status
    assert (raised, status, len(transport.sent), naps) == (expect["raises"], expect["status"], expect["sent"],
                                                          expect["sleeps"])
    record = asdict(sink.records[0])
    jsonschema.validate(record, RECORD_SCHEMA)
    assert {k: record[k] for k in expect["record"]} == expect["record"]


def test_a_file_the_js_sink_wrote_reads_as_its_records_and_report():
    expected = json.loads((VECTORS / "sink.json").read_text(encoding="utf-8"))
    data = (VECTORS / "sink.avro").read_bytes()
    assert decode(data) == expected["records"]
    assert file_status(data) == expected["report"]
    assert file_name(expected["at"], expected["sender"], data) == expected["name"]
