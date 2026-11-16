"""CI's wait times and compute load, collected for platform.ci_jobs and
platform.cluster_usage (Sustenaut/shared-infra's infra/platform.tf), through storage's
ci-usage buffer, which the ingest pod loads (INGEST_BUFFERS).

- `jobs`: every finished GitHub Actions job of each repository on the release board, once,
  with how long it waited for a runner (integrations ci-usage jobs).
- `cluster`: the k3s server's usage every two minutes, and each runner pod's peak memory
  (integrations ci-usage cluster).

Both record passes and quiet times alike: the rows are facts for a rule designed later.
"""

from __future__ import annotations

import hashlib
from datetime import datetime


def place(folder: str, sender: str, observed: datetime, data: bytes) -> str:
    """A file's place in the buffer: by day and time, named by its content's hash, so a
    retry sends the same name and replaces it."""
    return f"{folder}{observed:%Y-%m-%d}/{observed:%H%M%S}-{sender}-{hashlib.sha256(data).hexdigest()[:16]}.avro"
