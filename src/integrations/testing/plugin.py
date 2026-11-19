"""The `pytest11` entry point `integrations.testing`: it loads GitHub's steps
(`integrations.testing.github`) into a suite whose ini sets
`integrations_testing = true`, and nothing into any other, so a repository with
integrations among its test tools gets none of these steps unasked."""

from __future__ import annotations

import pytest

STEPS = "integrations.testing.github"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini("integrations_testing", "load integrations' GitHub steps (integrations.testing.github)",
                  type="bool", default=False)


def pytest_configure(config: pytest.Config) -> None:
    if config.getini("integrations_testing"):
        # Loaded once, however the suite asked: pytest_plugins in a conftest registers the same name.
        config.pluginmanager.import_plugin(STEPS)
