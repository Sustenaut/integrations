"""Call logs that services left in storage: scanned every two minutes, loaded into
BigQuery as one merged job per project, and deleted. What is in the inbox is what is
still to do, so an outage anywhere only leaves files waiting.

Before a load, a record in batches/ names its files and so its job. If this pod stops
at any point, the next one finds the record and sends the same job again, which
BigQuery won't load twice, then finishes deleting.

A batch BigQuery rejects is split in two until only the bad files are left, which move
to rejected/ for a person.

Every file carries its sender's report for the cycle, and those go to the status table as
one more job. This pod reports the same way, with a file of its own for each project: facts
only, and what to make of them is for whoever reads them.

A buffer that isn't a call log has a `Kind` of its own: its inbox folder, its table, its
files' format, and no reports. Observability's signals are one (SIGNALS): its evaluator
leaves them in a tenant of their own, and they load into a table of their own.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .. import __version__
from ..avrofile import decode, decode_need_states, decode_signals, encode, encode_need_states, encode_signals, encode_status, file_name, file_status, timestamp
from ..calls import Request, Response, Transport
from .bigquery import STATUS_TABLE, TABLE, BigQueryLoader, JobBudget, LoadResult, Outcome, job_id

INTERVAL_SECONDS = 120
INBOX = "inbox/calls/"
BATCHES = "batches/"
REJECTED = "rejected/"
LIST_LIMIT = 1000
MAX_BATCH_FILES = 500
# Merged in memory, in a pod with 384 MiB, and these files are compressed.
MAX_BATCH_BYTES = 32_000_000
TIMEOUT = 60


def tenant_of(dataset: str) -> str:
    """Each project's own tenant, named like its dataset: calls_visit_ee is calls-visit-ee."""
    return dataset.replace("_", "-")


@dataclass(frozen=True)
class Kind:
    """What a buffer holds: where its files wait, the table they load into, how they are
    read and merged, and whether senders' reports come with them."""

    folder: str
    table: str
    decode: Callable[[bytes], list[dict]]
    encode: Callable[[list[dict]], bytes]
    reports: bool
    what: str


# Looked up when called, as the module's own decode and encode.
CALLS = Kind(INBOX, TABLE, lambda data: decode(data), lambda rows: encode(rows), reports=True,  # noqa: PLW0108
             what="call records")
SIGNALS = Kind("inbox/signals/", "signals", decode_signals, encode_signals, reports=False, what="signals")
NEED_STATES = Kind("inbox/need-states/", "need_states", decode_need_states, encode_need_states, reports=False,
                   what="need states")


class StorageDown(Exception):
    """Storage didn't answer as it should: everything stays where it is, for next time."""


@dataclass(frozen=True)
class Waiting:
    name: str
    size: int
    uploaded: float


