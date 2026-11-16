"""cloudflare.dns's check, against recorded answers."""

from __future__ import annotations

import pytest

from integrations.calls import Caller, Replay
from integrations.clients.cloudflare_dns import VERIFY, CloudflareDns, FakeCloudflareDns
from pytest_bdd import scenarios, then

scenarios("cloudflare-dns.feature")

KEY = f"GET {VERIFY}"


def checked(answer):
    transport = Replay({KEY: answer})
    result = CloudflareDns(Caller("a-repo", transport=transport, sleep=lambda _: None), lambda: "a-token").check()
    return result, transport


@then("an active token passes and is sent as cloudflare wants")
def an_active_token_passes_and_is_sent_as_cloudflare_wants():
    result, transport = checked({"status": 200, "json": {"result": {"status": "active"}}})
    assert result.ok
    assert transport.sent[0].headers["Authorization"] == "Bearer a-token"


@then("anything else fails and says why")
def anything_else_fails_and_says_why():
    """Every case of the parametrized test this was, in turn."""
    for case0 in [
    ({"status": 200, "json": {"result": {"status": "expired"}}}, "expired"),
    ({"status": 401, "json": {}}, "HTTP 401"),
    ({"error": "no answer"}, "no answer"),
]:
        case0 = case0.values if type(case0).__name__ == "ParameterSet" else case0
        _case_anything_else_fails_and_says_why(answer=case0[0], says=case0[1])


def _case_anything_else_fails_and_says_why(answer, says):
    result, _ = checked(answer)
    assert not result.ok and says in result.detail


@then("the fake")
def the_fake():
    assert FakeCloudflareDns().check().ok and not FakeCloudflareDns(working=False).check().ok
