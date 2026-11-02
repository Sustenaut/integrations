"""Call logs that services left in storage: scanned every two minutes, loaded into
BigQuery as one merged job per project, and deleted. What is in the inbox is what is
still to do, so an outage anywhere only leaves files waiting.

Before a load, a record in batches/ names its files and so its job. If this pod stops
at any point, the next one finds the record and sends the same job again, which
BigQuery won't load twice, then finishes deleting.

A batch BigQuery rejects is split in two until only the bad files are left, which move
to rejected/ for a person. Each cycle reports facts as one status record, and what to
make of them is for whoever reads it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..calls import Request, Response, Transport
from .avrofile import decode, encode
from .bigquery import BigQueryLoader, JobBudget, LoadResult, Outcome
from .overflow import tenant_of
from .worker import job_id

INBOX = "inbox/calls/"
BATCHES = "batches/"
REJECTED = "rejected/"
LIST_LIMIT = 1000
MAX_BATCH_FILES = 500
# Merged in memory, in a pod with 384 MiB, and these files are compressed.
MAX_BATCH_BYTES = 32_000_000
TIMEOUT = 60


class StorageDown(Exception):
    """Storage didn't answer as it should: everything stays where it is, for next time."""


@dataclass(frozen=True)
class Waiting:
    name: str
    size: int
    uploaded: float


class Inbox:
    def __init__(self, transport: Transport, token: Callable[[], str], base_url: str):
        self.transport, self.token, self.base = transport, token, base_url.rstrip("/")

    def _send(self, dataset: str, method: str, path: str, body: bytes | None = None) -> Response:
        return self.transport.send(Request(method, f"{self.base}/{tenant_of(dataset)}/{path}",
                                           {"Authorization": f"Bearer {self.token()}"}, body), TIMEOUT)

    @staticmethod
    def _check(answer: Response, doing: str) -> Response:
        if not answer.ok:
            raise StorageDown(answer.error or f"{doing}: HTTP {answer.status}")
        return answer

    def list(self, dataset: str, prefix: str) -> tuple[list[Waiting], bool]:
        """The oldest page, and whether there is more after it."""
        answer = self._check(self._send(dataset, "GET", f"list?prefix={prefix}&limit={LIST_LIMIT}"), "list").json()
        files = [Waiting(f["name"], f["size"], datetime.fromisoformat(f["uploaded"].replace("Z", "+00:00")).timestamp())
                 for f in answer["files"]]
        return files, answer["cursor"] is not None

    def read(self, dataset: str, name: str) -> bytes | None:
        answer = self._send(dataset, "GET", name)
        return None if answer.status == 404 else self._check(answer, "read").body

    def put(self, dataset: str, name: str, data: bytes) -> None:
        self._check(self._send(dataset, "PUT", name, data), "write")

    def delete(self, dataset: str, name: str) -> None:
        self._check(self._send(dataset, "DELETE", name), "delete")


def _batch_job(files: list[str], attempt: int) -> str:
    return job_id("inbox", hashlib.sha256(json.dumps([attempt, sorted(files)]).encode()).hexdigest())