class Inbox:
    """`tenants` names the tenant of a dataset not named like it."""

    def __init__(self, transport: Transport, token: Callable[[], str], base_url: str,
                 tenants: dict[str, str] | None = None):
        self.transport, self.token, self.base = transport, token, base_url.rstrip("/")
        self.tenants = tenants or {}

    def _send(self, dataset: str, method: str, path: str, body: bytes | None = None) -> Response:
        tenant = self.tenants.get(dataset) or tenant_of(dataset)
        return self.transport.send(Request(method, f"{self.base}/{tenant}/{path}",
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
    sender: str = "ingest"
    # A dataset's Kind, when it isn't a call log.
    kinds: dict[str, Kind] = field(default_factory=dict)

    def kind(self, dataset: str) -> Kind:
        return self.kinds.get(dataset, CALLS)

    def tick(self, datasets: list[str]) -> None:
        for dataset in datasets:
            self._scan(dataset)

    def _scan(self, dataset: str) -> None:
        started = self.clock()
        status = {"kind": "ingest", "sender": self.sender, "version": __version__, "storage_ok": True,
                  "loaded_files": 0, "rejected_now": 0}
        problems: list[str] = []
        try:
            self._work(dataset, status, problems)
        except StorageDown as e:
            status["storage_ok"] = False
            problems.append(str(e))
        except Exception as e:  # noqa: BLE001 - one project's bad day must not stop the rest
            problems.append(f"{type(e).__name__}: {e}")
        ended = self.clock()
        kind = self.kind(dataset)
        status.update(at=timestamp(ended), cycle_start=timestamp(started), cycle_end=timestamp(ended),
                      jobs_today=self.jobs.used(dataset, kind.table), status_jobs_today=self.jobs.used(dataset, STATUS_TABLE))
        if not kind.reports:
            # Its report goes to the log alone: the buffer holds only what it is for.
            self.report({**status, "dataset": dataset, "problems": "; ".join(problems) or None})
            return
        try:
            data = encode([], {**status, "problems": "; ".join(problems) or None})
            self.inbox.put(dataset, file_name(ended, self.sender, data), data)
        except StorageDown as e:
            status["storage_ok"] = False
            problems.append(str(e))
        except Exception as e:  # noqa: BLE001 - a report that can't be made must not stop the next project
            problems.append(f"no report: {type(e).__name__}: {e}")
        self.report({**status, "dataset": dataset, "problems": "; ".join(problems) or None})

    def _work(self, dataset: str, status: dict, problems: list[str]) -> None:
        finished = True
        for record in self.inbox.list(dataset, BATCHES)[0]:
            finished = self._resume(dataset, record.name, status, problems) and finished
        waiting, more = self.inbox.list(dataset, self.kind(dataset).folder)
        done: set[str] = set()
        if finished and waiting:
            files = self._choose(waiting)
            record = f"{BATCHES}{_batch_job(files, 0)}.json"
            held = {"files": files, "attempt": 0, "status_attempt": 0, "status_done": False}
            self.inbox.put(dataset, record, json.dumps(held).encode())
            self._run(dataset, record, held, done, status, problems)
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

    def _resume(self, dataset: str, record: str, status: dict, problems: list[str]) -> bool:
        data = self.inbox.read(dataset, record)
        if data is None:
            return True
        return self._run(dataset, record, json.loads(data), set(), status, problems)

    def _run(self, dataset: str, record: str, held: dict, gone: set[str], status: dict, problems: list[str]) -> bool:
        """Settles the record's files, adding to `gone` those no longer in the inbox. The
        record goes only when all of them have, and True says so."""
        files, attempt = held["files"], held["attempt"]
        rows: dict[str, list[dict]] = {}
        contents: dict[str, bytes] = {}
        reports: list[dict] = []
        for name in files:
            data = self.inbox.read(dataset, name)
            if data is None:
                gone.add(name)  # An earlier try got as far as deleting it.
                continue
            kind = self.kind(dataset)
            try:
                rows[name] = kind.decode(data)
                contents[name] = data
            except Exception as e:  # noqa: BLE001 - fastavro has many ways to say a file is bad
                self._reject(dataset, name, data, f"can't be read: {type(e).__name__}", gone, status, problems)
                continue
            if kind.reports:
                self._read_report(name, data, reports, problems)
        if not self._load_reports(dataset, record, held, reports, problems):
            return False
        # All the record's files, even those gone: a job's id has to stay the same for a
        # try after a crash to be the same job.
        result = self._settle(dataset, files, rows, contents, attempt, gone, status, problems)
        if result.outcome is Outcome.RETRY:
            problems.append(f"waiting to load: {result.detail}")
            if result.ended:
                # That job ended in failure, and its id can't be used again.
                left = [name for name in files if name not in gone]
                self.inbox.put(dataset, record, json.dumps({**held, "files": left, "attempt": attempt + 1}).encode())
            return False
        self.inbox.delete(dataset, record)
        return True

    @staticmethod
    def _read_report(name: str, data: bytes, reports: list[dict], problems: list[str]) -> None:
        """A report that isn't one is only a lost report: the file's calls still load."""
        try:
            report = file_status(data)
            if report:
                encode_status([report])
                reports.append(report)
        except Exception as e:  # noqa: BLE001 - it came from the sender, in whatever state
            problems.append(f"bad report in {name}: {type(e).__name__}")

    def _load_reports(self, dataset: str, record: str, held: dict, reports: list[dict], problems: list[str]) -> bool:
        """False when the batch has to wait. A record notes when its reports have loaded, so
        a new job for its calls can't load them twice."""
        if not reports or held["status_done"]:
            return True
        if not self.jobs.may_load(dataset, STATUS_TABLE):
            problems.append("waiting to load reports: the day's load jobs are used")
            return False
        self.jobs.spend(dataset, STATUS_TABLE)
        digest = hashlib.sha256(json.dumps([held["status_attempt"], sorted(held["files"])]).encode()).hexdigest()
        result = self.loader.load(dataset, encode_status(reports), job_id("status", digest), STATUS_TABLE)
        if result.outcome is Outcome.RETRY:
            problems.append(f"waiting to load reports: {result.detail}")
            if result.ended:
                held["status_attempt"] += 1
                self.inbox.put(dataset, record, json.dumps(held).encode())
            return False
        if result.outcome is Outcome.REJECTED:
            problems.append(f"reports rejected: {result.detail}")
        held["status_done"] = True
        return True

    def _settle(self, dataset, files, rows, contents, attempt, gone, status, problems) -> LoadResult:
        records = [r for name in files for r in rows.get(name, [])]
        if not records:
            self._forget(dataset, files, gone)
            return LoadResult(Outcome.LOADED)
        kind = self.kind(dataset)
        if not self.jobs.may_load(dataset, kind.table):
            return LoadResult(Outcome.RETRY, "the day's load jobs are used")
        try:
            data = kind.encode(records)
        except Exception as e:  # noqa: BLE001 - a file that decodes can still not be a call record
            result = LoadResult(Outcome.REJECTED, f"not {kind.what}: {type(e).__name__}")
        else:
            self.jobs.spend(dataset, kind.table)
            result = self.loader.load(dataset, data, _batch_job(files, attempt), kind.table)
        if result.outcome is Outcome.LOADED:
            status["loaded_files"] += sum(name in rows for name in files)
            self._forget(dataset, files, gone)
        if result.outcome is not Outcome.REJECTED:
            return result
        if len(files) == 1:
            self._reject(dataset, files[0], contents[files[0]], result.detail, gone, status, problems)
            return LoadResult(Outcome.LOADED)
        middle = len(files) // 2
        first = self._settle(dataset, files[:middle], rows, contents, attempt, gone, status, problems)
        if first.outcome is Outcome.RETRY:
            return first
        return self._settle(dataset, files[middle:], rows, contents, attempt, gone, status, problems)

    def _forget(self, dataset: str, files: list[str], gone: set[str]) -> None:
        for name in files:
            self.inbox.delete(dataset, name)
            gone.add(name)

    def _reject(self, dataset: str, name: str, data: bytes, why: str, gone: set[str], status: dict,
                problems: list[str]) -> None:
        self.inbox.put(dataset, REJECTED + name.removeprefix("inbox/"), data)
        self.inbox.delete(dataset, name)
        gone.add(name)
        status["rejected_now"] += 1
        problems.append(f"rejected {name}: {why}")
