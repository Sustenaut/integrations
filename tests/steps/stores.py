"""The stateful stand-ins of storage and BigQuery the ingest scenarios share:
each takes a caller's requests as its transport and keeps what it was given,
which pubtest's fake services, answering each request the same, cannot."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from integrations.avrofile import (decode, decode_ci_jobs, decode_cluster_usage, decode_need_states,
                                   decode_pubrules_checks, decode_signals)
from integrations.calls import Response
from integrations.ingest.bigquery import STATUS_TABLE

BASE = "https://storage.example"


def record(n=1, **fields):
    """Call record N, as a sender writes it."""
    return {"at": "2026-11-02T10:00:00Z", "repo": "a-repo", "app": "web", "plugin": "github.edit-pr",
            "operation": "open-pr", "request_id": f"r{n}", "latency_ms": 12, "status": 201, "outcome": "ok",
            "retries": 0, "bytes_in": 3, "bytes_out": 4, "mode": "required", "route": "routed", **fields}


class FakeBuffer:
    """A tenant's folders, as the storage service lists them."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.uploaded = "2027-01-15T07:50:00.000Z"
        self.down_for: set[str] = set()
        self.sent: list[tuple[str, str]] = []

    def send(self, request, timeout):
        url = urlparse(request.url)
        tenant, _, path = url.path.lstrip("/").partition("/")
        assert tenant and request.headers["Authorization"] == "Bearer pod-token"
        self.sent.append((request.method, path))
        if request.method in self.down_for:
            return Response(None, error="no answer")
        if path == "list":
            prefix = parse_qs(url.query)["prefix"][0]
            names = sorted(n for n in self.files if n.startswith(prefix))
            return Response(200, json.dumps({
                "files": [{"name": n, "size": len(self.files[n]), "uploaded": self.uploaded} for n in names],
                "cursor": None}).encode())
        if request.method == "PUT":
            self.files[path] = request.body
            return Response(200, b"{}")
        if request.method == "DELETE":
            self.files.pop(path, None)
            return Response(204)
        return Response(200, self.files[path]) if path in self.files else Response(404, b"{}")

    def inbox(self):
        """What senders left: the pod's own report is left out."""
        return sorted(n for n in self.files if n.startswith("inbox/") and "-ingest-" not in n)

    def under(self, folder):
        return sorted(n for n in self.files if n.startswith(folder))

    def left(self):
        """Anything of the senders' still in the buffer, in any folder."""
        return [n for n in self.files if "-ingest-" not in n and not n.startswith("batches/")] + self.under("batches/")


class PickyBigQuery:
    """Load jobs, each table's rows kept apart. A calls job holding a row marked bad is
    rejected; `fail_jobs` calls jobs, or `fail_status` report jobs, end in a failure that
    may pass; `answer` other than "ok" is BigQuery busy (503 to the upload) or its jobs
    left running."""

    def __init__(self):
        self.jobs: dict[str, bytes] = {}
        self.rows: list[dict] = []
        self.answer = "ok"
        self.posts = 0
        self.ended: dict[str, dict] = {}
        self.fail_jobs = 0
        self.fail_status = 0
        self.refuse_status = False
        self.call_jobs: list[str] = []
        self.status_jobs: list[str] = []
        self.status_rows: list[dict] = []
        self.rows_of = {"signals": [], "need_states": [], "ci_jobs": [], "cluster_usage": [], "pubrules_checks": []}
        self.decoders = {"signals": decode_signals, "need_states": decode_need_states, "ci_jobs": decode_ci_jobs,
                         "cluster_usage": decode_cluster_usage, "pubrules_checks": decode_pubrules_checks}
        self.datasets: list[str] = []
        self.tables: list[str] = []

    def send(self, request, timeout):
        if request.method == "POST":
            head, _, tail = request.body.partition(b"application/octet-stream\r\n\r\n")
            config = json.loads(re.search(rb"(\{.*\})", head, re.S).group(1))
            job = config["jobReference"]["jobId"]
            table = config["configuration"]["load"]["destinationTable"]["tableId"]
            if self.answer != "ok":
                # Busy refuses the upload; running takes it and never finishes it.
                self.posts += 1
                if self.answer == "busy":
                    return Response(503, b"{}")
                if job in self.jobs:
                    return Response(409, b"{}")
                self.jobs[job] = tail[:tail.rindex(b"\r\n--")]
                return Response(200, b"{}")
            if job in self.jobs:
                return Response(409, b"{}")
            data = tail[:tail.rindex(b"\r\n--")]
            self.jobs[job] = data
            self.tables.append(table)
            self.datasets.append(config["configuration"]["load"]["destinationTable"]["datasetId"])
            if table in self.rows_of:
                self.rows_of[table] += self.decoders[table](data)
                return Response(200, b"{}")
            rows = decode(data)
            if table == STATUS_TABLE:
                self.status_jobs.append(job)
                if self.refuse_status:
                    self.ended[job] = {"reason": "invalid"}
                elif self.fail_status:
                    self.fail_status -= 1
                    self.ended[job] = {"reason": "backendError"}
                else:
                    self.status_rows += rows
                return Response(200, b"{}")
            self.posts += 1
            self.call_jobs.append(job)
            if any(r["operation"] == "bad" for r in rows):
                self.ended[job] = {"reason": "invalid"}
            elif self.fail_jobs:
                self.fail_jobs -= 1
                self.ended[job] = {"reason": "backendError"}
            else:
                self.rows += rows
            return Response(200, b"{}")
        job = request.url.rsplit("/", 1)[1]
        assert job
        if self.answer == "running":
            return Response(200, json.dumps({"status": {"state": "RUNNING"}}).encode())
        error = self.ended.get(job)
        return Response(200, json.dumps({"status": {"state": "DONE", **({"errorResult": error} if error else {})}}).encode())
