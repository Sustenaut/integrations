"""The ingest pod's only route: its health, for Kubernetes. Records reach it as files in
storage, so nothing is sent to it."""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Integrations ingest", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app
