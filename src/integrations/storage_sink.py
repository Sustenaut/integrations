"""Call records into a project's buffer in the storage service: one file every two
minutes, even with no calls in it, whose metadata is the sender's report. So a quiet
service can be told from one that has stopped, and this process keeps working while
the ingest pod or BigQuery is down.

Writing a record never fails. Records wait in memory up to a limit, and past it the
oldest are dropped and counted. A file storage didn't take is sent again as it was,
under the same name, so a lost answer can't double its calls. Only what storage has
taken is safe from a restart.

A web app can also say how long it took to answer each request, with ``page``. Its
report then gives the window's count and its quickest, median and slowest answer, for the
whole app, and ``cold_start`` says the process started in that window.

A process that only has CPU while it answers a request, such as a Cloud Run service with
CPU throttled between requests, never runs the background thread on time. Made with
``flush_on_write``, the sink also writes its files from the request that finds them
overdue, so that request waits on storage, once every two minutes at most.

The background thread's first file comes a random part of a cycle in, so pods that
started together don't all send at the same moment.

A sender can instead write only when it has something to say. With a deadline
(``deadline_seconds``, else CALL_LOG_DEADLINE_SECONDS) or a heartbeat
(``heartbeat_seconds``, else CALL_LOG_HEARTBEAT_SECONDS), each dataset's file goes once
its first unsent call is ``deadline`` seconds old, and a dataset with no calls writes a
report alone once ``heartbeat`` seconds have passed since its last file; the one not set
is ``every_seconds``. A file storage didn't take is tried again once a deadline later.
The report's counters (drops, failures, the page times) keep counting until the next
file. With neither set, it is the cycle above, a file every ``every_seconds``.
"""

from __future__ import annotations

import atexit
import os
import random
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Callable

from . import __version__
from .avrofile import encode, file_name, timestamp
from .calls import CallRecord, Caller, Transport
from .clients.storage import Storage, StorageError, StorageUnavailable

EVERY_SECONDS = 120
DEADLINE_VARIABLE = "CALL_LOG_DEADLINE_SECONDS"
HEARTBEAT_VARIABLE = "CALL_LOG_HEARTBEAT_SECONDS"
# How often the background thread looks for a file that is due, at most.
LOOK_SECONDS = 1.0
# The page times a window keeps for its median; past this it goes on counting.
PAGE_LIMIT = 10_000