@dataclass
class Scanner:
    inbox: Inbox
    loader: BigQueryLoader
    jobs: JobBudget
    report: Callable[[dict], None]
    clock: Callable[[], float] = time.time

    def tick(self, datasets: list[str]) -> None:
        for dataset in datasets:
            self._scan(dataset)

    def _scan(self, dataset: str) -> None:
        status = {"kind": "ingest-status", "at": int(self.clock()), "dataset": dataset, "storage_ok": True,
                  "loaded_files": 0, "rejected_now": 0, "problems": []}
        try:
            self._work(dataset, status)
        except StorageDown as e:
            status["storage_ok"] = False
            status["problems"].append(str(e))
        except Exception as e:  # noqa: BLE001 - one project's bad day must not stop the rest
            status["problems"].append(f"{type(e).__name__}: {e}")
        status["jobs_today"] = self.jobs.used(dataset)
        self.report(status)

    def _work(self, dataset: str, status: dict) -> None:
        finished = True
        for record in self.inbox.list(dataset, BATCHES)[0]:
            finished = self._resume(dataset, record.name, status) and finished
        waiting, more = self.inbox.list(dataset, INBOX)
        done: set[str] = set()
        if finished and waiting:
            files = self._choose(waiting)
            record = f"{BATCHES}{_batch_job(files, 0)}.json"
            self.inbox.put(dataset, record, json.dumps({"files": files, "attempt": 0}).encode())
            self._run(dataset, record, files, 0, done, status)
        left = [f for f in waiting if f.name not in done]
        rejected, rejected_more = self.inbox.list(dataset, REJECTED)
        status.update(inbox_files=len(left), inbox_bytes=sum(f.size for f in left), inbox_more=more,
                      inbox_oldest_seconds=self._age(left), rejected_files=len(rejected),
                      rejected_more=rejected_more, rejected_oldest_seconds=self._age(rejected))

    def _age(self, files: list[Waiting]) -> int | None:
        return int(self.clock() - min(f.uploaded for f in files)) if files else None

    @staticmethod
    def _choose(waiting: list[Waiting]) -> list[str]:
        files, size = [], 0
        for f in waiting[:MAX_BATCH_FILES]:
            if files and size + f.size > MAX_BATCH_BYTES:
                break
            files.append(f.name)
            size += f.size
        return files

    def _resume(self, dataset: str, record: str, status: dict) -> bool:
        data = self.inbox.read(dataset, record)
        if data is None:
            return True
        held = json.loads(data)
        return self._run(dataset, record, held["files"], held["attempt"], set(), status)

    def _run(self, dataset: str, record: str, files: list[str], attempt: int, gone: set[str], status: dict) -> bool:
        """Settles the record's files, adding to `gone` those no longer in the inbox. The
        record goes only when all of them have, and True says so."""
        rows: dict[str, list[dict]] = {}
        contents: dict[str, bytes] = {}
        for name in files:
            data = self.inbox.read(dataset, name)
            if data is None:
                gone.add(name)  # An earlier try got as far as deleting it.
                continue
            try:
                rows[name] = decode(data)
                contents[name] = data
            except Exception as e:  # noqa: BLE001 - fastavro has many ways to say a file is bad
                self._reject(dataset, name, data, f"can't be read: {type(e).__name__}", gone, status)
        # All the record's files, even those gone: a job's id has to stay the same for a
        # try after a crash to be the same job.
        result = self._settle(dataset, files, rows, contents, attempt, gone, status)
        if result.outcome is Outcome.RETRY:
            status["problems"].append(f"waiting to load: {result.detail}")
            if result.ended:
                # That job ended in failure, and its id can't be used again.
                left = [name for name in files if name not in gone]
                self.inbox.put(dataset, record, json.dumps({"files": left, "attempt": attempt + 1}).encode())
            return False
        self.inbox.delete(dataset, record)
        return True

    def _settle(self, dataset, files, rows, contents, attempt, gone, status) -> LoadResult:
        records = [r for name in files for r in rows.get(name, [])]
        if not records:
            self._forget(dataset, files, gone)
            return LoadResult(Outcome.LOADED)
        if not self.jobs.may_load(dataset):
            return LoadResult(Outcome.RETRY, "the day's load jobs are used")
        try:
            data = encode(records)
        except Exception as e:  # noqa: BLE001 - a file that decodes can still not be a call record
            result = LoadResult(Outcome.REJECTED, f"not call records: {type(e).__name__}")
        else:
            self.jobs.spend(dataset)
            result = self.loader.load(dataset, data, _batch_job(files, attempt))
        if result.outcome is Outcome.LOADED:
            status["loaded_files"] += sum(name in rows for name in files)
            self._forget(dataset, files, gone)
        if result.outcome is not Outcome.REJECTED:
            return result
        if len(files) == 1:
            self._reject(dataset, files[0], contents[files[0]], result.detail, gone, status)
            return LoadResult(Outcome.LOADED)
        middle = len(files) // 2
        first = self._settle(dataset, files[:middle], rows, contents, attempt, gone, status)
        if first.outcome is Outcome.RETRY:
            return first
        return self._settle(dataset, files[middle:], rows, contents, attempt, gone, status)

    def _forget(self, dataset: str, files: list[str], gone: set[str]) -> None:
        for name in files:
            self.inbox.delete(dataset, name)
            gone.add(name)

    def _reject(self, dataset: str, name: str, data: bytes, why: str, gone: set[str], status: dict) -> None:
        self.inbox.put(dataset, REJECTED + name.removeprefix("inbox/"), data)
        self.inbox.delete(dataset, name)
        gone.add(name)
        status["rejected_now"] += 1
        status["problems"].append(f"rejected {name}: {why}")
