"""cloudflare.dns's check, against recorded answers."""

from __future__ import annotations

import pytest

from integrations.calls import Caller, Replay
from integrations.clients.cloudflare_dns import VERIFY, CloudflareDns, FakeCloudflareDns

KEY = f"GET {VERIFY}"


def checked(answer):
    transport = Replay({KEY: answer})
    result = CloudflareDns(Caller("a-repo", transport=transport, sleep=lambda _: None), lambda: "a-token").check()
    return result, transport


def test_an_active_token_passes_and_is_sent_as_cloudflare_wants():
    result, transport = checked({"status": 200, "json": {"result": {"status": "active"}}})
    assert result.ok
    assert transport.sent[0].headers["Authorization"] == "Bearer a-token"


@pytest.mark.parametrize(("answer", "says"), [
    ({"status": 200, "json": {"result": {"status": "expired"}}}, "expired"),
    ({"status": 401, "json": {}}, "HTTP 401"),
    ({"error": "no answer"}, "no answer"),
])
def test_anything_else_fails_and_says_why(answer, says):
    result, _ = checked(answer)
    assert not result.ok and says in result.detail


def test_the_fake():
    assert FakeCloudflareDns().check().ok and not FakeCloudflareDns(working=False).check().ok
