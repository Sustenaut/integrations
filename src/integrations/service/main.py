"""What uvicorn runs: integrations.service.main:app. With INTEGRATIONS_STORAGE_URL set,
call records go to each project's buffer in storage, and otherwise to the log."""

import json
import os
from pathlib import Path

from ..calls import Check, Network, StdoutSink
from .app import create_app


def sink():
    storage = os.environ.get("INTEGRATIONS_STORAGE_URL")
    if storage:
        from ..storage_sink import StorageSink  # needs fastavro, which only this path does
        token = Path(os.environ["INTEGRATIONS_STORAGE_TOKEN_FILE"])
        return StorageSink(json.loads(os.environ["INGEST_DATASETS"]), transport=Network(),
                           token=lambda: token.read_text(encoding="utf-8").strip(), base_url=storage,
                           sender=os.environ.get("HOSTNAME", "service"))
    return StdoutSink()


def checks() -> list[Check]:
    """Whether the call log's buffer can be reached: the token the kubelet projects."""
    path = os.environ.get("INTEGRATIONS_STORAGE_TOKEN_FILE")
    if not os.environ.get("INTEGRATIONS_STORAGE_URL") or not path:
        return []
    try:
        ok = bool(Path(path).read_text(encoding="utf-8").strip())
    except OSError:
        ok = False
    return [Check("storage.buffer", ok, "" if ok else f"{path} can't be read")]


app = create_app(sink=sink(), checks=checks)
