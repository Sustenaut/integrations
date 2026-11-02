"""Files BigQuery can't take go to the storage service: those waiting for BigQuery
to come back, and those it rejected, for a person to look at.

Each consuming project has its own private tenant, named like its dataset
(calls_visit_ee is calls-visit-ee), so its files stay apart from every other
project's and can be deleted on their own.

Storage has no listing, so a small manifest behind a named head says what is in
each: pending and rejected. Files are kept for good; replaying one takes it off the
manifest. If a file has no manifest entry, nothing replays it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Callable

from ..calls import Request, Response, Transport

PENDING = "pending"
REJECTED = "rejected"
TIMEOUT = 60


def tenant_of(dataset: str) -> str:
    """Only this project's tenant, whichever file it is."""
    return dataset.replace("_", "-")


class OverflowUnavailable(Exception):
    """Storage didn't take it: the file stays in the spool."""


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    job_id: str


class Overflow:
    def __init__(self, transport: Transport, token: Callable[[], str], base_url: str):
        self.transport, self.token, self.base = transport, token, base_url.rstrip("/")

    def _send(self, dataset: str, method: str, path: str, headers: dict | None = None,
              body: bytes | None = None) -> Response:
        return self.transport.send(Request(method, f"{self.base}/{tenant_of(dataset)}/{path}",
                                           {"Authorization": f"Bearer {self.token()}", **(headers or {})}, body), TIMEOUT)

    def _upload(self, dataset: str, name: str, data: bytes, content_type: str) -> str:
        boundary = uuid.uuid4().hex
        body = b"".join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode(), data,
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="kept"\r\n\r\ntrue'
            f"\r\n--{boundary}--\r\n".encode()])
        answer = self._send(dataset, "POST", "files", {"Content-Type": f"multipart/form-data; boundary={boundary}"}, body)
        if not answer.ok:
            raise OverflowUnavailable(answer.error or f"upload: HTTP {answer.status}")
        return answer.json()["id"]

    def _manifest(self, kind: str, dataset: str) -> tuple[list[Entry], str | None]:
        answer = self._send(dataset, "GET", f"head/{kind}")
        if answer.status == 404:
            return [], None
        if not answer.ok:
            raise OverflowUnavailable(answer.error or f"manifest: HTTP {answer.status}")
        return [Entry(**e) for e in answer.json()], answer.etag

    def _write_manifest(self, kind: str, dataset: str, entries: list[Entry], etag: str | None) -> None:
        text = json.dumps([e.__dict__ for e in entries], sort_keys=True).encode()
        file_id = self._upload(dataset, f"{kind}-{uuid.uuid4().hex}.json", text, "application/json")
        headers = {"Content-Type": "application/json", **({"If-Match": etag} if etag else {"If-None-Match": "*"})}
        answer = self._send(dataset, "PUT", f"head/{kind}", headers, json.dumps({"hash": file_id}).encode())
        if not answer.ok:
            raise OverflowUnavailable(answer.error or f"manifest move: HTTP {answer.status}")

    def put(self, kind: str, dataset: str, data: bytes, job_id: str) -> None:
        name = f"{kind}-{job_id}.avro"
        file_id = self._upload(dataset, name, data, "application/avro")
        entries, etag = self._manifest(kind, dataset)
        if not any(e.job_id == job_id for e in entries):
            self._write_manifest(kind, dataset, [*entries, Entry(file_id, name, job_id)], etag)

    def pending(self, dataset: str) -> list[Entry]:
        return self._manifest(PENDING, dataset)[0]

    def read(self, dataset: str, entry: Entry) -> bytes:
        answer = self._send(dataset, "GET", f"f/{entry.id}/{entry.name}")
        if not answer.ok:
            raise OverflowUnavailable(answer.error or f"read: HTTP {answer.status}")
        return answer.body

    def forget(self, kind: str, dataset: str, done: list[Entry]) -> None:
        entries, etag = self._manifest(kind, dataset)
        gone = {e.job_id for e in done}
        self._write_manifest(kind, dataset, [e for e in entries if e.job_id not in gone], etag)
