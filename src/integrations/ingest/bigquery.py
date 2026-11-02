"""Loads an Avro file into BigQuery with a load job. A job's id comes from the
file's content, so sending a file again can't load it twice."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from ..calls import Caller, Request, Unavailable

PLUGIN = "google.bigquery"
API = "https://bigquery.googleapis.com"
TABLE = "calls"
# BigQuery's own words for a problem another try may get past.
TRANSIENT = {"backendError", "internalError", "rateLimitExceeded", "quotaExceeded", "jobBackendError", "timeout"}
# Under BigQuery's 1,500 a table a day, leaving room for a person's own loads.
JOBS_PER_TABLE_PER_DAY = 1400


class Outcome(Enum):
    LOADED = "loaded"
    REJECTED = "rejected"
    RETRY = "retry"


@dataclass(frozen=True)
class LoadResult:
    outcome: Outcome
    detail: str = ""
    # The job finished without loading, so another try needs another job id.
    ended: bool = False


@dataclass
class JobBudget:
    """A table's load jobs today, shared by everything in the pod that loads."""

    clock: Callable[[], float] = time.time
    spent: dict[tuple[str, str], int] = field(default_factory=dict)

    def _key(self, dataset: str) -> tuple[str, str]:
        return dataset, datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")

    def used(self, dataset: str) -> int:
        return self.spent.get(self._key(dataset), 0)

    def may_load(self, dataset: str) -> bool:
        return self.used(dataset) < JOBS_PER_TABLE_PER_DAY

    def spend(self, dataset: str) -> None:
        self.spent[self._key(dataset)] = self.used(dataset) + 1


class BigQueryLoader:
    def __init__(self, caller: Caller, token: Callable[[], str], project: str, *,
                 sleep: Callable[[float], None] = time.sleep, polls: int = 30):
        self.caller, self.token, self.project, self.sleep, self.polls = caller, token, project, sleep, polls

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    def load(self, dataset: str, data: bytes, job_id: str) -> LoadResult:
        try:
            return self._load(dataset, data, job_id)
        except Unavailable as e:
            return LoadResult(Outcome.RETRY, e.detail)

    def _load(self, dataset: str, data: bytes, job_id: str) -> LoadResult:
        job = {"jobReference": {"projectId": self.project, "jobId": job_id},
               "configuration": {"load": {
                   "destinationTable": {"projectId": self.project, "datasetId": dataset, "tableId": TABLE},
                   "sourceFormat": "AVRO", "useAvroLogicalTypes": True, "writeDisposition": "WRITE_APPEND",
                   "createDisposition": "CREATE_NEVER"}}}
        boundary = f"integrations{job_id}"
        body = b"".join([
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(job).encode(),
            f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
            data, f"\r\n--{boundary}--\r\n".encode()])
        started = self.caller.call(PLUGIN, "load", Request(
            "POST", f"{API}/upload/bigquery/v2/projects/{self.project}/jobs?uploadType=multipart",
            {**self._headers(), "Content-Type": f"multipart/related; boundary={boundary}"}, body, repeatable=True))
        # 409: this file's job already exists, from a try whose answer was lost.
        if not started.ok and started.status != 409:
            return LoadResult(Outcome.RETRY if started.status in (401, 403) else Outcome.REJECTED,
                              f"HTTP {started.status}")
        return self._wait(job_id)

    def _wait(self, job_id: str) -> LoadResult:
        url = f"{API}/bigquery/v2/projects/{self.project}/jobs/{job_id}"
        for _ in range(self.polls):
            answer = self.caller.call(PLUGIN, "load", Request("GET", url, self._headers()))
            if not answer.ok:
                return LoadResult(Outcome.RETRY, f"HTTP {answer.status}")
            status = answer.json()["status"]
            if status.get("state") == "DONE":
                error = status.get("errorResult")
                if not error:
                    return LoadResult(Outcome.LOADED)
                outcome = Outcome.RETRY if error.get("reason") in TRANSIENT else Outcome.REJECTED
                return LoadResult(outcome, error.get("reason", "unknown"), ended=True)
            self.sleep(2)
        return LoadResult(Outcome.RETRY, "the job did not finish in time")
