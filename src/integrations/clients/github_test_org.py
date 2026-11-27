"""The GitHub test org: the repositories a recording makes there (`integrations.testing.github`),
and the janitor that deletes those a run left behind (`integrations test-org sweep`).

Every repository a recording makes is named `rec-<consumer>-<run>-<process>-<n>` and is
private; a run deletes its own when each scenario ends, and the janitor deletes any `rec-*`
older than a day. The calls are github.repo-ops' `test-org` (a recording's) and
`test-org-sweep` (the janitor's), with the recorder app's installation token
(github.app-token), through a Caller given its own transport: none of them is ever
recorded into a cassette.

The org is INTEGRATIONS_TEST_ORG, else SustenautTest. The token is
GITHUB_TEST_RECORDER_TOKEN when it is set (an installation token, as a workflow's
actions/create-github-app-token mints), else minted from GITHUB_TEST_RECORDER_CLIENT_ID,
GITHUB_TEST_RECORDER_APP_KEY and GITHUB_TEST_RECORDER_INSTALLATION_ID, which
./with-secrets exports from Bitwarden's github-test-recorder-* items. The client id
(Iv, then letters, digits and .) is the JWT's issuer, which GitHub takes in place of
the app's id.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

from ..calls import Caller, Network, Refused, Request, Response
from .github_app_token import AppToken

PLUGIN = "github.repo-ops"
SET_UP = "test-org"
SWEEP = "test-org-sweep"
API = "https://api.github.com"
ORG = "INTEGRATIONS_TEST_ORG"
DEFAULT_ORG = "SustenautTest"
TOKEN = "GITHUB_TEST_RECORDER_TOKEN"
CLIENT_ID = "GITHUB_TEST_RECORDER_CLIENT_ID"
APP_KEY = "GITHUB_TEST_RECORDER_APP_KEY"
INSTALLATION_ID = "GITHUB_TEST_RECORDER_INSTALLATION_ID"
# What a GitHub App's client id looks like, as pubsec app-secrets checks it.
CLIENT_ID_SHAPE = re.compile(r"Iv[A-Za-z0-9.]{4,}")
PREFIX = "rec-"
DESCRIPTION = "A recording's repository: its run deletes it, and the janitor any left after a day"
# A repository GitHub has just made may not have its first branch yet: asked again this
# many times, a second apart.
SETTLE_TRIES = 10


class MissingCredentials(RuntimeError):
    """The recorder app's credentials are not in the environment, or its client id is not one."""


def org_name(env: dict[str, str] | None = None) -> str:
    """The test org: INTEGRATIONS_TEST_ORG, else SustenautTest."""
    env = os.environ if env is None else env
    return env.get(ORG) or DEFAULT_ORG


def token_from_env(caller: Caller, env: dict[str, str] | None = None) -> Callable[[], str]:
    """The recorder app's installation token: GITHUB_TEST_RECORDER_TOKEN, else one minted
    from its client id (the JWT's issuer), key and installation id. Raises
    `MissingCredentials` naming those not set, or saying the client id is not one,
    without its value."""
    env = os.environ if env is None else env
    if env.get(TOKEN):
        token = env[TOKEN]
        return lambda: token
    missing = [name for name in (CLIENT_ID, APP_KEY, INSTALLATION_ID) if not env.get(name)]
    if missing:
        raise MissingCredentials(
            f"the GitHub test org needs the recorder app's credentials, and {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set: run it through ./with-secrets, which exports "
            f"{CLIENT_ID}, {APP_KEY} and {INSTALLATION_ID} from Bitwarden's github-test-recorder-* items "
            f"(or set {TOKEN} to an installation token)")
    if not (env[CLIENT_ID].isascii() and CLIENT_ID_SHAPE.fullmatch(env[CLIENT_ID])):
        raise MissingCredentials(
            f"{CLIENT_ID} is not a client id: it starts Iv, then letters, digits and . "
            f"(the app's client id, not its app id)")
    return AppToken(caller, env[CLIENT_ID], env[APP_KEY], env[INSTALLATION_ID], clock=time.time)


