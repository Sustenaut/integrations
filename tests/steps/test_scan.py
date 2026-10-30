"""Steps driving `bin/integrations` as a user would, from INTEGRATIONS_BIN_DIR."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, when

scenarios("../features/scan.feature")


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.replace("\\n", "\n").replace('\\"', '"') + "\n", encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    return root


def declare(root: Path, **fields) -> None:
    path = root / "integrations.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    for key, value in fields.items():
        data[key] = data.get(key, []) + value
    path.write_text(json.dumps(data), encoding="utf-8")


@given(parsers.re(r'a repository with "(?P<path>[^"]+)" containing "(?P<content>.*)"'))
@given(parsers.re(r'the repository has "(?P<path>[^"]+)" containing "(?P<content>.*)"'))
def repository_file(repository, path, content):
    write(repository, path, content)


@given("its integrations.json declares nothing")
def declares_nothing(repository):
    declare(repository)


@given(parsers.parse('its integrations.json declares "{plugin}" in "{paths}"'))
def declares(repository, plugin, paths):
    declare(repository, uses=[{"plugin": plugin, "paths": paths.split(",")}])


@given(parsers.parse('its integrations.json declares "{plugin}" with no paths'))
def declares_no_paths(repository, plugin):
    declare(repository, uses=[{"plugin": plugin, "paths": []}])


@given(parsers.parse('its integrations.json lists "{key}" entry "{name}" "{value}"'))
def lists(repository, key, name, value):
    declare(repository, **{key: [{name: value, "reason": "because"}]})


@given(parsers.parse('its integrations.json owns "{host}"'))
def owns(repository, host):
    declare(repository, own_hosts=[host])


@given(parsers.parse('its integrations.json has a stack "{where}" with "{plugin}"'))
def stack(repository, where, plugin):
    declare(repository, terraform=[{"dir": where, "plugins": [plugin], "runs": ["laptop"]}])


@when("I scan it", target_fixture="result")
def scan_it(run, binary, repository):
    return run(binary("integrations"), "scan", str(repository))


@when(parsers.parse('I scan the path "{path}"'), target_fixture="result")
def scan_path(run, binary, path):
    return run(binary("integrations"), "scan", path)


@when("I ask for a suggestion", target_fixture="result")
def suggestion(run, binary, repository):
    return run(binary("integrations"), "scan", "--suggest", str(repository))
