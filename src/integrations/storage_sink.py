"""Call records into a project's buffer in the storage service: one file every two
minutes, even with no calls in it, whose metadata is the sender's report. So a quiet
service can be told from one that has stopped, and this process keeps working while
the ingest pod or BigQuery is down.

Writing a record never waits on the network and never fails. Records wait in memory up
to a limit, and past it the oldest are dropped and counted. A file storage didn't take
is sent again as it was, under the same name, so a lost answer can't double its calls.
Only what storage has taken is safe from a restart.
"""

from __future__ import annotations

import atexit
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Callable

from . import __version__
from .avrofile import encode, file_name, timestamp
from .calls import CallRecord, Request, Transport

EVERY_SECONDS = 120
TIMEOUT = 60


class StorageSink:
    def __init__(self, datasets: dict[str, str], *, transport: Transport, token: Callable[[], str], base_url: str,
                 sender: str, every_seconds: float = EVERY_SECONDS, limit: int = 10_000,
                 clock: Callable[[], float] = time.time, start: bool = True):
        self.datasets, self.transport, self.token = datasets, transport, token
        self.base, self.sender, self.every_seconds, self.limit = base_url.rstrip("/"), sender, every_seconds, limit
        self.clock = clock
        names = sorted(set(datasets.values()))
        self.waiting: dict[str, deque[dict]] = {name: deque() for name in names}
        self.held: dict[str, tuple[str, bytes]] = {}
        self.dropped = dict.fromkeys(names, 0)
        self.failures = dict.fromkeys(names, 0)
        self.peak = dict.fromkeys(names, 0)
        self.started = clock()
        self.lock = threading.Lock()
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

    def flush(self) -> None:
        """One file per project, whatever has happened since the last."""
        started, ended = self.started, self.clock()
        self.started = ended
        for dataset in self.waiting:
            self._flush(dataset, started, ended)

    def _flush(self, dataset: str, started: float, ended: float) -> None:
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
                      "storage_failures": self.failures[dataset]}
            self.dropped[dataset] = self.failures[dataset] = self.peak[dataset] = 0
        data = encode(batch, status)
        name = file_name(ended, self.sender, data)
        if not self._put(dataset, name, data):
            self.held[dataset] = name, data

    def _put(self, dataset: str, name: str, data: bytes) -> bool:
        tenant = dataset.replace("_", "-")
        try:
            token = self.token()
        except OSError:
            token = ""
        answer = self.transport.send(Request("PUT", f"{self.base}/{tenant}/{name}", {"Authorization": f"Bearer {token}"},
                                             data), TIMEOUT)
        if not answer.ok:
            with self.lock:
                self.failures[dataset] += 1
        return answer.ok

    def _run(self) -> None:  # pragma: no cover - the process's own thread
        while True:
            time.sleep(self.every_seconds)
            self.flush()
