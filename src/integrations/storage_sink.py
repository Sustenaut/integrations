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
"""

from __future__ import annotations

import atexit
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
# The page times a window keeps for its median; past this it goes on counting.
PAGE_LIMIT = 10_000


class StorageSink:
    def __init__(self, datasets: dict[str, str], *, transport: Transport, token: Callable[[], str], base_url: str,
                 sender: str, every_seconds: float = EVERY_SECONDS, limit: int = 10_000,
                 clock: Callable[[], float] = time.time, start: bool = True, flush_on_write: bool = False):
        self.datasets, self.transport, self.token = datasets, transport, token
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
            due = self.flush_on_write and self.clock() - self.started >= self.every_seconds
        # Outside the lock, and only if no other flush is under way.
        if due and self.flushing.acquire(blocking=False):
            try:
                self._flush_all()
            finally:
                self.flushing.release()

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

    def _flush_all(self) -> None:
        started, ended = self.started, self.clock()
        self.started = ended
        pages = self._page_report()
        for dataset in self.waiting:
            self._flush(dataset, started, ended, pages)

    def _flush(self, dataset: str, started: float, ended: float, pages: dict) -> None:
        if dataset in self.held:
            if not self._put(dataset, *self.held[dataset]):
                return
            del self.held[dataset]
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
        while True:
            time.sleep(self.every_seconds)
            self.flush()

