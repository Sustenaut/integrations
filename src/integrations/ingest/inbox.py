"""Call logs that services left in storage: loaded into BigQuery as one merged job per
project, and deleted. What is in the inbox is what is still to do, so an outage anywhere
only leaves files waiting.

Files reach the scanner two ways. Storage tells the pod of each file written into a
buffer's inbox (notify.py), and the scanner takes the names it was told, without listing:
a buffer's batch goes once its first name has waited WAIT_SECONDS and its last batch is
GAP_SECONDS old, so a busy buffer still loads at most once a gap. And a sweep lists every
buffer, at start and then every INGEST_SWEEP_SECONDS (INTERVAL_SECONDS unless set), for
whatever no one told it of: files from before the pod started, a notice that never came,
a batch that didn't finish. With no notices, the sweep alone is the scanner as it was.

Before a load, a record in batches/ names its files and so its job. If this pod stops
at any point, the next one finds the record and sends the same job again, which
BigQuery won't load twice, then finishes deleting.

A batch BigQuery rejects is split in two until only the bad files are left, which move
to rejected/ for a person.

Every file carries its sender's report for the cycle, and those go to the status table as
one more job. This pod's own report of each pass, for each project, goes with them: it
waits in memory for that project's next reports job, and when none has gone for
INGEST_REPORT_SECONDS (INTERVAL_SECONDS unless set), it loads as a job of its own. It is
never a file in the buffer, so a pass leaves the next one nothing to do. Facts only, and
what to make of them is for whoever reads them.

A buffer that isn't a call log has a `Kind` of its own: its inbox folder, its table, its
files' format, and no reports. Observability's signals are one (SIGNALS): its evaluator
leaves them in a tenant of their own, and they load into a table of their own.

The scanner names each buffer by a key: a call log's or a named buffer's dataset, or, for
the buffers INGEST_BUFFERS names, `<dataset>.<table>`, so that one dataset can hold
several Kinds (platform holds pubrules_checks, ci_jobs and cluster_usage). Kinds that share
a tenant keep their batch records apart by a prefix of their own (`batches/<table>-`).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .. import __version__
from ..avrofile import (decode, decode_ci_jobs, decode_cluster_usage, decode_need_states, decode_pubrules_checks,
                        decode_signals, encode, encode_ci_jobs, encode_cluster_usage, encode_need_states,
                        encode_pubrules_checks, encode_signals, encode_status, file_status, timestamp)
from ..calls import Request, Response, Transport
from .bigquery import STATUS_TABLE, TABLE, BigQueryLoader, JobBudget, LoadResult, Outcome, job_id

INTERVAL_SECONDS = 120
# A notified name waits this long, so the files written about together go as one batch.
WAIT_SECONDS = 5
# A buffer's batches are at least this far apart. 120 keeps a table to 720 load jobs a
# day; 62 is as low as it can go within JOBS_PER_TABLE_PER_DAY.
GAP_SECONDS = 120
# The pod's own reports a project keeps while BigQuery can't take them; past it the oldest go.
REPORTS_KEPT = 60
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
    # Where its batch records go: BATCHES for a Kind alone in its tenant.
    batches: str = BATCHES


# Looked up when called, as the module's own decode and encode.
CALLS = Kind(INBOX, TABLE, lambda data: decode(data), lambda rows: encode(rows), reports=True,  # noqa: PLW0108
             what="call records")
SIGNALS = Kind("inbox/signals/", "signals", decode_signals, encode_signals, reports=False, what="signals")
NEED_STATES = Kind("inbox/need-states/", "need_states", decode_need_states, encode_need_states, reports=False,
                   what="need states")
PUBRULES_CHECKS = Kind("inbox/pubrules-checks/", "pubrules_checks", decode_pubrules_checks, encode_pubrules_checks,
                     reports=False, what="pubrules checks")
# CI's wait times and compute load: two Kinds in the one tenant ci-usage.
CI_JOBS = Kind("inbox/ci-jobs/", "ci_jobs", decode_ci_jobs, encode_ci_jobs, reports=False, what="CI jobs",
               batches=f"{BATCHES}ci_jobs-")
CLUSTER_USAGE = Kind("inbox/cluster-usage/", "cluster_usage", decode_cluster_usage, encode_cluster_usage,
                     reports=False, what="cluster usage", batches=f"{BATCHES}cluster_usage-")
# The Kinds INGEST_BUFFERS may name, by their tables.
KINDS = {kind.table: kind for kind in (SIGNALS, NEED_STATES, PUBRULES_CHECKS, CI_JOBS, CLUSTER_USAGE)}


def dataset_of(key: str) -> str:
    """The dataset a buffer's key names: the key itself, or what is before `.<table>`."""
    return key.partition(".")[0]


class StorageDown(Exception):
    """Storage didn't answer as it should: everything stays where it is, for next time."""


@dataclass(frozen=True)
class Waiting:
    name: str
    size: int
    uploaded: float


