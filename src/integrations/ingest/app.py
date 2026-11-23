"""The ingest pod's routes: its health, for Kubernetes, and storage's notices of files it
took into a buffer (notify.py). Records themselves reach it only as files in storage."""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..calls import Check, health
from .notify import MAX_BODY_BYTES, ROUTE, Notices, Refused


def create_app(checks: Callable[[], list[Check]] = list, notices: Notices | None = None) -> FastAPI:
    """`checks` says whether the scanner still works; one that fails makes the pod
    unready (integrations.calls.health). Without `notices`, a notice is refused as when
    INGEST_NOTIFY_KEY isn't set."""
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

    @app.post(ROUTE)
    async def notify(request: Request) -> JSONResponse:
        too_big = JSONResponse({"error": f"a notice is at most {MAX_BODY_BYTES} bytes"}, status_code=413)
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return too_big
        # Read no further than the limit, whatever the length said.
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                return too_big
        try:
            if notices is None:
                raise Refused(403, "notices are off: INGEST_NOTIFY_KEY isn't set")
            taken = notices.receive(request.headers.get("x-ingest-timestamp"),
                                    request.headers.get("x-ingest-signature"), body)
        except Refused as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        return JSONResponse(taken, status_code=202)

    return app
