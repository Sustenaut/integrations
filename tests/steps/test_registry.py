"""The registry and the consumer format, held to their JSON schemas."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import jsonschema
from hypothesis import given
from hypothesis import strategies as st

from integrations import registry
from integrations.consumer import BadConsumer, glob_regex, parse
from integrations.scan import compile_signature, host_regex
from pytest_bdd import given as a_given, parsers, scenarios, when

scenarios("registry.feature")

HERE = Path(__file__).resolve().parent.parent
PLUGIN_FILES = sorted(registry.PLUGINS.glob("*.json"))
PLUGIN_SCHEMA = registry.load(registry.SCHEMAS / "plugin.schema.json")
CONSUMER_SCHEMA = registry.load(registry.SCHEMAS / "consumer.schema.json")
KNOWN = {"linear.sync", "inwx.registrar"}
CONSUMERS = re.compile(r"sustenaut|schryer|opportunity|visit-ee", re.IGNORECASE)


def failures(path, check):
    try:
        check(path)
    except Exception as e:  # noqa: BLE001 - each failure is reported by its file
        return [f"{path.name}: {e}"]
    return []


def meets_schema(path):
    data = registry.load(path)
    jsonschema.validate(data, PLUGIN_SCHEMA)
    assert data["id"] == path.stem, f"its id is {data['id']}"


def compiles(path):
    for kind, texts in registry.load(path)["detect"].items():
        if kind != "files":
            for text in texts:
                compile_signature(kind, text)


@when("I hold every plugin to the plugin schema")
def every_plugin_schema(ctx):
    ctx.attempt(lambda: [f for path in PLUGIN_FILES for f in failures(path, meets_schema)])


@when("I compile every plugin's signatures")
def every_signature(ctx):
    ctx.attempt(lambda: [f for path in PLUGIN_FILES for f in failures(path, compiles)])


@a_given(parsers.parse('the plugin "{plugin}" with no signatures'))
def plugin_without_signatures(ctx, plugin):
    ctx.plugin = registry.load(registry.PLUGINS / f"{plugin}.json") | {"detect": {}}


@when("I hold it to the plugin schema")
def hold_to_schema(ctx):
    ctx.attempt(lambda: jsonschema.validate(ctx.plugin, PLUGIN_SCHEMA))


@when("I compare the inventory with the registry")
def compare_inventory(ctx):
    def compare():
        inventory = json.loads((HERE / "inventory.json").read_text())
        listed = Counter([p for ids in inventory["rows"].values() for p in ids] + inventory["found_by_the_scan"])
        plugins = Counter(list(registry.plugins()))
        return {"rows": len(inventory["rows"]), "not listed": sorted((plugins - listed).elements()),
                "not plugins": sorted((listed - plugins).elements())}
    ctx.attempt(compare)


@when("I read the classes")
def read_classes(ctx):
    ctx.attempt(lambda: sorted(registry.classes()))


@when(parsers.parse('I read the class "{name}"'))
def read_class(ctx, name):
    ctx.attempt(lambda: registry.classes()[name])


@when("I look for a consumer's name in the plugins and not-providers.json")
def consumer_names(ctx):
    paths = [*PLUGIN_FILES, registry.HERE / "not-providers.json"]
    ctx.attempt(lambda: [p.name for p in paths if CONSUMERS.search(p.read_text())])


@when(parsers.parse("the schema and the loader read '{data}'"))
def schema_and_loader(ctx, data):
    def read():
        parsed = json.loads(data)
        said = {}
        try:
            jsonschema.validate(parsed, CONSUMER_SCHEMA)
            said["schema"] = "accepted"
        except jsonschema.ValidationError:
            said["schema"] = "refused"
        try:
            parse(parsed, KNOWN)
            said["loader"] = "accepted"
        except BadConsumer:
            said["loader"] = "refused"
        return said
    ctx.attempt(read)


@when(parsers.parse('I match "{path}" against the path pattern "{pattern}"'))
def match_pattern(ctx, path, pattern):
    ctx.attempt(lambda: bool(glob_regex(pattern).match(path)))


LABEL = st.from_regex(r"[a-z][a-z0-9-]{0,8}[a-z0-9]", fullmatch=True)


@given(LABEL, LABEL)
def test_a_host_is_found_only_as_a_whole_name(prefix, name):
    host = f"{name}.example"
    regex = re.compile(host_regex(host))
    assert regex.search(f"https://{host}/x")
    assert not regex.search(f"https://{prefix}{host}/x")
    assert not regex.search(f"https://{prefix}.{host}/x")
    assert not regex.search(f"https://{host}.evil/x")
    assert re.compile(host_regex(f"*.{host}")).search(f"https://{prefix}.{host}/x")
