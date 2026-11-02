"""What the page asks BigQuery. Dataset names come from the deployment and are checked,
as they can't be query parameters; anything a visitor gives is a parameter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from ..calls import Caller, Refused, Request
from ..ingest.bigquery import API

PLUGIN = "google.bigquery"
DATASET = re.compile(r"[a-z][a-z0-9_]{0,1023}")
DAYS = 7
TOTALS = ("service-total", "client-total")


@dataclass(frozen=True)
class Row:
    dataset: str
    plugin: str
    operation: str
    calls: int
    p50: int
    p95: int
    not_ok: int


@dataclass(frozen=True)
class Step:
    at: str
    dataset: str
    plugin: str
    operation: str
    latency_ms: int
    status: int | None
    outcome: str
    retries: int


@dataclass(frozen=True)
class Split:
    """One request's time. A part is None when its record isn't there yet: an app's
    arrives in a batch, later. The service's reads run at once, so GitHub's time can be
    more than the whole request's, and then the service's shows as 0."""

    provider_ms: int
    service_ms: int | None
    app_ms: int | None
    calls_at_once: bool


def split(steps: list[Step]) -> Split:
    calls = [s for s in steps if s.operation not in TOTALS]
    provider = sum(s.latency_ms for s in calls)
    service_total = next((s.latency_ms for s in steps if s.operation == "service-total"), None)
    client_total = next((s.latency_ms for s in steps if s.operation == "client-total"), None)
    return Split(provider, None if service_total is None else max(service_total - provider, 0),
                 None if client_total is None or service_total is None else max(client_total - service_total, 0),
                 service_total is not None and provider > service_total)


class CallLog:
    def __init__(self, caller: Caller, token: Callable[[], str], project: str, datasets: list[str]):
        bad = [d for d in datasets if not DATASET.fullmatch(d)]
        if bad:
            raise ValueError(f"not dataset names: {bad}")
        self.caller, self.token, self.project, self.datasets = caller, token, project, datasets

    def _query(self, sql: str, parameters: list[dict] | None = None) -> list[list]:
        response = self.caller.call(PLUGIN, "query", Request(
            "POST", f"{API}/bigquery/v2/projects/{self.project}/queries",
            {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
            json.dumps({"query": sql, "useLegacySql": False, "parameterMode": "NAMED",
                        "queryParameters": parameters or [], "timeoutMs": 8000}).encode(), repeatable=True))
        if not response.ok:
            raise Refused(PLUGIN, "query", f"HTTP {response.status}")
        out = response.json()
        return [[cell["v"] for cell in row["f"]] for row in out.get("rows", [])]

    def _all(self, where: str) -> str:
        return " UNION ALL ".join(f"SELECT '{d}' AS dataset, * FROM `{self.project}.{d}.calls` WHERE {where}"
                                  for d in self.datasets)

    def summary(self) -> list[Row]:
        recent = f"`at` >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {DAYS} DAY)"
        rows = self._query(
            "SELECT dataset, plugin, operation, COUNT(*), APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)], "
            "APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)], COUNTIF(outcome != 'ok') "
            f"FROM ({self._all(recent)}) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")
        return [Row(r[0], r[1], r[2], int(r[3]), int(r[4]), int(r[5]), int(r[6])) for r in rows]

    def trace(self, request_id: str) -> list[Step]:
        rows = self._query(
            "SELECT FORMAT_TIMESTAMP('%FT%TZ', `at`), dataset, plugin, operation, latency_ms, status, outcome, retries "
            f"FROM ({self._all('request_id = @id')}) ORDER BY 1, 4",
            [{"name": "id", "parameterType": {"type": "STRING"}, "parameterValue": {"value": request_id}}])
        return [Step(r[0], r[1], r[2], r[3], int(r[4]), None if r[5] is None else int(r[5]), r[6], int(r[7])) for r in rows]
