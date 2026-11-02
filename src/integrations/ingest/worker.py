"""What happens every two minutes: close the open files, load each batch, and move
what BigQuery won't take to overflow. Once a load works, replay what is waiting."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable

from .avrofile import decode, encode
from .bigquery import BigQueryLoader, JobBudget, Outcome
from .overflow import PENDING, REJECTED, Overflow, OverflowUnavailable
from .spool import Batch, Spool

INTERVAL_SECONDS = 120
MAX_REPLAY_FILES = 50


def job_id(prefix: str, digest: str) -> str:
    return f"{prefix}_{digest[:48]}"


@dataclass
class Worker:
    spool: Spool
    loader: BigQueryLoader
    overflow: Overflow
    alert: Callable[[str], None]
    overflow_after_seconds: float = 3600
    overflow_above_bytes: int = 100_000_000
    clock: Callable[[], float] = time.time
    jobs: JobBudget | None = None
    first_failed: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.jobs = self.jobs or JobBudget(self.clock)

    def _load(self, dataset: str, data: bytes, job: str):
        self.jobs.spend(dataset)
        return self.loader.load(dataset, data, job)

    def tick(self) -> None:
        self.spool.close_open_files()
        loaded = set()
        for batch in self.spool.batches():
            if self.jobs.may_load(batch.dataset) and self._handle(batch):
                loaded.add(batch.dataset)
        for dataset in sorted(loaded):
            self._replay(dataset)

    def _handle(self, batch: Batch) -> bool:
        """True when this batch loaded."""
        data = encode(batch.records())
        job = job_id("calls", batch.digest)
        result = self._load(batch.dataset, data, job)
        if result.outcome is Outcome.LOADED:
            batch.path.unlink()
            self.first_failed.pop(batch.digest, None)
            return True
        if result.outcome is Outcome.REJECTED:
            self._to_overflow(batch, REJECTED, data, job)
            self.alert(f"BigQuery rejected a call log file for {batch.dataset}: {result.detail}")
            return False
        since = self.first_failed.setdefault(batch.digest, self.clock())
        if self.clock() - since >= self.overflow_after_seconds or self.spool.size() >= self.overflow_above_bytes:
            self._to_overflow(batch, PENDING, data, job)
        return False

    def _to_overflow(self, batch: Batch, kind: str, data: bytes, job: str) -> None:
        try:
            self.overflow.put(kind, batch.dataset, data, job)
        except OverflowUnavailable as e:
            self.alert(f"Storage did not take a call log file for {batch.dataset}, so it stays in the spool: {e}")
            return
        batch.path.unlink()
        self.first_failed.pop(batch.digest, None)

    def _replay(self, dataset: str) -> None:
        """What was waiting goes as one merged load, oldest first, so catching up
        spends one job of the day."""
        try:
            entries = self.overflow.pending(dataset)[:MAX_REPLAY_FILES]
            if not entries or not self.jobs.may_load(dataset):
                return
            records = [r for entry in entries for r in decode(self.overflow.read(dataset, entry))]
            digest = hashlib.sha256("".join(e.job_id for e in entries).encode()).hexdigest()
            if self._load(dataset, encode(records), job_id("replay", digest)).outcome is Outcome.LOADED:
                self.overflow.forget(PENDING, dataset, entries)
        except OverflowUnavailable as e:
            self.alert(f"Could not replay {dataset}'s call log files yet: {e}")
