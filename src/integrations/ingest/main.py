"""What the ingest pod runs: integrations.ingest.main:app. Its scanner starts with
the app. Settings come from the environment, and a key from a mounted Secret."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..calls import Caller, Network, StdoutSink
from .app import create_app
from .bigquery import BigQueryLoader, JobBudget
from .google_token import GoogleToken
from .inbox import INTERVAL_SECONDS, Inbox, Scanner


def build():  # pragma: no cover - the pod's wiring, with real paths and keys
    datasets = json.loads(os.environ["INGEST_DATASETS"])
    caller = Caller("integrations", "ingest", sink=StdoutSink())
    key = json.loads(Path(os.environ["INGEST_KEY_FILE"]).read_text(encoding="utf-8"))
    storage_token = Path(os.environ["INGEST_STORAGE_TOKEN_FILE"])
    loader = BigQueryLoader(caller, GoogleToken(caller, key), key["project_id"])
    inbox = Inbox(Network(), lambda: storage_token.read_text(encoding="utf-8").strip(), os.environ["INGEST_STORAGE_URL"])
    scanner = Scanner(inbox, loader, JobBudget(), report=lambda status: print(json.dumps(status), flush=True),
                      sender=os.environ.get("HOSTNAME", "ingest"))
    buffers = sorted(set(datasets.values()))

    def run() -> None:
        while True:
            time.sleep(INTERVAL_SECONDS)
            scanner.tick(buffers)

    threading.Thread(target=run, daemon=True).start()
    return create_app()


app = build() if os.environ.get("INGEST_KEY_FILE") else None
