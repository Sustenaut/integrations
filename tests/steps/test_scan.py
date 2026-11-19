"""The repository `integrations scan` reads: its integrations.json, in the
scratch directory. The scan itself is pubtest's `I run`, of bin/integrations
from INTEGRATIONS_BIN_DIR."""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, scenarios

scenarios("../features/scan.feature")


def declare(ctx, **fields) -> None:
    """Add to the scratch directory's integrations.json."""
    path = ctx.directory / "integrations.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    for key, value in fields.items():
        data[key] = data.get(key, []) + value
    path.write_text(json.dumps(data), encoding="utf-8")


@given("its integrations.json declares nothing")
def declares_nothing(ctx):
    declare(ctx)


@given(parsers.parse('its integrations.json declares "{plugin}" in "{paths}"'))
def declares(ctx, plugin, paths):
    declare(ctx, uses=[{"plugin": plugin, "paths": paths.split(",")}])


@given(parsers.parse('its integrations.json declares "{plugin}" with no paths'))
def declares_no_paths(ctx, plugin):
    declare(ctx, uses=[{"plugin": plugin, "paths": []}])


@given(parsers.parse('its integrations.json declares "{plugin}" in "{paths}" as advisory'))
def declares_advisory(ctx, plugin, paths):
    declare(ctx, uses=[{"plugin": plugin, "paths": paths.split(","), "mode": "advisory"}])


@given(parsers.parse('its integrations.json declares "{plugin}" as advisory with no paths'))
def declares_advisory_no_paths(ctx, plugin):
    declare(ctx, uses=[{"plugin": plugin, "paths": [], "mode": "advisory"}])


@given(parsers.parse('its integrations.json has an advisory stack "{where}" with "{plugin}"'))
def advisory_stack(ctx, where, plugin):
    declare(ctx, terraform=[{"dir": where, "plugins": [plugin], "runs": [], "mode": "advisory"}])


@given(parsers.parse('its integrations.json lists "{key}" entry "{name}" "{value}"'))
def lists(ctx, key, name, value):
    declare(ctx, **{key: [{name: value, "reason": "because"}]})


@given(parsers.parse('its integrations.json owns "{host}"'))
def owns(ctx, host):
    declare(ctx, own_hosts=[host])


@given(parsers.parse('its integrations.json has a stack "{where}" with "{plugin}"'))
def stack(ctx, where, plugin):
    declare(ctx, terraform=[{"dir": where, "plugins": [plugin], "runs": ["laptop"]}])
