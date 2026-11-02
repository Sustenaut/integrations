"""What uvicorn runs: integrations.service.main:app. With INTEGRATIONS_INGEST_URL
set, call records go to the ingest pod in batches, and otherwise to the log."""

import os

from ..calls import HttpSink, StdoutSink
from .app import create_app


def sink():
    url = os.environ.get("INTEGRATIONS_INGEST_URL")
    return HttpSink(f"{url.rstrip('/')}/records") if url else StdoutSink()


app = create_app(sink=sink())
