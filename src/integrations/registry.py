"""The registry: one JSON file per plugin, the classes their operations
belong to, and the hosts that are not providers.

A file whose name starts with `_` is not a plugin: it holds record rules that
plugins share through `"record": {"extends": "_name"}` (`record()`)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
SCHEMAS = HERE / "schemas"


@dataclass(frozen=True)
class Plugin:
    id: str
    provider: str
    use: str
    operations: tuple[dict[str, str], ...]
    setup: tuple[str, ...]
    detect: dict[str, list[str]]

    @property
    def manual(self) -> bool:
        """A plugin with no signatures: nothing in code shows it is used."""
        return not any(self.detect.values())


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def plugins() -> dict[str, Plugin]:
    found = {}
    for path in plugin_files():
        data = load(path)
        found[data["id"]] = Plugin(data["id"], data["provider"], data["use"],
                                   tuple(data["operations"]), tuple(data["setup"]),
                                   data["detect"])
    return found


def plugin_files() -> list[Path]:
    """The plugins' files, without the shared ones (`_name.json`)."""
    return [p for p in sorted(PLUGINS.glob("*.json")) if not p.name.startswith("_")]


# What a plugin with no record block records: nothing but the default secrets.
SECRET_HEADERS = ("Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie")
MATCH = {"url": "scheme+host+path+sorted-query", "body": "json", "graphql": None}


def _merge(base: dict, extra: dict) -> dict:
    """`extra` over `base`, shallow per field: lists are joined, objects merged,
    anything else replaced."""
    merged = dict(base)
    for key, value in extra.items():
        if key == "extends":
            continue
        old = merged.get(key)
        if isinstance(old, list) and isinstance(value, list):
            merged[key] = old + [v for v in value if v not in old]
        elif isinstance(old, dict) and isinstance(value, dict):
            merged[key] = _merge(old, value)
        else:
            merged[key] = value
    return merged


def _record_block(name: str, seen: tuple[str, ...] = ()) -> dict:
    if name in seen:
        raise ValueError(f"record rules extend themselves: {' -> '.join((*seen, name))}")
    path = PLUGINS / f"{name}.json"
    if not path.exists():
        raise ValueError(f"no plugin or shared record rules {name}")
    block = load(path).get("record") or {}
    if "extends" in block:
        return _merge(_record_block(block["extends"], (*seen, name)), block)
    return block


def record(plugin_id: str | None) -> dict:
    """The record rules of `plugin_id`, its `extends` merged, every field there:
    `keep_headers`, `volatile` (`headers`, `json`, `values`), `secret`
    (`headers`, `json`, `query`), `match` (`url`, `body`, `graphql`) and
    `customers`. A plugin with no record block, or no plugin, gets the defaults:
    nothing kept, nothing volatile, the usual credential headers secret."""
    rules = {"keep_headers": [], "volatile": {"headers": [], "json": [], "values": {}},
             "secret": {"headers": list(SECRET_HEADERS), "json": [], "query": []},
             "match": dict(MATCH), "customers": None}
    if plugin_id and not plugin_id.startswith("_") and (PLUGINS / f"{plugin_id}.json").exists():
        rules = _merge(rules, _record_block(plugin_id))
    return rules


def classes() -> dict[str, dict[str, str]]:
    return load(HERE / "classes.json")


def not_providers() -> dict[str, list[str]]:
    """What the scan never counts as an unregistered provider."""
    return load(HERE / "not-providers.json")
