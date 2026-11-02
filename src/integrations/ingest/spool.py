"""Records on disk, so a restart loses nothing: one open file per dataset, closed
into a batch that stays until its load, or its move to overflow, has succeeded."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

OPEN = "open.jsonl"
BATCH_SUFFIX = ".batch"


@dataclass(frozen=True)
class Batch:
    dataset: str
    path: Path
    digest: str

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]


class Spool:
    def __init__(self, root: Path, max_bytes: int = 500_000_000):
        self.root, self.max_bytes = root, max_bytes
        self.dropped = 0
        root.mkdir(parents=True, exist_ok=True)

    def size(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    def append(self, dataset: str, records: list[dict]) -> None:
        """On disk before this returns. At the hard cap the newest are refused and
        counted: older ones may already be on their way to overflow."""
        lines = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode()
        if self.size() + len(lines) > self.max_bytes:
            self.dropped += len(records)
            return
        folder = self.root / dataset
        folder.mkdir(exist_ok=True)
        with (folder / OPEN).open("ab") as f:
            f.write(lines)
            f.flush()
            os.fsync(f.fileno())

    def close_open_files(self) -> None:
        """Closes each dataset's open file into a batch named by its content, so a
        batch closed twice by a crash is still one batch."""
        for folder in self._datasets():
            current = folder / OPEN
            if current.exists() and current.stat().st_size:
                digest = hashlib.sha256(current.read_bytes()).hexdigest()
                current.rename(folder / f"{digest}{BATCH_SUFFIX}")

    def batches(self) -> list[Batch]:
        """Oldest first."""
        found = [Batch(folder.name, path, path.name.removesuffix(BATCH_SUFFIX))
                 for folder in self._datasets() for path in folder.glob(f"*{BATCH_SUFFIX}")]
        return sorted(found, key=lambda b: b.path.stat().st_mtime)

    def _datasets(self) -> list[Path]:
        return sorted(p for p in self.root.iterdir() if p.is_dir())
