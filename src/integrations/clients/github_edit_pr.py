"""github.edit-pr: an edit made in a web editor becomes a pull request.

An edit is one commit per file on a new branch. It is refused with `Conflict`
when a file on the base branch is no longer what the edit started from, so
nobody's work is overwritten. The caller names the branch's stem; `propose` adds
one past the highest number taken.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

from ..calls import Caller, Check, IntegrationError, Refused, Request, Response

PLUGIN = "github.edit-pr"
OPERATION = "open-pr"
API = "https://api.github.com"


@dataclass(frozen=True)
class Change:
    path: str
    old: str | None  # the text the edit started from; None for a new file
    new: str


class Conflict(Exception):
    """The file on the base branch is no longer what the edit started from."""


def next_branch(stem: str, taken: list[str]) -> str:
    """`stem` and one past the highest number already taken."""
    used = [int(m.group(1)) for t in taken if (m := re.fullmatch(re.escape(stem) + r"(\d+)", t))]
    return f"{stem}{max(used, default=0) + 1}"


class EditPullRequests:
    def __init__(self, caller: Caller, token: Callable[[], str], repo: str, base: str = "main"):
        self.caller, self.token, self.repo, self.base = caller, token, repo, base

    def _request(self, method: str, path: str, payload: dict | None = None, *, operation: str = OPERATION,
                 ok: tuple[int, ...] = (200, 201), request_id: str | None = None) -> Response:
        headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        response = self.caller.call(PLUGIN, operation, Request(method, f"{API}/repos/{self.repo}{path}", headers, body),
                                    request_id=request_id)
        if response.status not in ok:
            raise Refused(PLUGIN, operation, f"{method} {path.split('?')[0]}: HTTP {response.status}")
        return response

    def propose(self, *, stem: str, changes: list[Change], title: str, body: str,
                request_id: str | None = None) -> str:
        """Open a pull request with the changes on a new branch; its URL."""
        call = lambda *args, **kw: self._request(*args, request_id=request_id, **kw)  # noqa: E731
        base_sha = call("GET", f"/git/ref/heads/{quote(self.base)}").json()["object"]["sha"]
        blobs = {}
        for change in changes:
            found = call("GET", f"/contents/{quote(change.path)}?ref={base_sha}", ok=(200, 404))
            now = base64.b64decode(found.json()["content"]).decode() if found.status == 200 else None
            if now != change.old:
                raise Conflict(f"{change.path} has changed on {self.base} since this edit began")
            blobs[change.path] = found.json()["sha"] if found.status == 200 else None
        refs = call("GET", f"/git/matching-refs/heads/{quote(stem)}").json()
        branch = next_branch(stem, [ref["ref"].removeprefix("refs/heads/") for ref in refs])
        call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})
        for change in changes:
            put = {"message": title, "branch": branch, "content": base64.b64encode(change.new.encode()).decode()}
            if blobs[change.path]:
                put["sha"] = blobs[change.path]
            call("PUT", f"/contents/{quote(change.path)}", put)
        pull = call("POST", "/pulls", {"title": title, "head": branch, "base": self.base, "body": body})
        return pull.json()["html_url"]

    def check(self) -> Check:
        """The token can read the repository."""
        try:
            self._request("GET", "", operation="check", ok=(200,))
        except IntegrationError as e:
            return Check(PLUGIN, False, e.detail)
        return Check(PLUGIN, True)


@dataclass
class FakeEditPullRequests:
    """A stand-in for a program's tests: the same methods and refusals, kept in
    memory. `files` is what the base branch holds, from each path to its text."""

    files: dict[str, str] = field(default_factory=dict)
    repo: str = "example-org/example"
    working: bool = True
    proposals: list[dict] = field(default_factory=list)

    def propose(self, *, stem: str, changes: list[Change], title: str, body: str,
                request_id: str | None = None) -> str:
        for change in changes:
            if self.files.get(change.path) != change.old:
                raise Conflict(f"{change.path} has changed since this edit began")
        branch = next_branch(stem, [p["branch"] for p in self.proposals])
        self.proposals.append({"branch": branch, "changes": changes, "title": title, "body": body})
        return f"https://github.com/{self.repo}/pull/{len(self.proposals)}"

    def check(self) -> Check:
        return Check(PLUGIN, self.working, "" if self.working else "the fake is set to fail")
