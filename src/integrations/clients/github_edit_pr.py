"""github.edit-pr: an edit made in a web editor becomes a pull request.

`EditPullRequests.propose` makes one commit per file on a new branch. It is refused with `Conflict`
when a file on the base branch is no longer what the edit started from, so
nobody's work is overwritten. The caller names the branch's stem; `propose` adds
one past the highest number taken.

`OneCommitProposer.propose` makes one commit with every change, as a person, on a
branch they name. It builds on the base's newest commit or on one they read from,
and answers rather than raises where nothing was wrong: "nothing to change", or
"stale" when a file isn't what they read.
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

from ..calls import Caller, Check, IntegrationError, Refused, Request, Response

PLUGIN = "github.edit-pr"
OPERATION = "open-pr"
API = "https://api.github.com"
# How many files are read from GitHub at once.
READERS = 8


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


class GitHubRefused(Refused):
    """GitHub said no. Its status and its words may go back to the person."""

    def __init__(self, operation: str, status: int, message: str):
        super().__init__(PLUGIN, operation, f"HTTP {status}: {message}")
        self.status, self.message = status, message


ENABLE_AUTO_MERGE = """mutation ($id: ID!) {
  enablePullRequestAutoMerge(input: { pullRequestId: $id, mergeMethod: MERGE }) { clientMutationId }
}"""


def _path(path: str) -> str:
    return quote(path, safe="/")


class _AsThePerson:
    """Calls GitHub with the token of the person who is asking."""

    operation = OPERATION

    def __init__(self, caller: Caller, token: Callable[[], str], request_id: str | None = None):
        self.caller, self.token, self.request_id = caller, token, request_id

    def _api(self, method: str, url: str, payload: dict | None = None, *, raw: bool = False) -> Response:
        headers = {"Authorization": f"Bearer {self.token()}", "X-GitHub-Api-Version": "2022-11-28",
                   "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        return self.caller.call(PLUGIN, self.operation, Request(method, f"{API}{url}", headers, body),
                                request_id=self.request_id)

    def _read(self, method: str, url: str, payload: dict | None = None) -> dict:
        response = self._api(method, url, payload)
        if not response.ok:
            self._refuse(response)
        return response.json()

    def _refuse(self, response: Response) -> None:
        detail = response.json() if response.body.strip().startswith(b"{") else {}
        raise GitHubRefused(self.operation, response.status, detail.get("message", "refused"))


class RepositoryReader(_AsThePerson):
    """What an editor reads before it proposes anything."""

    operation = "read"

    def snapshot(self, repo: str, branch: str | None = None) -> dict:
        """The newest commit on a branch, by default the default one, every file's
        path at it, and whether this token may write to the repository."""
        at = f"/repos/{repo}"
        about = self._read("GET", at)
        target = branch or about["default_branch"]
        commit = self._read("GET", f"{at}/git/ref/heads/{_path(target)}")["object"]["sha"]
        tree = self._read("GET", f"{at}/git/trees/{commit}?recursive=1")
        return {"branch": target, "commit": commit, "can_push": bool(about.get("permissions", {}).get("push")),
                "paths": [t["path"] for t in tree["tree"] if t["type"] == "blob"],
                "truncated": bool(tree.get("truncated"))}

    def files(self, repo: str, commit: str, paths: list[str]) -> dict[str, str | None]:
        """Each path's text at a commit, or None for a file that isn't there."""
        with ThreadPoolExecutor(max_workers=READERS) as pool:
            return dict(zip(paths, pool.map(lambda path: self._file(repo, commit, path), paths)))

    def _file(self, repo: str, commit: str, path: str) -> str | None:
        response = self._api("GET", f"/repos/{repo}/contents/{_path(path)}?ref={commit}", raw=True)
        if response.status == 404:
            return None
        if not response.ok:
            self._refuse(response)
        return response.body.decode("utf-8")


class OneCommitProposer(_AsThePerson):
    def _blob_sha(self, at: str, path: str, ref: str) -> str | None:
        """A file's blob sha at a commit, or None if it isn't there."""
        response = self._api("GET", f"{at}/contents/{_path(path)}?ref={ref}")
        if response.status == 404:
            return None
        if not response.ok:
            raise GitHubRefused(OPERATION, response.status, "could not read a file")
        return response.json()["sha"]

    def propose(self, *, repo: str, branch: str, title: str, changes: dict[str, str | None], body: str = "",
                base: str | None = None, message: str | None = None, parent: str | None = None,
                expect: dict[str, str | None] | None = None, auto_merge: bool = False) -> dict:
        if not changes:
            return {"status": "nothing to change"}
        at = f"/repos/{repo}"
        target = base or self._read("GET", at)["default_branch"]
        start = parent or self._read("GET", f"{at}/git/ref/heads/{_path(target)}")["object"]["sha"]
        base_tree = self._read("GET", f"{at}/git/commits/{start}")["tree"]["sha"]
        for path, sha in (expect or {}).items():
            if self._blob_sha(at, path, start) != sha:
                return {"status": "stale", "path": path}
        # GitHub refuses to delete a file that isn't there.
        tree = []
        for path, content in changes.items():
            if content is not None:
                tree.append({"path": path, "mode": "100644", "type": "blob", "content": content})
            elif self._blob_sha(at, path, start) is not None:
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        if not tree:
            return {"status": "nothing to change"}
        # Git names a tree by its content, so the same name means the same files.
        next_tree = self._read("POST", f"{at}/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
        if next_tree == base_tree:
            return {"status": "nothing to change"}
        commit = self._read("POST", f"{at}/git/commits", {
            "message": message or (f"{title}\n\n{body}" if body else title), "tree": next_tree, "parents": [start]})["sha"]
        self._read("POST", f"{at}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
        pull = self._read("POST", f"{at}/pulls", {"title": title, "head": branch, "base": target, "body": body})
        opened = {"status": "opened", "url": pull["html_url"], "number": pull["number"], "branch": branch,
                  "commit": commit}
        return {**opened, "autoMerge": self._merge_when_ready(at, pull)} if auto_merge else opened

    def _merge_when_ready(self, at: str, pull: dict) -> str:
        """Only the GraphQL API can turn auto-merge on. GitHub refuses when there
        is nothing left to wait for, and then the pull request can merge now. The
        pull request exists either way, so a refusal is the answer, not an error."""
        try:
            errors = self._read("POST", "/graphql", {"query": ENABLE_AUTO_MERGE,
                                                     "variables": {"id": pull["node_id"]}}).get("errors") or []
            if not errors:
                return "on"
            if "clean status" not in errors[0]["message"].lower():
                return errors[0]["message"]
            self._read("PUT", f"{at}/pulls/{pull['number']}/merge", {"merge_method": "merge"})
            return "merged"
        except IntegrationError as e:
            return e.detail


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
