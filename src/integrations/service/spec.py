"""Prints the OpenAPI spec the clients and docs are generated from:
python -m integrations.service.spec > openapi/integrations.json"""

from __future__ import annotations

import json

from .app import create_app
from .settings import Settings


def spec_text() -> str:
    spec = create_app(Settings(), sink=None).openapi()
    # The release changes without the API changing; a client's version is its tag.
    spec["info"]["version"] = "1"
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(spec_text(), end="")
