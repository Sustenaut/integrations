"""The Linear client, against recorded answers."""

from __future__ import annotations

import json

import pytest

from integrations.calls import Caller, Check, MemorySink, Refused, Replay, Response, Unavailable
from integrations.clients.linear import API, FakeLinear, LinearClient

KEY = f"POST {API}"


def client(answers, operation="sync"):
    sink, transport = MemorySink(), Replay({KEY: answers})
    made = Caller("a-repo", sink=sink, transport=transport, sleep=lambda _: None)
    return LinearClient(made, lambda: "a-key", operation), sink, transport


def test_a_query_answers_its_data_and_sends_the_key_as_linear_wants():
    linear, sink, transport = client({"status": 200, "json": {"data": {"viewer": {"id": "1"}}}})
    assert linear.query("{ viewer { id } }", {"a": 1}) == {"viewer": {"id": "1"}}
    assert transport.sent[0].headers["Authorization"] == "a-key"
    assert json.loads(transport.sent[0].body) == {"query": "{ viewer { id } }", "variables": {"a": 1}}
    assert (sink.records[0].plugin, sink.records[0].operation) == ("linear.sync", "sync")


def test_operation_says_who_is_waiting():
    linear, sink, _ = client({"status": 200, "json": {"data": {}}}, "read")
    linear.query("{ a }", request_id="from-the-app")
    assert (sink.records[0].operation, sink.records[0].request_id) == ("read", "from-the-app")


def test_errors_beside_a_200_and_in_a_400_are_refusals_with_linears_reason():
    linear, _, _ = client({"status": 200, "json": {"errors": [{"message": "no such field"}]}})
    with pytest.raises(Refused, match="no such field"):
        linear.query("{ a }")
    linear, _, _ = client({"status": 400, "json": {"errors": [{"message": "bad"}, {"message": "worse"}]}})
    with pytest.raises(Refused, match="bad; worse"):
        linear.query("{ a }")
    linear, _, _ = client({"status": 401, "json": {}})
    with pytest.raises(Refused, match="HTTP 401"):
        linear.query("{ a }")


def test_a_refusal_with_a_body_that_is_not_json_still_says_its_status():
    class Plain:
        def send(self, request, timeout):
            return Response(403, b"<html>")

    linear = LinearClient(Caller("a-repo", transport=Plain()), lambda: "a-key")
    with pytest.raises(Refused, match="HTTP 403"):
        linear.query("{ a }")


def test_a_query_is_retried_on_a_timer_and_a_mutation_is_not():
    linear, _, transport = client({"status": 503, "json": {}})
    with pytest.raises(Unavailable):
        linear.query("{ a }")
    assert len(transport.sent) == 3
    linear, _, transport = client({"status": 503, "json": {}})
    with pytest.raises(Unavailable):
        linear.query("  mutation { a }")
    assert len(transport.sent) == 1


def test_a_query_a_person_waits_on_is_not_retried():
    linear, _, transport = client({"status": 503, "json": {}}, "read")
    with pytest.raises(Unavailable):
        linear.query("{ a }")
    assert len(transport.sent) == 1


def test_check_says_whether_the_key_works():
    linear, _, _ = client({"status": 200, "json": {"data": {"viewer": {"id": "1"}}}})
    assert linear.check() == Check("linear.sync", True)
    linear, _, _ = client({"status": 401, "json": {}})
    assert not linear.check().ok


def test_the_fake_answers_in_turn_and_keeps_what_was_asked():
    fake = FakeLinear(answers=[{"a": 1}])
    assert fake.query("{ a }", {"x": 1}) == {"a": 1}
    assert fake.query("{ b }") == {}
    assert fake.asked == [("{ a }", {"x": 1}), ("{ b }", {})]
    assert fake.check().ok
    broken = FakeLinear(working=False)
    with pytest.raises(Refused):
        broken.query("{ a }")
    assert not broken.check().ok