class Inbox:
    """`tenants` names the tenant of a buffer (by its key) not named like its dataset."""

    def __init__(self, transport: Transport, token: Callable[[], str], base_url: str,
                 tenants: dict[str, str] | None = None):
        self.transport, self.token, self.base = transport, token, base_url.rstrip("/")
        self.tenants = tenants or {}

    def _send(self, dataset: str, method: str, path: str, body: bytes | None = None) -> Response:
        tenant = self.tenants.get(dataset) or tenant_of(dataset_of(dataset))
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
    # A buffer's Kind, by its key, when it isn't a call log.
    kinds: dict[str, Kind] = field(default_factory=dict)

    # How often the pod's own reports load when nothing else takes them, and how a
    # notified buffer's batches are spaced.
    report_seconds: float = INTERVAL_SECONDS
    wait_seconds: float = WAIT_SECONDS
    gap_seconds: float = GAP_SECONDS
    # The pod's reports waiting for their project's next reports job, when each project's
    # reports last loaded, the names storage told of, by buffer (each with its size and when
    # it was told), when each buffer was last worked on, and the buffers a sweep must settle.
    pending: dict[str, list[dict]] = field(default_factory=dict)
    reports_loaded: dict[str, float] = field(default_factory=dict)
    queued: dict[str, dict[str, tuple[int, float]]] = field(default_factory=dict)
    worked: dict[str, float] = field(default_factory=dict)
    unsettled: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        # Counted from the pod's start: a new pod's first report waits for a reports job a while.
        self.started = self.clock()

    def kind(self, dataset: str) -> Kind:
        return self.kinds.get(dataset, CALLS)

    def tick(self, datasets: list[str]) -> None:
        """A sweep: every buffer listed, and what it finds worked through."""
        for dataset in datasets:
            done = self._scan(dataset)
            with self.lock:
                for name in done:
                    self.queued.get(dataset, {}).pop(name, None)

    def notified(self, dataset: str, name: str, size: int) -> None:
        """Storage took `name` into the buffer: it goes in that buffer's next batch. Safe
        from any thread; the same name twice is one."""
        with self.lock:
            self.queued.setdefault(dataset, {}).setdefault(name, (size, self.clock()))

    def drain(self) -> None:
        """The batches that are due, from the names storage told of. A buffer that didn't
        settle last time is swept instead, as only a listing finds what it left."""
        now = self.clock()
        for dataset in sorted(self.queued):
            with self.lock:
                queued = dict(self.queued.get(dataset, {}))
            if not queued or now - min(at for _, at in queued.values()) < self.wait_seconds:
                continue
            if now - self.worked.get(dataset, float("-inf")) < self.gap_seconds:
                continue
            if dataset in self.unsettled:
                self.tick([dataset])
                continue
            chosen = self._choose([Waiting(name, size, at) for name, (size, at) in sorted(queued.items())])
            done = self._scan(dataset, chosen)
            with self.lock:
                for name in done:
                    self.queued[dataset].pop(name, None)

    def _scan(self, dataset: str, chosen: list[str] | None = None) -> set[str]:
        """One project's work, a sweep's or, with `chosen`, those names alone, and its
        report. The names it settled come back."""
        started = self.clock()
        self.worked[dataset] = started
        done: set[str] = set()
        status = {"kind": "ingest", "sender": self.sender, "version": __version__, "storage_ok": True,
                  "loaded_files": 0, "rejected_now": 0}
        problems: list[str] = []
        try:
            if chosen is None:
                self._work(dataset, status, problems, done)
            else:
                self._take(dataset, chosen, status, problems, done)
        except StorageDown as e:
            status["storage_ok"] = False
            problems.append(str(e))
            self.unsettled.add(dataset)
        except Exception as e:  # noqa: BLE001 - one project's bad day must not stop the rest
            problems.append(f"{type(e).__name__}: {e}")
            self.unsettled.add(dataset)
        ended = self.clock()
        kind = self.kind(dataset)
        status.update(at=timestamp(ended), cycle_start=timestamp(started), cycle_end=timestamp(ended),
                      jobs_today=self.jobs.used(dataset_of(dataset), kind.table),
                      status_jobs_today=self.jobs.used(dataset_of(dataset), STATUS_TABLE))
        if not kind.reports:
            # Its report goes to the log alone: the buffer holds only what it is for.
            self.report({**status, "dataset": dataset_of(dataset), "table": kind.table,
                         "problems": "; ".join(problems) or None})
            return done
        own = {**status, "problems": "; ".join(problems) or None}
        kept = self.pending.setdefault(dataset, [])
        kept.append(own)
        del kept[:-REPORTS_KEPT]
        self._load_own(dataset, problems)
        self.report({**status, "dataset": dataset, "problems": "; ".join(problems) or None})
        return done

    def _load_own(self, dataset: str, problems: list[str]) -> None:
        """The pod's reports as a job of their own, once none has gone with a batch for
        report_seconds (a tenth early, so passes that drift don't skip one)."""
        since = self.clock() - self.reports_loaded.get(dataset, self.started)
        own = self.pending.get(dataset)
        if not own or since < self.report_seconds * 0.9:
            return
        if not self.jobs.may_load(dataset_of(dataset), STATUS_TABLE):
            problems.append("the pod's report waits: the day's load jobs are used")
            return
        self.jobs.spend(dataset_of(dataset), STATUS_TABLE)
        rows = list(own)
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        result = self.loader.load(dataset_of(dataset), encode_status(rows), job_id("ingest", digest), STATUS_TABLE)
        if result.outcome is Outcome.RETRY:
            problems.append(f"the pod's report waits: {result.detail}")
            return
        if result.outcome is Outcome.REJECTED:
            problems.append(f"the pod's reports rejected: {result.detail}")
        self._loaded_own(dataset, len(rows))

    def _loaded_own(self, dataset: str, count: int) -> None:
        del self.pending.get(dataset, [])[:count]
        self.reports_loaded[dataset] = self.clock()

    def _work(self, dataset: str, status: dict, problems: list[str], done: set[str]) -> None:
        finished = True
        for record in self.inbox.list(dataset, self.kind(dataset).batches)[0]:
            finished = self._resume(dataset, record.name, status, problems, done) and finished
        waiting, more = self.inbox.list(dataset, self.kind(dataset).folder)
        if finished and waiting:
            finished = self._batch(dataset, self._choose(waiting), done, status, problems)
        if finished:
            self.unsettled.discard(dataset)
        else:
            self.unsettled.add(dataset)
        left = [f for f in waiting if f.name not in done]
        rejected, rejected_more = self.inbox.list(dataset, REJECTED)
        status.update(inbox_files=len(left), inbox_bytes=sum(f.size for f in left), inbox_more=more,
                      inbox_oldest_seconds=self._age(left), rejected_files=len(rejected),
                      rejected_more=rejected_more, rejected_oldest_seconds=self._age(rejected))

    def _take(self, dataset: str, files: list[str], status: dict, problems: list[str], done: set[str]) -> None:
        """The names storage told of, as one batch, with nothing listed. What is left
        waiting is what the pod was told of and hasn't settled."""
        if not self._batch(dataset, files, done, status, problems):
            self.unsettled.add(dataset)
        with self.lock:
            left = [Waiting(n, size, at) for n, (size, at) in self.queued.get(dataset, {}).items() if n not in done]
        status.update(inbox_files=len(left), inbox_bytes=sum(f.size for f in left), inbox_more=None,
                      inbox_oldest_seconds=self._age(left))

    def _batch(self, dataset: str, files: list[str], done: set[str], status: dict, problems: list[str]) -> bool:
        """A batch record for `files`, then the batch. True once it is settled."""
        record = f"{self.kind(dataset).batches}{_batch_job(files, 0)}.json"
        held = {"files": files, "attempt": 0, "status_attempt": 0, "status_done": False}
        self.inbox.put(dataset, record, json.dumps(held).encode())
        return self._run(dataset, record, held, done, status, problems)

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

    def _resume(self, dataset: str, record: str, status: dict, problems: list[str], done: set[str]) -> bool:
        data = self.inbox.read(dataset, record)
        if data is None:
            return True
        gone: set[str] = set()
        finished = self._run(dataset, record, json.loads(data), gone, status, problems)
        done.update(gone)
        return finished

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
        a new job for its calls can't load them twice. The pod's own reports go with them.
        The job's id is its files', so a try after a crash is the same job; the pod's
        reports in a job that had already gone are the ones lost."""
        own = list(self.pending.get(dataset, [])) if self.kind(dataset).reports else []
        reports = reports + own
        if not reports or held["status_done"]:
            return True
        if not self.jobs.may_load(dataset_of(dataset), STATUS_TABLE):
            problems.append("waiting to load reports: the day's load jobs are used")
            return False
        self.jobs.spend(dataset_of(dataset), STATUS_TABLE)
        digest = hashlib.sha256(json.dumps([held["status_attempt"], sorted(held["files"])]).encode()).hexdigest()
        result = self.loader.load(dataset_of(dataset), encode_status(reports), job_id("status", digest), STATUS_TABLE)
        if result.outcome is Outcome.RETRY:
            problems.append(f"waiting to load reports: {result.detail}")
            if result.ended:
                held["status_attempt"] += 1
                self.inbox.put(dataset, record, json.dumps(held).encode())
            return False
        if result.outcome is Outcome.REJECTED:
            problems.append(f"reports rejected: {result.detail}")
        held["status_done"] = True
        self._loaded_own(dataset, len(own))
        return True

    def _settle(self, dataset, files, rows, contents, attempt, gone, status, problems) -> LoadResult:
        records = [r for name in files for r in rows.get(name, [])]
        if not records:
            self._forget(dataset, files, gone)
            return LoadResult(Outcome.LOADED)
        kind = self.kind(dataset)
        if not self.jobs.may_load(dataset_of(dataset), kind.table):
            return LoadResult(Outcome.RETRY, "the day's load jobs are used")
        try:
            data = kind.encode(records)
        except Exception as e:  # noqa: BLE001 - a file that decodes can still not be a call record
            result = LoadResult(Outcome.REJECTED, f"not {kind.what}: {type(e).__name__}")
        else:
            self.jobs.spend(dataset_of(dataset), kind.table)
            result = self.loader.load(dataset_of(dataset), data, _batch_job(files, attempt), kind.table)
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
