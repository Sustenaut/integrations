"""The registry and the consumer format, held to their JSON schemas."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
from hypothesis import given
from hypothesis import strategies as st

from integrations import registry
from integrations.consumer import BadConsumer, glob_regex, parse
from integrations.scan import compile_signature, host_regex
from pytest_bdd import scenarios, then

scenarios("registry.feature")

HERE = Path(__file__).resolve().parent.parent
PLUGIN_FILES = sorted(registry.PLUGINS.glob("*.json"))
PLUGIN_SCHEMA = registry.load(registry.SCHEMAS / "plugin.schema.json")
CONSUMER_SCHEMA = registry.load(registry.SCHEMAS / "consumer.schema.json")
KNOWN = {"linear.sync", "inwx.registrar"}


@then("plugin meets the schema")
def plugin_meets_the_schema():
    """Every case of the parametrized test this was, in turn."""
    for path in PLUGIN_FILES:
        path = path.values[0] if type(path).__name__ == "ParameterSet" else path
        _case_plugin_meets_the_schema(path=path)


def _case_plugin_meets_the_schema(path):
    data = registry.load(path)
    jsonschema.validate(data, PLUGIN_SCHEMA)
    assert data["id"] == path.stem


@then("every signature compiles")
def every_signature_compiles():
    """Every case of the parametrized test this was, in turn."""
    for path in PLUGIN_FILES:
        path = path.values[0] if type(path).__name__ == "ParameterSet" else path
        _case_every_signature_compiles(path=path)


def _case_every_signature_compiles(path):
    for kind, texts in registry.load(path)["detect"].items():
        if kind != "files":
            for text in texts:
                compile_signature(kind, text)


@then("a plugin with no signatures must be manual only")
def a_plugin_with_no_signatures_must_be_manual_only():
    data = registry.load(registry.PLUGINS / "linear.sync.json") | {"detect": {}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, PLUGIN_SCHEMA)


@then("every inventory row has its plugins and every plugin a row")
def every_inventory_row_has_its_plugins_and_every_plugin_a_row():
    inventory = json.loads((HERE / "inventory.json").read_text())
    rows = inventory["rows"]
    assert len(rows) == 33
    listed = [p for ids in rows.values() for p in ids] + inventory["found_by_the_scan"]
    assert sorted(listed) == sorted(registry.plugins())


@then("every class has its targets")
def every_class_has_its_targets():
    classes = registry.classes()
    assert set(classes) == {"H", "E", "S", "M"}
    for target in classes.values():
        assert {"response_p95", "uptime", "on_failure"} <= set(target)
        # How a Caller calls, in Python and in clients/js/caller.js.
        assert target["timeout"] > 0 and isinstance(target["tries"], int) and target["tries"] >= 1


@then("the registry names no consumer")
def the_registry_names_no_consumer():
    """Consumers name themselves, in their own repositories."""
    consumers = re.compile(r"sustenaut|schryer|opportunity|visit-ee", re.IGNORECASE)
    for path in [*PLUGIN_FILES, registry.HERE / "not-providers.json"]:
        assert not consumers.search(path.read_text()), path


GOOD = [
    {},
    {"uses": [{"plugin": "linear.sync", "paths": ["tools/**"]}]},
    {"uses": [{"plugin": "inwx.registrar", "paths": []}]},
    {"uses": [{"plugin": "linear.sync", "paths": [], "mode": "advisory"}]},
    {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": [], "mode": "required"}]},
    {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": ["laptop", "actions"]}]},
    {"own_hosts": ["mine.example"], "other_hosts": [{"host": "a.example", "reason": "why"}],
     "other_secrets": [{"name": "A", "reason": "why"}], "exclude": [{"path": "tests/**", "reason": "why"}]},
]
BAD = [
    [],
    {"nope": []},
    {"uses": {}},
    {"uses": [{"plugin": "linear.sync"}]},
    {"uses": [{"plugin": "linear.sync", "paths": [""]}]},
    {"uses": [{"plugin": "linear.sync", "paths": [], "extra": 1}]},
    {"uses": [{"plugin": "linear.sync", "paths": [], "mode": "optional"}]},
    {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": [], "mode": "optional"}]},
    {"terraform": {}},
    {"terraform": [{"dir": "infra", "plugins": ["linear.sync"]}]},
    {"terraform": [{"dir": "infra", "plugins": ["linear.sync"], "runs": ["cloud"]}]},
    {"own_hosts": "mine.example"},
    {"other_hosts": {}},
    {"other_hosts": [{"host": "a.example"}]},
    {"other_secrets": [{"name": "A", "reason": ""}]},
    {"exclude": [{"path": "tests/**"}]},
]


@then("the loader and the schema accept the same")
def the_loader_and_the_schema_accept_the_same():
    """Every case of the parametrized test this was, in turn."""
    for data in GOOD:
        data = data.values[0] if type(data).__name__ == "ParameterSet" else data
        _case_the_loader_and_the_schema_accept_the_same(data=data)


def _case_the_loader_and_the_schema_accept_the_same(data):
    jsonschema.validate(data, CONSUMER_SCHEMA)
    parse(data, KNOWN)


@then("the loader and the schema refuse the same")
def the_loader_and_the_schema_refuse_the_same():
    """Every case of the parametrized test this was, in turn."""
    for data in BAD:
        data = data.values[0] if type(data).__name__ == "ParameterSet" else data
        _case_the_loader_and_the_schema_refuse_the_same(data=data)


def _case_the_loader_and_the_schema_refuse_the_same(data):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, CONSUMER_SCHEMA)
    with pytest.raises(BadConsumer):
        parse(data, KNOWN)


@then("path patterns")
def path_patterns():
    """Every case of the parametrized test this was, in turn."""
    for case0 in [
    ("a.py", "a.py", True),
    ("a.py", "b/a.py", False),
    ("tests/", "tests/x/y.py", True),
    ("tests/**", "tests/y.py", True),
    ("tests/**", "testsx/y.py", False),
    ("**/*.py", "a.py", True),
    ("**/*.py", "b/c/a.py", True),
    ("*.py", "b/a.py", False),
    ("a?.py", "ab.py", True),
]:
        case0 = case0.values if type(case0).__name__ == "ParameterSet" else case0
        _case_path_patterns(pattern=case0[0], path=case0[1], matches=case0[2])


def _case_path_patterns(pattern, path, matches):
    assert bool(glob_regex(pattern).match(path)) is matches


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
