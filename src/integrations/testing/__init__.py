"""Test harnesses for a suite whose code calls a provider through integrations.
Nothing here runs in a service; it ships in the wheel so a suite loads it rather
than keep a copy.

Loading it. The `pytest11` entry point `integrations.testing` (`plugin.py`) is
always there, and loads nothing until a suite asks, either way:

- `integrations_testing = true` in its pytest.ini (or `[tool.pytest.ini_options]`);
- `pytest_plugins = ["integrations.testing.github"]` in its top conftest.py.

`integrations.testing.github` is GitHub's: steps that set up what a scenario's
repositories hold, made in the GitHub test org when recording and replayed from the
cassette (its docstring says how). The same steps for Node are
clients/js/testing-github.js, a pubtest test customer.
"""
