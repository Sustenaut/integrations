"""slack.post and slack.channel-heal: what a bot says, and which channels exist.

Messages use a bot token, so they show as the bot. Managing channels takes the
workspace owner's token, since members can't create them. Slack answers 200 even
when it refuses, with `ok` false and its reason, so a refusal is read from the body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

from ..calls import Caller, Check, IntegrationError, Refused, Request

POST = "slack.post"
HEAL = "slack.channel-heal"
API = "https://slack.com/api"


def _call(caller: Caller, plugin: str, operation: str, token: str, method: str, *, repeatable: bool = False,
          **params: object) -> dict:
    response = caller.call(plugin, operation, Request(
        "POST", f"{API}/{method}", {"Authorization": f"Bearer {token}"}, urlencode(params).encode(),
        repeatable=repeatable), refused=lambda r: not r.json().get("ok"))
    if not response.ok:
        raise Refused(plugin, operation, f"{method}: HTTP {response.status}")
    out = response.json()
    if not out.get("ok"):
        raise Refused(plugin, operation, f"{method}: {out.get('error')}")
    return out


class SlackPost:
    def __init__(self, caller: Caller, token: Callable[[], str]):
        self.caller, self.token = caller, token

    def post(self, channel: str, text: str) -> None:
        """A message from the bot. `channel` may be a person's id, which is a
        direct message."""
        _call(self.caller, POST, "post", self.token(), "chat.postMessage", channel=channel, text=text)

    def check(self) -> Check:
        try:
            _call(self.caller, POST, "check", self.token(), "auth.test", repeatable=True)
        except IntegrationError as e:
            return Check(POST, False, e.detail)
        return Check(POST, True)


@dataclass
class FakeSlackPost:
    """A stand-in for a program's tests: keeps what would have been posted."""

    posted: list[tuple[str, str]] = field(default_factory=list)
    working: bool = True

    def post(self, channel: str, text: str) -> None:
        if not self.working:
            raise Refused(POST, "post", "chat.postMessage: the fake is set to fail")
        self.posted.append((channel, text))

    def check(self) -> Check:
        return Check(POST, self.working, "" if self.working else "the fake is set to fail")


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    archived: bool = False
    creator: str = ""
    purpose: str = ""


class SlackChannels:
    def __init__(self, caller: Caller, owner_token: Callable[[], str], bot_token: Callable[[], str]):
        self.caller, self.owner_token, self.bot_token = caller, owner_token, bot_token

    def _owner(self, operation: str, method: str, **params: object) -> dict:
        return _call(self.caller, HEAL, operation, self.owner_token(), method, **params)

    def user_id(self, token: Callable[[], str]) -> str:
        """Whose token this is."""
        return _call(self.caller, HEAL, "list", token(), "auth.test", repeatable=True)["user_id"]

    def channels(self) -> list[SlackChannel]:
        found, cursor = [], ""
        while True:
            page = _call(self.caller, HEAL, "list", self.owner_token(), "conversations.list", repeatable=True,
                         types="public_channel,private_channel", exclude_archived="false", limit=1000, cursor=cursor)
            found += [SlackChannel(c["id"], c["name"], c.get("is_archived", False), c.get("creator", ""),
                                   (c.get("purpose") or {}).get("value", "")) for c in page["channels"]]
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return found

    def create(self, name: str) -> SlackChannel:
        """A new channel, with the bot invited so it can post there."""
        channel = self._owner("change", "conversations.create", name=name)["channel"]
        self._owner("change", "conversations.invite", channel=channel["id"], users=self.user_id(self.bot_token))
        return SlackChannel(channel["id"], channel["name"])

    def rename(self, channel_id: str, name: str) -> None:
        self._owner("change", "conversations.rename", channel=channel_id, name=name)

    def archive(self, channel_id: str) -> None:
        self._owner("change", "conversations.archive", channel=channel_id)

    def unarchive(self, channel_id: str) -> None:
        self._owner("change", "conversations.unarchive", channel=channel_id)

    def set_purpose(self, channel_id: str, text: str) -> None:
        self._owner("change", "conversations.setPurpose", channel=channel_id, purpose=text)

    def check(self) -> Check:
        try:
            _call(self.caller, HEAL, "check", self.owner_token(), "auth.test", repeatable=True)
        except IntegrationError as e:
            return Check(HEAL, False, e.detail)
        return Check(HEAL, True)


@dataclass
class FakeSlackChannels:
    """A stand-in for a program's tests: the channels, in memory."""

    existing: dict[str, SlackChannel] = field(default_factory=dict)
    working: bool = True
    users: dict[str, str] = field(default_factory=dict)

    def user_id(self, token: Callable[[], str]) -> str:
        return self.users.get(token(), "U-fake")

    def channels(self) -> list[SlackChannel]:
        return list(self.existing.values())

    def create(self, name: str) -> SlackChannel:
        channel = SlackChannel(f"C{len(self.existing) + 1}", name)
        self.existing[channel.id] = channel
        return channel

    def _replace(self, channel_id: str, **changes: object) -> None:
        was = self.existing[channel_id]
        self.existing[channel_id] = SlackChannel(**{**was.__dict__, **changes})

    def rename(self, channel_id: str, name: str) -> None:
        self._replace(channel_id, name=name)

    def archive(self, channel_id: str) -> None:
        self._replace(channel_id, archived=True)

    def unarchive(self, channel_id: str) -> None:
        self._replace(channel_id, archived=False)

    def set_purpose(self, channel_id: str, text: str) -> None:
        self._replace(channel_id, purpose=text)

    def check(self) -> Check:
        return Check(HEAL, self.working, "" if self.working else "the fake is set to fail")
