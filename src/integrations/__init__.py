"""The registry of every outside service our code calls, and the check that
keeps it true."""

import re as _re
from importlib import metadata as _metadata
from pathlib import Path as _Path


def _version() -> str:
    """pyproject.toml's version, the one place pubrel sets it: read from the
    file in a source checkout (tests run from src/), else from the installed
    package's metadata."""
    source = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    if source.is_file():
        text = source.read_text(encoding="utf-8")
        if _re.search(r'^name = "integrations"$', text, _re.M):
            return _re.search(r'^version = "([^"]+)"$', text, _re.M)[1]
    return _metadata.version("integrations")


__version__ = _version()
