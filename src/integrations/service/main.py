"""What uvicorn runs: integrations.service.main:app. With INTEGRATIONS_STORAGE_URL set,
call records go to each project's buffer in storage; with INTEGRATIONS_INGEST_URL, to the
ingest pod in batches; and otherwise to the log."""

import json
import os
from pathlib import Path

from ..calls import HttpSink, Network, StdoutSink
from .app import create_app


def sink():
    storage = os.environ.get("INTEGRATIONS_STORAGE_URL")
    if storage:
        from ..storage_sink import StorageSink  # needs fastavro, which only this path does
        token = Path(os.environ["INTEGRATIONS_STORAGE_TOKEN_FILE"])
        return StorageSink(json.loads(os.environ["INGEST_DATASETS"]), transport=Network(),
                           token=lambda: token.read_text(encoding="utf-8").strip(), base_url=storage,
                           sender=os.environ.get("HOSTNAME", "service"))
    url = os.environ.get("INTEGRATIONS_INGEST_URL")
    return HttpSink(f"{url.rstrip('/')}/records") if url else StdoutSink()


app = create_app(sink=sink())
