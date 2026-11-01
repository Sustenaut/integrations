"""linear.sync: Linear's GraphQL API.

A query is safe to repeat, so a scheduled one is retried; a mutation is not.
Linear explains a refusal in the body, as a 400 or as `errors` beside a 200.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..calls import Caller, Check, IntegrationError, Refused, Request

PLUGIN = "linear.sync"
API = "https://api.linear.app/graphql"
MUTATION = re.compile(r"\s*mutation\b")


def _reason(body: bytes, status: int | None) -> str:
    try:
        errors = json.loads(body).get("errors") or []
    except (ValueError, AttributeError):
        errors = []
    return "; ".join(e.get("message", "") for e in errors)[:500] or f"HTTP {status}"


class LinearClient:
    """`operation` says who is waiting: `read` for a person, `sync` for a timer."""

    def __init__(self, caller: Caller, key: Callable[[], str], operation: str = "sync", plugin: str = PLUGIN):
        self.caller, self.key, self.operation, self.plugin = caller, key, operation, plugin

    def query(self, query: str, variables: dict[str, Any] | None = None, *, request_id: str | None = None) -> dict:
        """What the query's `data` holds. Raises `Refused` or `Unavailable`."""
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        response = self.caller.call(self.plugin, self.operation, Request(
            "POST", API, {"Content-Type": "application/json", "Authorization": self.key()}, body,
            repeatable=not MUTATION.match(query)), request_id=request_id,
            refused=lambda r: bool(r.json().get("errors")))
        if not response.ok:
            raise Refused(self.plugin, self.operation, _reason(response.body, response.status))
        out = response.json()
        if out.get("errors"):
            raise Refused(self.plugin, self.operation, _reason(response.body, response.status))
        return out["data"]

    def check(self) -> Check:
        try:
            self.query("{ viewer { id } }")
        except IntegrationError as e:
            return Check(self.plugin, False, e.detail)
        return Check(self.plugin, True)


@dataclass
class FakeLinear:
    """A stand-in for a program's tests: answers each query in `answers` in turn,
    and keeps what was asked."""

    answers: list[dict] = field(default_factory=list)
    working: bool = True
    asked: list[tuple[str, dict]] = field(default_factory=list)

    def query(self, query: str, variables: dict[str, Any] | None = None, *, request_id: str | None = None) -> dict:
        self.asked.append((query, variables or {}))
        if not self.working:
            raise Refused(PLUGIN, "sync", "the fake is set to fail")
        return self.answers.pop(0) if self.answers else {}

    def check(self) -> Check:
        return Check(PLUGIN, self.working, "" if self.working else "the fake is set to fail")
