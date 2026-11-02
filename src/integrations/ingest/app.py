"""Takes batches of call records over HTTP, inside the cluster, and spools them. No
ingress points here. Which dataset a repository's records go to is the deployment's."""

from __future__ import annotations

import threading
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .spool import Spool

MAX_BATCH = 5000


class Record(BaseModel):
    at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    repo: str = Field(min_length=1)
    app: str
    plugin: str = Field(pattern=r"^[a-z0-9-]+\.[a-z0-9-]+$")
    operation: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    status: int | None
    outcome: Literal["ok", "refused", "unavailable"]
    retries: int = Field(ge=0)
    bytes_in: int = Field(ge=0)
    bytes_out: int = Field(ge=0)
    mode: Literal["required", "advisory"]
    route: Literal["routed", "reported", "none"]


class Batch(BaseModel):
    records: list[Record] = Field(max_length=MAX_BATCH)


def create_app(spool: Spool, datasets: dict[str, str]) -> FastAPI:
    app = FastAPI(title="Integrations ingest", docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "dropped": spool.dropped}

    @app.post("/records", status_code=202)
    def records(batch: Batch) -> dict:
        by_dataset: dict[str, list[dict]] = {}
        for record in batch.records:
            dataset = datasets.get(record.repo)
            if dataset is None:
                raise HTTPException(403, f"{record.repo} has no dataset here")
            by_dataset.setdefault(dataset, []).append(record.model_dump())
        with lock:
            for dataset, rows in by_dataset.items():
                spool.append(dataset, rows)
        return {"accepted": len(batch.records)}

    return app
