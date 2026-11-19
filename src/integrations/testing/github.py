"""GitHub's steps for a suite whose code calls GitHub through integrations: what
a scenario's repositories hold, set up in the GitHub test org when recording and
read from the cassette when replaying. pubtest's harness (`pubtest record`)
chooses the mode and the cassette; these steps only say what the world holds.

A stub in this release. What works:

- `Given the GitHub repository "o/r" holds:` + docstring (a JSON object from each
  file's path to its text) registers the repository: its name maps to itself, the
  name the replayed calls use, and its files are kept for the scenario
  (`repositories(ctx)`).

What does not yet: in record mode (INTEGRATIONS_MODE=record) the same step would
create the repository in the GitHub test org, under a name of its own, and map
"o/r" to it. That needs the test org and its recorder app, so it raises
`NotImplementedError("needs the GitHub test org: a later release")`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from pytest_bdd import given, parsers

from ..transports import MODE

LATER = "needs the GitHub test org: a later release"


@dataclass
class Repository:
    """A repository a scenario named, as its calls name it (`recorded`), and its files."""

    name: str
    recorded: str
    files: dict[str, str] = field(default_factory=dict)


def repositories(ctx) -> dict[str, Repository]:
    """The scenario's GitHub repositories, by the name its steps give them."""
    if getattr(ctx, "github_repositories", None) is None:
        ctx.github_repositories = {}
    return ctx.github_repositories


def hold(ctx, name: str, files: dict[str, str]) -> Repository:
    """Registers the repository `name` holding `files`: in replay, under its own name."""
    if os.environ.get(MODE) == "record":
        raise NotImplementedError(LATER)
    if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
        raise ValueError(f"{name} holds a JSON object from each file's path to its text")
    repository = Repository(name, name, dict(files))
    repositories(ctx)[name] = repository
    return repository


@given(parsers.parse('the GitHub repository "{name}" holds:'))
def repository_holds(ctx, name, docstring):
    hold(ctx, name, json.loads(docstring))