class StorageSink:
    def __init__(self, datasets: dict[str, str], *, transport: Transport, token: Callable[[], str], base_url: str,
                 sender: str, every_seconds: float = EVERY_SECONDS, limit: int = 10_000,
                 clock: Callable[[], float] = time.time, start: bool = True, flush_on_write: bool = False,
                 deadline_seconds: float | None = None, heartbeat_seconds: float | None = None,
                 random: Callable[[], float] = random.random):
        self.datasets, self.transport, self.token = datasets, transport, token
        deadline = deadline_seconds if deadline_seconds is not None else seconds(DEADLINE_VARIABLE)
        heartbeat = heartbeat_seconds if heartbeat_seconds is not None else seconds(HEARTBEAT_VARIABLE)
        # None: the cycle. Else when a call must be sent by, and how often a quiet dataset reports.
        self.deadline = None if deadline is None and heartbeat is None else (deadline or every_seconds)
        self.heartbeat = heartbeat or every_seconds
        # Its own calls to storage are the call log's plumbing, so nothing records them.
        self.caller = Caller("integrations", f"storage-sink:{sender}", transport=transport)
        self.base, self.sender, self.every_seconds, self.limit = base_url.rstrip("/"), sender, every_seconds, limit
        self.clock, self.flush_on_write = clock, flush_on_write
        names = sorted(set(datasets.values()))
        self.waiting: dict[str, deque[dict]] = {name: deque() for name in names}
        self.held: dict[str, tuple[str, bytes]] = {}
        self.dropped = dict.fromkeys(names, 0)
        self.failures = dict.fromkeys(names, 0)
        self.peak = dict.fromkeys(names, 0)
        self.pages: list[float] = []
        self.page_count = 0
        self.cold = True  # until the first report goes out
        self.started = clock()
        # Each dataset's window: since its last file, when its first unsent call came, and
        # when it next reports with nothing to say, the first a random part of a heartbeat in.
        self.since = dict.fromkeys(names, self.started)
        self.first: dict[str, float | None] = dict.fromkeys(names)
        self.beat = {name: self.started + random() * self.heartbeat for name in names}
        self.lock = threading.Lock()
        self.flushing = threading.Lock()  # one flush at a time, whoever starts it
        if start:  # pragma: no cover - the process's own thread
            threading.Thread(target=self._run, daemon=True).start()
            atexit.register(self.flush)

    def write(self, record: CallRecord) -> None:
        with self.lock:
            dataset = self.datasets.get(record.repo)
            if dataset is None:
                # On behalf of a project with no buffer: reported, on the first there is.
                for name in sorted(self.dropped)[:1]:
                    self.dropped[name] += 1
                return
            waiting = self.waiting[dataset]
            if len(waiting) >= self.limit:
                waiting.popleft()
                self.dropped[dataset] += 1
            waiting.append(asdict(record))
            self.peak[dataset] = max(self.peak[dataset], len(waiting))
            if self.first[dataset] is None:
                self.first[dataset] = self.clock()
            due = self.flush_on_write and bool(self.due())
        # Outside the lock, and only if no other flush is under way.
        if due and self.flushing.acquire(blocking=False):
            try:
                self._flush_due()
            finally:
                self.flushing.release()

    def due(self) -> list[str]:
        """The datasets whose file is due now: in the cycle, all of them once a cycle is
        over; with a deadline, those whose first call has waited it, whose file storage
        didn't take a deadline ago, or whose heartbeat has come."""
        now = self.clock()
        if self.deadline is None:
            return sorted(self.waiting) if now - self.started >= self.every_seconds else []
        return [name for name in sorted(self.waiting)
                if (self.first[name] is not None and now - self.first[name] >= self.deadline)
                or (name in self.held and now - self.since[name] >= self.deadline)
                or now >= self.beat[name]]

    def page(self, ms: float) -> None:
        """How long the app took to answer one request, in milliseconds."""
        with self.lock:
            self.page_count += 1
            if len(self.pages) < PAGE_LIMIT:
                self.pages.append(ms)

    def _page_report(self) -> dict:
        """The window's page times, for every project's report, and then forgotten."""
        with self.lock:
            pages, count, cold = self.pages, self.page_count, self.cold
            self.pages, self.page_count, self.cold = [], 0, False
        if not pages:
            return {"page_count": count, "page_ms_low": None, "page_ms_median": None, "page_ms_high": None,
                    "cold_start": cold}
        return {"page_count": count, "page_ms_low": round(min(pages)), "page_ms_median": round(statistics.median(pages)),
                "page_ms_high": round(max(pages)), "cold_start": cold}

    def flush(self) -> None:
        """One file per project, whatever has happened since the last."""
        with self.flushing:
            self._flush_all()

    def flush_due(self) -> None:
        """The files that are due, and no others (due)."""
        with self.flushing:
            self._flush_due()

    def _flush_all(self) -> None:
        self._flush_these(sorted(self.waiting), every=True)

    def _flush_due(self) -> None:
        names = self.due()
        if names:
            self._flush_these(names, every=self.deadline is None)

    def _flush_these(self, names: list[str], every: bool) -> None:
        """Each dataset's file. Unless `every`, a dataset whose held file was all that was
        due sends that, and no report alone before its heartbeat."""
        ended = self.clock()
        started, self.started = self.started, ended
        pages = self._page_report()
        for dataset in names:
            new = every or bool(self.waiting[dataset]) or ended >= self.beat[dataset]
            # The cycle's window is the sink's; with a deadline, each dataset's own.
            since = started if self.deadline is None else self.since[dataset]
            self.since[dataset] = ended
            if new:
                self.first[dataset] = None
                self.beat[dataset] = ended + self.heartbeat
            self._flush(dataset, since, ended, pages, new)

    def _flush(self, dataset: str, started: float, ended: float, pages: dict, new: bool = True) -> None:
        if dataset in self.held:
            if not self._put(dataset, *self.held[dataset]):
                return
            del self.held[dataset]
        if not new:
            return
        with self.lock:
            batch = list(self.waiting[dataset])
            self.waiting[dataset].clear()
            status = {"kind": "sender", "sender": self.sender, "version": __version__, "at": timestamp(ended),
                      "cycle_start": timestamp(started), "cycle_end": timestamp(ended), "calls": len(batch),
                      "drops": self.dropped[dataset], "buffer_size": self.peak[dataset],
                      "storage_failures": self.failures[dataset], **pages}
            self.dropped[dataset] = self.failures[dataset] = self.peak[dataset] = 0
        data = encode(batch, status)
        name = file_name(ended, self.sender, data)
        if not self._put(dataset, name, data):
            self.held[dataset] = name, data

    def _put(self, dataset: str, name: str, data: bytes) -> bool:
        """One file, through the storage client, as storage.api's sink operation: tried once,
        for up to a minute, and not itself recorded (its caller keeps nothing)."""
        no_token = ""

        def token() -> str:
            nonlocal no_token
            try:
                return self.token()
            except OSError as e:
                no_token = f"; no token to send: {e}"
                return ""

        tenant = dataset.replace("_", "-")
        storage = Storage(self.caller, tenant, base_url=self.base, token=token)
        try:
            storage.put_buffer(name, data, as_operation="sink")
            return True
        except (StorageError, StorageUnavailable) as e:
            # Counted in the next report, and said here too, for the process's own log, with
            # storage's own reason: it says why a token was refused, never what was in it.
            why = f"HTTP {e.status} {e.error}".rstrip() if e.status is not None else e.detail
        with self.lock:
            self.failures[dataset] += 1
        print(f"integrations: storage didn't take {name} for {tenant}: {why}{no_token}", file=sys.stderr, flush=True)
        return False

    def _run(self) -> None:  # pragma: no cover - the process's own thread
        if self.deadline is not None:
            while True:
                time.sleep(min(LOOK_SECONDS, self.deadline))
                self.flush_due()
        time.sleep(first_wait(self.every_seconds))
        while True:
            self.flush()
            time.sleep(self.every_seconds)


def seconds(variable: str, environ=os.environ) -> float | None:
    """A number of seconds the environment gives, above 0, or None when it is unset or empty."""
    text = environ.get(variable, "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise ValueError(f"{variable} is a number of seconds above 0, not {text!r}")
    return value


def first_wait(every_seconds: float, random: Callable[[], float] = random.random) -> float:
    """The thread's first file comes a random part of a cycle in, so pods started together
    don't all send at once; after that, one every cycle."""
    return random() * every_seconds
