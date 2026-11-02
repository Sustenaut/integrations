"""What the ingest pod runs: integrations.ingest.main:app. Its worker starts with
the app. Settings come from the environment, and a key from a mounted Secret."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..calls import Caller, Network, StdoutSink
from .app import create_app
from .bigquery import BigQueryLoader
from .google_token import GoogleToken
from .overflow import Overflow
from .spool import Spool
from .worker import Worker


def build():  # pragma: no cover - the pod's wiring, with real paths and keys
    spool = Spool(Path(os.environ["INGEST_SPOOL_DIR"]))
    datasets = json.loads(os.environ["INGEST_DATASETS"])
    caller = Caller("integrations", "ingest", sink=StdoutSink())
    key = json.loads(Path(os.environ["INGEST_KEY_FILE"]).read_text(encoding="utf-8"))
    storage_token = Path(os.environ["INGEST_STORAGE_TOKEN_FILE"])
    overflow = Overflow(Network(), lambda: storage_token.read_text(encoding="utf-8").strip(),
                        os.environ["INGEST_STORAGE_URL"])
    loader = BigQueryLoader(caller, GoogleToken(caller, key), key["project_id"])
    worker = Worker(spool, loader, overflow, alert=lambda text: print(f"ALERT: {text}", flush=True))
    threading.Thread(target=worker.run, daemon=True).start()
    return create_app(spool, datasets)


app = build() if os.environ.get("INGEST_SPOOL_DIR") else None