class GitHubTestOrg:
    """The test org's repositories, through `caller` (given its own transport) with
    `token`. `operation` is the github.repo-ops operation its calls are: `test-org` for
    a recording, `test-org-sweep` for the janitor."""

    def __init__(self, caller: Caller, token: Callable[[], str], org: str = DEFAULT_ORG, *,
                 operation: str = SET_UP, sleep: Callable[[float], None] | None = None):
        self.caller, self.token, self.org, self.operation, self._sleep = caller, token, org, operation, sleep

    def sleep(self, seconds: float) -> None:
        """The wait for a new repository's first branch: the one given, else the Caller's
        (its clock and sleep are looked up at each call, so a harness's `use_clock()`
        reaches it)."""
        (self._sleep or self.caller.sleep)(seconds)

    # --- calls ---------------------------------------------------------------------

    def _call(self, method: str, path: str, body: Any = None) -> Response:
        headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        return self.caller.call(PLUGIN, self.operation, Request(method, f"{API}{path}", headers, data))

    def _ok(self, response: Response, doing: str) -> Any:
        if not response.ok:
            raise Refused(PLUGIN, self.operation, f"{doing}: HTTP {response.status}{_message(response)}")
        return response.json() if response.body else None

    def _repo(self, name: str) -> str:
        return f"/repos/{quote(self.org)}/{quote(name)}"

    def _ref(self, name: str, branch: str) -> str | None:
        """The commit `branch` points at, or None when there is no such branch."""
        response = self._call("GET", f"{self._repo(name)}/git/ref/heads/{quote(branch, safe='/')}")
        if response.status in (404, 409):  # 409: the repository is empty
            return None
        return self._ok(response, f"reading {branch} of {self.org}/{name}")["object"]["sha"]

    def _settled_ref(self, name: str, branch: str) -> str:
        """The commit a new repository's first branch points at, once GitHub has made it."""
        for _ in range(SETTLE_TRIES):
            sha = self._ref(name, branch)
            if sha is not None:
                return sha
            self.sleep(1)
        raise Refused(PLUGIN, self.operation, f"{self.org}/{name} has no {branch} after {SETTLE_TRIES} s")

    def _commit(self, name: str, entries: list[dict], message: str, parents: list[str],
                base_tree: str | None = None) -> str:
        tree_body: dict[str, Any] = {"tree": entries}
        if base_tree:
            tree_body["base_tree"] = base_tree
        tree = self._ok(self._call("POST", f"{self._repo(name)}/git/trees", tree_body),
                        f"writing a tree in {self.org}/{name}")["sha"]
        return self._ok(self._call("POST", f"{self._repo(name)}/git/commits",
                                   {"message": message, "tree": tree, "parents": parents}),
                        f"writing a commit in {self.org}/{name}")["sha"]

    # --- what a recording does ---------------------------------------------------------

    def create(self, name: str, files: dict[str, str]) -> tuple[str, str | None]:
        """Make the private repository `name` holding exactly `files` (path -> text) in
        one commit on its default branch, or empty when there are none. Returns the
        default branch and its commit (None for an empty repository). Raises
        `NameTaken` when the org has a repository of that name already."""
        response = self._call("POST", f"/orgs/{quote(self.org)}/repos",
                              {"name": name, "private": True, "auto_init": bool(files),
                               "description": DESCRIPTION})
        if response.status == 422 and b"already exists" in response.body:
            raise NameTaken(name)
        made = self._ok(response, f"making {self.org}/{name}")
        branch = made.get("default_branch") or "main"
        if not files:
            return branch, None
        first = self._settled_ref(name, branch)
        commit = self._commit(name, [_blob(path, text) for path, text in sorted(files.items())],
                              "What the scenario's repository holds", [])
        self._ok(self._call("PATCH", f"{self._repo(name)}/git/refs/heads/{quote(branch, safe='/')}",
                            {"sha": commit, "force": True}),
                 f"pointing {branch} of {self.org}/{name} at its files (from {first})")
        return branch, commit

    def push(self, name: str, branch: str, files: dict[str, str | None]) -> str:
        """Someone else's commit on `branch`: each file of `files` set to its text, or
        deleted where it is None. A branch that does not exist is made from the default
        branch. Returns the commit."""
        head = self._ref(name, branch)
        made = head is None
        if made:
            default = self._ok(self._call("GET", self._repo(name)), f"reading {self.org}/{name}")["default_branch"]
            head = self._ref(name, default) if default != branch else None
        base_tree = None
        if head is not None:
            base_tree = self._ok(self._call("GET", f"{self._repo(name)}/git/commits/{head}"),
                                 f"reading {head} in {self.org}/{name}")["tree"]["sha"]
        entries = [_blob(path, text) for path, text in sorted(files.items())]
        commit = self._commit(name, entries, "Someone else's commit", [head] if head else [], base_tree)
        if made:
            self._ok(self._call("POST", f"{self._repo(name)}/git/refs",
                                {"ref": f"refs/heads/{branch}", "sha": commit}),
                     f"making {branch} of {self.org}/{name}")
        else:
            self._ok(self._call("PATCH", f"{self._repo(name)}/git/refs/heads/{quote(branch, safe='/')}",
                                {"sha": commit, "force": False}),
                     f"pushing to {branch} of {self.org}/{name}")
        return commit

    def delete(self, name: str) -> bool:
        """Delete the repository `name`; False when it was not there."""
        response = self._call("DELETE", self._repo(name))
        if response.status == 404:
            return False
        self._ok(response, f"deleting {self.org}/{name}")
        return True

    # --- the janitor -------------------------------------------------------------------

    def repositories(self) -> list[dict]:
        """Every repository in the org, as GitHub lists them."""
        found: list[dict] = []
        page = 1
        while True:
            batch = self._ok(self._call("GET", f"/orgs/{quote(self.org)}/repos?type=all&per_page=100&page={page}"),
                             f"listing {self.org}'s repositories")
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    def sweep(self, older_than: timedelta = timedelta(days=1), now: datetime | None = None) -> dict:
        """Delete every `rec-*` repository made more than `older_than` ago. Returns the
        names deleted and the `rec-*` ones kept, as they were too new."""
        now = now or datetime.now(timezone.utc)
        deleted, kept = [], []
        for repo in sorted(self.repositories(), key=lambda r: r["name"]):
            if not repo["name"].startswith(PREFIX):
                continue
            made = datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
            if now - made <= older_than:
                kept.append(repo["name"])
            elif self.delete(repo["name"]):
                deleted.append(repo["name"])
        return {"org": self.org, "deleted": deleted, "kept": kept}


class NameTaken(Refused):
    """The org has a repository of that name already."""

    def __init__(self, name: str):
        super().__init__(PLUGIN, SET_UP, f"the test org has a repository {name} already")
        self.name = name


def _blob(path: str, text: str | None) -> dict:
    if text is None:
        return {"path": path, "mode": "100644", "type": "blob", "sha": None}
    return {"path": path, "mode": "100644", "type": "blob", "content": text}


def _message(response: Response) -> str:
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        return ""
    return f": {message}" if message else ""


def sweep_from_env(older_than: timedelta, *, org: str | None = None, caller: Caller | None = None,
                   env: dict[str, str] | None = None, now: datetime | None = None) -> dict:
    """The janitor, with the org and token the environment gives."""
    caller = caller or Caller(os.environ.get("GITHUB_REPOSITORY", "Sustenaut/integrations"), "test-org-janitor",
                              transport=Network())
    the_org = GitHubTestOrg(caller, token_from_env(caller, env), org or org_name(env), operation=SWEEP)
    return the_org.sweep(older_than, now)
