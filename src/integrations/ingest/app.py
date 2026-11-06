"""The ingest pod's only routes: its health, for Kubernetes. Records reach it as files in
storage, so nothing is sent to it."""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..calls import Check, health


def create_app(checks: Callable[[], list[Check]] = list) -> FastAPI:
    """`checks` says whether the scanner still works; one that fails makes the pod
    unready (integrations.calls.health)."""
    app = FastAPI(title="Integrations ingest", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/livez")
    def livez() -> dict:
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        h = health(checks(), {})
        return JSONResponse({"status": "ok" if h.healthy else "unhealthy",
                             "unhealthy": [c.plugin for c in h.unhealthy]},
                            status_code=200 if h.healthy else 503)

    return app
