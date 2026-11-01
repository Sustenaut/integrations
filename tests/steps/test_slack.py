"""The Slack clients, against recorded answers."""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest

from integrations import cli
from integrations.calls import Caller, Check, MemorySink, Refused, Replay, Unavailable
from integrations.clients.slack import (FakeSlackChannels, FakeSlackPost, SlackChannel, SlackChannels, SlackPost)

API = "https://slack.com/api"


def caller(responses):
    sink, transport = MemorySink(), Replay(responses)
    return Caller("a-repo", sink=sink, transport=transport, sleep=lambda _: None), sink, transport


def ok(**fields):
    return {"status": 200, "json": {"ok": True, **fields}}


def params(request):
    return {k: v[0] for k, v in parse_qs(request.body.decode()).items()}


def test_a_post_uses_the_bot_token_and_is_recorded():
    made, sink, transport = caller({f"POST {API}/chat.postMessage": ok()})
    SlackPost(made, lambda: "bot").post("C1", "hello")
    request = transport.sent[0]
    assert request.headers["Authorization"] == "Bearer bot"
    assert params(request) == {"channel": "C1", "text": "hello"}
    assert (sink.records[0].plugin, sink.records[0].operation) == ("slack.post", "post")


def test_slacks_refusal_in_a_200_is_a_refusal():
    made, sink, _ = caller({f"POST {API}/chat.postMessage": {"status": 200, "json": {"ok": False, "error": "no_channel"}}})
    with pytest.raises(Refused, match="no_channel"):
        SlackPost(made, lambda: "bot").post("C1", "hello")
    assert sink.records[0].outcome == "ok"  # Slack answered; what it said is the client's to read


def test_an_http_refusal_is_a_refusal():
    made, _, _ = caller({f"POST {API}/chat.postMessage": {"status": 403, "json": {}}})
    with pytest.raises(Refused, match="HTTP 403"):
        SlackPost(made, lambda: "bot").post("C1", "hello")


def test_a_post_is_not_repeated_as_it_could_be_seen_twice():
    made, _, transport = caller({f"POST {API}/chat.postMessage": {"status": 503, "json": {}}})
    with pytest.raises(Unavailable):
        SlackPost(made, lambda: "bot").post("C1", "hello")
    assert len(transport.sent) == 1


def test_check_says_whether_the_token_works():
    made, _, transport = caller({f"POST {API}/auth.test": ok(user_id="U1")})
    assert SlackPost(made, lambda: "bot").check() == Check("slack.post", True)
    assert SlackChannels(made, lambda: "owner", lambda: "bot").check() == Check("slack.channel-heal", True)
    assert transport.sent[1].headers["Authorization"] == "Bearer owner"
    refused = {f"POST {API}/auth.test": {"status": 200, "json": {"ok": False, "error": "invalid_auth"}}}
    made, _, _ = caller(refused)
    assert not SlackPost(made, lambda: "bot").check().ok
    assert not SlackChannels(made, lambda: "owner", lambda: "bot").check().ok


def test_channels_follow_every_page():
    pages = [ok(channels=[{"id": "C1", "name": "one", "is_archived": True, "creator": "U1",
                           "purpose": {"value": "why"}}], response_metadata={"next_cursor": "more"}),
             ok(channels=[{"id": "C2", "name": "two"}], response_metadata={"next_cursor": ""})]
    made, _, transport = caller({f"POST {API}/conversations.list": pages})
    channels = SlackChannels(made, lambda: "owner", lambda: "bot").channels()
    assert channels == [SlackChannel("C1", "one", True, "U1", "why"), SlackChannel("C2", "two")]
    assert params(transport.sent[1])["cursor"] == "more"
    assert params(transport.sent[0])["exclude_archived"] == "false"


def test_a_new_channel_invites_the_bot_with_the_owners_token():
    made, _, transport = caller({
        f"POST {API}/conversations.create": ok(channel={"id": "C9", "name": "new"}),
        f"POST {API}/auth.test": ok(user_id="U-bot"),
        f"POST {API}/conversations.invite": ok()})
    assert SlackChannels(made, lambda: "owner", lambda: "bot").create("new") == SlackChannel("C9", "new")
    by_url = {r.url.rsplit("/", 1)[1]: r for r in transport.sent}
    assert by_url["auth.test"].headers["Authorization"] == "Bearer bot"
    assert by_url["conversations.invite"].headers["Authorization"] == "Bearer owner"
    assert params(by_url["conversations.invite"]) == {"channel": "C9", "users": "U-bot"}


@pytest.mark.parametrize(("method", "args", "called", "sent"), [
    ("rename", ("C1", "n"), "conversations.rename", {"channel": "C1", "name": "n"}),
    ("archive", ("C1",), "conversations.archive", {"channel": "C1"}),
    ("unarchive", ("C1",), "conversations.unarchive", {"channel": "C1"}),
    ("set_purpose", ("C1", "why"), "conversations.setPurpose", {"channel": "C1", "purpose": "why"}),
])
def test_each_change_uses_the_owners_token(method, args, called, sent):
    made, sink, transport = caller({f"POST {API}/{called}": ok()})
    getattr(SlackChannels(made, lambda: "owner", lambda: "bot"), method)(*args)
    assert params(transport.sent[0]) == sent
    assert transport.sent[0].headers["Authorization"] == "Bearer owner"
    assert (sink.records[0].plugin, sink.records[0].operation) == ("slack.channel-heal", "change")


def test_the_fakes_answer_as_the_clients_do():
    post = FakeSlackPost()
    post.post("C1", "hi")
    assert post.posted == [("C1", "hi")] and post.check().ok
    broken = FakeSlackPost(working=False)
    with pytest.raises(Refused):
        broken.post("C1", "hi")
    assert not broken.check().ok

    channels = FakeSlackChannels(users={"bot": "U-bot"})
    made = channels.create("one")
    channels.rename(made.id, "two")
    channels.set_purpose(made.id, "why")
    channels.archive(made.id)
    assert channels.channels() == [SlackChannel(made.id, "two", True, "", "why")]
    channels.unarchive(made.id)
    assert not channels.channels()[0].archived
    assert channels.user_id(lambda: "bot") == "U-bot"
    assert channels.check().ok and not FakeSlackChannels(working=False).check().ok


def test_the_command_posts_with_the_token_from_the_environment(monkeypatch, capsys):
    transport = Replay({f"POST {API}/chat.postMessage": ok()})
    monkeypatch.setenv("SLACK_TOKEN", "from-env")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(cli, "Caller", lambda repo, app: Caller(repo, app, transport=transport))
    assert cli.main(["slack", "post", "--channel", "U1", "--text", "hi"]) == 0
    assert transport.sent[0].headers["Authorization"] == "Bearer from-env"


def test_the_command_says_when_slack_refuses(monkeypatch, capsys):
    transport = Replay({f"POST {API}/chat.postMessage": {"status": 200, "json": {"ok": False, "error": "nope"}}})
    monkeypatch.setenv("SLACK_TOKEN", "t")
    monkeypatch.setattr(cli, "Caller", lambda repo, app: Caller(repo, app, transport=transport))
    assert cli.main(["slack", "post", "--channel", "U1", "--text", "hi"]) == 1
    assert "nope" in capsys.readouterr().err
