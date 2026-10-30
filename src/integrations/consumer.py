"""A consuming repository's integrations.json: which plugins it uses and where.

The reusable workflow runs from a plain checkout with no packages installed,
so this checks the file's shape by hand. tests/ holds it to
schemas/consumer.schema.json, which is what editors and people read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

FILE = "integrations.json"
RUNS = {"laptop", "actions"}
KEYS = {"$schema", "uses", "terraform", "own_hosts", "other_hosts", "other_secrets", "exclude"}


class BadConsumer(Exception):
    """integrations.json is missing or not what it should be."""


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A path pattern as a regex: `**` crosses directories, `*` and `?` do
    not, and a pattern ending in `/` means everything under that directory."""
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


@dataclass
class Use:
    plugin: str
    paths: list[str]
    regexes: list[re.Pattern[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.regexes = [glob_regex(p) for p in self.paths]

    def covers(self, path: str) -> bool:
        return any(r.match(path) for r in self.regexes)


@dataclass
class Consumer:
    uses: list[Use]
    own_hosts: list[str]
    other_hosts: list[str]
    other_secrets: list[str]
    exclude: list[re.Pattern[str]]

    def excluded(self, path: str) -> bool:
        return any(r.match(path) for r in self.exclude)


def _strings(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise BadConsumer(f"{where} must be a list of non-empty strings")
    return value


def _reasoned(data: dict, key: str, name: str) -> list[dict]:
    """A list of {name: ..., "reason": ...}: nothing is left out unexplained."""
    entries = data.get(key, [])
    if not isinstance(entries, list):
        raise BadConsumer(f"{key} must be a list")
    for i, entry in enumerate(entries):
        if (not isinstance(entry, dict) or set(entry) != {name, "reason"}
                or not all(isinstance(v, str) and v for v in entry.values())):
            raise BadConsumer(f'{key}[{i}] must be {{"{name}": ..., "reason": ...}}')
    return entries


def parse(data: object, known: set[str]) -> Consumer:
    if not isinstance(data, dict):
        raise BadConsumer("it must be a JSON object")
    if unknown := set(data) - KEYS:
        raise BadConsumer(f"unknown keys: {', '.join(sorted(unknown))}")
    raw_uses = data.get("uses", [])
    if not isinstance(raw_uses, list):
        raise BadConsumer("uses must be a list")
    uses = []
    for i, use in enumerate(raw_uses):
        if not isinstance(use, dict) or set(use) != {"plugin", "paths"}:
            raise BadConsumer(f'uses[{i}] must be {{"plugin": ..., "paths": [...]}}')
        uses.append(Use(use["plugin"], _strings(use["paths"], f"uses[{i}].paths")))
    raw_stacks = data.get("terraform", [])
    if not isinstance(raw_stacks, list):
        raise BadConsumer("terraform must be a list")
    for i, stack in enumerate(raw_stacks):
        if not isinstance(stack, dict) or set(stack) != {"dir", "plugins", "runs"}:
            raise BadConsumer(f'terraform[{i}] must be {{"dir": ..., "plugins": [...], "runs": [...]}}')
        if not set(_strings(stack["runs"], f"terraform[{i}].runs")) <= RUNS:
            raise BadConsumer(f"terraform[{i}].runs may name only {', '.join(sorted(RUNS))}")
        where = stack["dir"].rstrip("/")
        pattern = "**" if where in ("", ".") else f"{where}/**"
        uses += [Use(p, [pattern]) for p in _strings(stack["plugins"], f"terraform[{i}].plugins")]
    for use in uses:
        if use.plugin not in known:
            raise BadConsumer(f"{use.plugin} is not a registered plugin")
    return Consumer(
        uses=uses,
        own_hosts=_strings(data.get("own_hosts", []), "own_hosts"),
        other_hosts=[e["host"] for e in _reasoned(data, "other_hosts", "host")],
        other_secrets=[e["name"] for e in _reasoned(data, "other_secrets", "name")],
        exclude=[glob_regex(e["path"]) for e in _reasoned(data, "exclude", "path")],
    )


def read(root: Path, known: set[str]) -> Consumer:
    path = root / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BadConsumer(f"{FILE} is missing: `integrations scan --suggest` drafts one") from None
    except ValueError as e:
        raise BadConsumer(f"it is not JSON: {e}") from None
    return parse(data, known)
