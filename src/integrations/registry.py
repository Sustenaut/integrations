"""The registry: one JSON file per plugin, the classes their operations
belong to, and the hosts that are not providers."""

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
    for path in sorted(PLUGINS.glob("*.json")):
        data = load(path)
        found[data["id"]] = Plugin(data["id"], data["provider"], data["use"],
                                   tuple(data["operations"]), tuple(data["setup"]),
                                   data["detect"])
    return found


def classes() -> dict[str, dict[str, str]]:
    return load(HERE / "classes.json")


def not_providers() -> dict[str, list[str]]:
    """What the scan never counts as an unregistered provider."""
    return load(HERE / "not-providers.json")
