"""GitHub's steps for a suite whose code calls GitHub through integrations: what a
scenario's repositories hold, made in the GitHub test org when recording and read
from the cassette when replaying. pubtest's harness (`pubtest record`) chooses the
mode and the cassette; these steps only say what the world holds.

- `Given the GitHub repository "o/r" holds:` + docstring (a JSON object from each
  file's path to its text). Replaying, it registers the repository under its own
  name: the code under test calls `o/r`, as the cassette does. Recording
  (INTEGRATIONS_MODE=record), it makes the private repository
  `rec-<consumer>-<run>-<n>` in the test org holding exactly those files, in one
  commit on its default branch (an empty object: an empty repository), and the
  code under test calls that: `repositories(ctx)["o/r"].recorded`.
- `Given someone else pushes to "BRANCH" of "o/r":` + docstring (each file's path
  to its new text, or null to delete it). Recording, it makes a real commit on
  BRANCH (made from the default branch when there is none), so the code under
  test meets the race for real; replaying, it does nothing, as the cassette holds
  what the code met.

What the steps themselves call when recording (making, pushing to and deleting the
test org's repositories, the recorder app's token) goes through a Caller of their
own, straight to GitHub (`network`), and is never in the cassette: a replay makes
none of it, and the code under test sees exactly the answers it was given when it
was recorded, the race among them.

Recording, the repositories' real names reach the Recorder through
INTEGRATIONS_NAMES (`integrations.transports`), which every program the scenario
runs inherits: `SustenautTest/rec-pubpr-1a2b-1` is written `o/r`, the repository
`r` and the org `o`, so the cassette and its replay only ever see the scenario's
names. A scenario's repositories are all in the one org, so they share one owner.
When the scenario ends, passed or failed, its repositories are deleted and
INTEGRATIONS_NAMES is as it was; a run cut short leaves them to the janitor
(`integrations test-org sweep`, the test-org workflow), which deletes `rec-*`
older than a day.

`token(ctx)` is the token the code under test calls GitHub with: recording, the
recorder app's installation token; replaying, a stand-in (a request's
Authorization is never recorded, nor matched).

The org is INTEGRATIONS_TEST_ORG, else SustenautTest; the recorder app's
credentials are GITHUB_TEST_RECORDER_APP_ID, GITHUB_TEST_RECORDER_APP_KEY and
GITHUB_TEST_RECORDER_INSTALLATION_ID (or GITHUB_TEST_RECORDER_TOKEN), which
./with-secrets exports from Bitwarden: recording without them stops at the first
step, naming them. The consumer is the repository under test's name (its origin's,
else its directory's), the run is PUBTEST_RUN, and n counts the run's repositories.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytest_bdd import given, parsers

from ..calls import Caller, Network, Transport
from ..clients.github_test_org import GitHubTestOrg, NameTaken, org_name, token_from_env
from ..transports import MARKER, MODE, NAMES, names_from

# The transport the steps' own calls go through when recording: the network, unless a
# suite's tests of recording put a stand-in here.
network: Transport | None = None
# A token's stand-in in replay: a request's Authorization is neither recorded nor matched.
REPLAY_TOKEN = "ghs_" + "0" * 36
_numbers = itertools.count(1)


@dataclass
class Repository:
    """A repository a scenario named (`name`), as the code under test calls it
    (`recorded`: the same name replaying, the test org's recording), its files, and,
    recording, its default branch and the commit its files are in."""

    name: str
    recorded: str
    files: dict[str, str] = field(default_factory=dict)
    branch: str | None = None
    head: str | None = None
    pushes: list[dict] = field(default_factory=list)


def recording_mode() -> bool:
    return os.environ.get(MODE) == "record"


def repositories(ctx) -> dict[str, Repository]:
    """The scenario's GitHub repositories, by the name its steps give them."""
    if getattr(ctx, "github_repositories", None) is None:
        ctx.github_repositories = {}
    return ctx.github_repositories


def _owner_and_name(name: str) -> tuple[str, str]:
    owner, slash, repo = name.partition("/")
    if not slash or not owner or not repo or "/" in repo:
        raise ValueError(f'"{name}" is no repository: a scenario names one "owner/name", such as "o/r"')
    return owner, repo


def _slug(text: str, limit: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "x"


def consumer_name(root: Path) -> str:
    """The repository under test's name: its origin's, else its checkout's directory's."""
    def git(*args: str) -> str:
        try:
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
                                  timeout=30)
        except OSError:
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    origin = git("remote", "get-url", "origin")
    if origin:
        return re.sub(r"\.git$", "", origin.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    top = git("rev-parse", "--show-toplevel")
    return Path(top).name if top else root.name


class Recording:
    """One scenario's repositories in the test org: made as its steps say, their names
    in INTEGRATIONS_NAMES, deleted by `close()`."""

    def __init__(self, org: GitHubTestOrg, consumer: str, run: str):
        self.org, self.consumer, self.run = org, _slug(consumer, 40), _slug(run, 16)
        self.made: list[str] = []
        self.names: dict[str, str] = {}
        self.owner: str | None = None
        self._before = os.environ.get(NAMES)

    def _name(self) -> str:
        return f"rec-{self.consumer}-{self.run}-{next(_numbers)}"

    def create(self, logical: str, files: dict[str, str]) -> Repository:
        owner, repo = _owner_and_name(logical)
        if self.owner is not None and owner != self.owner:
            raise ValueError(f'a scenario\'s repositories are all in the test org, so they share one owner: '
                             f'"{logical}" is not "{self.owner}/…"')
        for _ in range(100):
            name = self._name()
            try:
                self.made.append(name)  # deleted when the scenario ends, however far this gets
                branch, head = self.org.create(name, files)
                break
            except NameTaken:
                self.made.remove(name)  # another process's: the next number
        else:
            raise RuntimeError(f"the test org has every name rec-{self.consumer}-{self.run}-N tried")
        self.owner = owner
        real = f"{self.org.org}/{name}"
        self.names.update({real: logical, name: repo, self.org.org: owner})
        os.environ[NAMES] = json.dumps({**names_from(), **self.names})
        return Repository(logical, real, dict(files), branch, head)

    def push(self, repository: Repository, branch: str, files: dict[str, str | None]) -> str:
        return self.org.push(repository.recorded.split("/", 1)[1], branch, files)

    def close(self) -> None:
        """Delete what the scenario made, and put INTEGRATIONS_NAMES back."""
        errors: list[str] = []
        for name in reversed(self.made):
            try:
                self.org.delete(name)
            except Exception as e:  # noqa: BLE001 - every repository is tried; the janitor takes what is left
                errors.append(f"{self.org.org}/{name}: {e}")
        self.made.clear()
        if self._before is None:
            os.environ.pop(NAMES, None)
        else:
            os.environ[NAMES] = self._before
        if errors:
            raise RuntimeError("the test org's repositories were not all deleted (the janitor will): "
                               + "; ".join(errors))


def recording(ctx, request) -> Recording:
    """The scenario's recording in the test org, made at its first step that needs it
    and closed when the scenario ends, passed or failed."""
    found = getattr(ctx, "github_recording", None)
    if found is not None:
        return found
    consumer = consumer_name(Path(str(request.config.rootpath)))
    caller = Caller(consumer, "pubtest record", transport=network if network is not None else Network())
    org = GitHubTestOrg(caller, token_from_env(caller), org_name())
    found = Recording(org, consumer, os.environ.get(MARKER) or "run")
    ctx.github_recording = found
    request.addfinalizer(found.close)
    return found


def token(ctx) -> str:
    """The token the code under test calls GitHub with: the recorder app's when
    recording, a stand-in replaying."""
    if not recording_mode():
        return REPLAY_TOKEN
    found = getattr(ctx, "github_recording", None)
    if found is None:
        raise LookupError("recording, the token is the recorder app's: a step says what a GitHub repository "
                          "holds first")
    return found.org.token()


def _files(name: str, files: Any, *, deleting: bool) -> dict:
    allowed = (str, type(None)) if deleting else (str,)
    if not isinstance(files, dict) or not all(isinstance(v, allowed) for v in files.values()):
        what = "its text, or null to delete it" if deleting else "its text"
        raise ValueError(f"{name}'s files are a JSON object from each file's path to {what}")
    return files


def hold(ctx, name: str, files: dict[str, str], request: Any = None) -> Repository:
    """Registers the repository `name` holding `files`: replaying, under its own name;
    recording, made in the test org (which needs pytest's `request`)."""
    _owner_and_name(name)
    files = _files(name, files, deleting=False)
    if recording_mode():
        if request is None:
            raise ValueError("recording a GitHub repository needs the scenario's pytest request")
        repository = recording(ctx, request).create(name, files)
    else:
        repository = Repository(name, name, dict(files))
    repositories(ctx)[name] = repository
    return repository


def push(ctx, name: str, branch: str, files: dict[str, str | None], request: Any = None) -> Repository:
    """Someone else's commit on `branch` of `name`: made for real when recording,
    nothing more than noted when replaying."""
    repository = repositories(ctx).get(name)
    if repository is None:
        raise LookupError(f'no GitHub repository "{name}": a step says what it holds first')
    files = _files(name, files, deleting=True)
    if recording_mode():
        commit = recording(ctx, request).push(repository, branch, files)
        repository.pushes.append({"branch": branch, "files": dict(files), "commit": commit})
    else:
        repository.pushes.append({"branch": branch, "files": dict(files)})
    return repository


@given(parsers.parse('the GitHub repository "{name}" holds:'))
def repository_holds(ctx, request, name, docstring):
    hold(ctx, name, json.loads(docstring), request)


@given(parsers.parse('someone else pushes to "{branch}" of "{name}":'))
def someone_else_pushes(ctx, request, branch, name, docstring):
    push(ctx, name, branch, json.loads(docstring), request)
