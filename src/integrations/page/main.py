"""What the page's pod runs: integrations.page.main:app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..calls import Caller, StdoutSink
from ..ingest.google_token import GoogleToken
from .app import create_app
from .queries import CallLog


def build():  # pragma: no cover - the pod's wiring, with real keys
    key = json.loads(Path(os.environ["PAGE_KEY_FILE"]).read_text(encoding="utf-8"))
    caller = Caller("integrations", "page", sink=StdoutSink())
    return create_app(CallLog(caller, GoogleToken(caller, key), key["project_id"],
                              json.loads(os.environ["PAGE_DATASETS"])))


app = build() if os.environ.get("PAGE_KEY_FILE") else None
