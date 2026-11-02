"""The page itself: plain HTML from the server, with no script and nothing loaded from
elsewhere. It holds no call's body, as the call log has none."""

from __future__ import annotations

from html import escape

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import registry
from ..calls import IntegrationError
from .queries import DAYS, CallLog, Step, split

STYLE = ("body{font:15px/1.5 system-ui;margin:2rem auto;max-width:60rem;padding:0 1rem;color-scheme:light dark}"
         "table{border-collapse:collapse;width:100%}th,td{padding:.25rem .5rem;border-bottom:1px solid #8884;"
         "text-align:left}td.n,th.n{text-align:right}.bad{font-weight:600}")
ID = r"^[\w-]{1,64}$"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html lang="en"><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>'
                        f"<style>{STYLE}</style><h1>{escape(title)}</h1>{body}</html>",
                        headers={"Cache-Control": "private, no-store", "Content-Security-Policy":
                                 "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"})


def _classes() -> dict[tuple[str, str], str]:
    return {(p.id, o["name"]): o["class"] for p in registry.plugins().values() for o in p.operations}


def _ms(value: int | None) -> str:
    return "not yet" if value is None else f"{value:,} ms"


def _steps_table(steps: list[Step]) -> str:
    rows = "".join(
        f"<tr><td>{escape(s.at)}</td><td>{escape(s.operation)}</td><td class=n>{s.latency_ms:,}</td>"
        f"<td class=n>{'' if s.status is None else s.status}</td><td>{escape(s.outcome)}</td><td class=n>{s.retries}</td></tr>"
        for s in steps)
    return ("<table><tr><th>At<th>Operation<th class=n>ms<th class=n>Status<th>Outcome<th class=n>Retries</tr>"
            f"{rows}</table>")


def create_app(log: CallLog) -> FastAPI:
    app = FastAPI(title="Call log", docs_url=None, redoc_url=None, openapi_url=None)
    targets = registry.classes()

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def summary() -> HTMLResponse:
        try:
            rows = log.summary()
        except IntegrationError:
            raise HTTPException(503, "BigQuery didn't answer: try again") from None
        classes = _classes()
        cells = []
        for r in rows:
            kind = classes.get((r.plugin, r.operation), "")
            target = targets.get(kind, {}).get("response_p95", "")
            rate = 100 * r.not_ok / r.calls if r.calls else 0
            mark = ' class="n bad"' if r.not_ok else " class=n"
            cells.append(f"<tr><td>{escape(r.dataset)}</td><td>{escape(r.plugin)}</td><td>{escape(r.operation)}</td>"
                         f"<td>{escape(kind)}</td><td>{escape(target)}</td><td class=n>{r.calls:,}</td>"
                         f"<td class=n>{r.p50:,}</td><td class=n>{r.p95:,}</td><td{mark}>{rate:.1f}%</td></tr>")
        table = ("<table><tr><th>Dataset<th>Plugin<th>Operation<th>Class<th>Target p95<th class=n>Calls"
                 f"<th class=n>p50 ms<th class=n>p95 ms<th class=n>Not ok</tr>{''.join(cells)}</table>") if cells else "<p>No calls yet.</p>"
        form = ('<form action="/trace"><label>Request id <input name="id" required pattern="[A-Za-z0-9_-]{1,64}"></label> '
                "<button>Show</button></form>")
        return _page(f"Calls, last {DAYS} days", table + form)

    @app.get("/trace", response_class=HTMLResponse)
    def trace(id: str = Query(pattern=ID)) -> HTMLResponse:  # noqa: A002 - the form's field
        try:
            steps = log.trace(id)
        except IntegrationError:
            raise HTTPException(503, "BigQuery didn't answer: try again") from None
        if not steps:
            return _page(f"Request {id}", "<p>No records. A new request's take a few minutes to arrive.</p>")
        parts = split(steps)
        note = "<p>Its calls ran at once, so GitHub's time is added across them.</p>" if parts.calls_at_once else ""
        summary = (f"<p>Provider: {_ms(parts.provider_ms)} · Service: {_ms(parts.service_ms)} · App: {_ms(parts.app_ms)}</p>"
                   f"{note}")
        return _page(f"Request {id}", summary + _steps_table(steps))

    return app
