"""What the ingest pod runs: integrations.ingest.main:app. Its scanner starts with
the app. Settings come from the environment, and a key from a mounted Secret.
INGEST_SIGNALS, when set, also loads observability's signals: {"tenant": ...,
"dataset": ...}, the tenant its evaluator writes them to and the dataset whose
signals table they load into."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..calls import Caller, Check, Network, StdoutSink
from .app import create_app
from .bigquery import BigQueryLoader, JobBudget
from .google_token import GoogleToken
from .inbox import INTERVAL_SECONDS, SIGNALS, Inbox, Kind, Scanner


def signals(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_SIGNALS as the scanner takes it: the signals dataset's tenant, and its Kind."""
    if not text.strip():
        return {}, {}
    given = json.loads(text)
    if not isinstance(given, dict) or set(given) != {"tenant", "dataset"} or not all(
            isinstance(v, str) and v for v in given.values()):
        raise ValueError('INGEST_SIGNALS is {"tenant": ..., "dataset": ...}')
    return {given["dataset"]: given["tenant"]}, {given["dataset"]: SIGNALS}


def build():  # pragma: no cover - the pod's wiring, with real paths and keys
    datasets = json.loads(os.environ["INGEST_DATASETS"])
    tenants, kinds = signals(os.environ.get("INGEST_SIGNALS", ""))
    caller = Caller("integrations", "ingest", sink=StdoutSink())
    key = json.loads(Path(os.environ["INGEST_KEY_FILE"]).read_text(encoding="utf-8"))
    storage_token = Path(os.environ["INGEST_STORAGE_TOKEN_FILE"])
    loader = BigQueryLoader(caller, GoogleToken(caller, key), key["project_id"])
    inbox = Inbox(Network(), lambda: storage_token.read_text(encoding="utf-8").strip(), os.environ["INGEST_STORAGE_URL"],
                  tenants)
    scanner = Scanner(inbox, loader, JobBudget(), report=lambda status: print(json.dumps(status), flush=True),
                      sender=os.environ.get("HOSTNAME", "ingest"), kinds=kinds)
    buffers = sorted(set(datasets.values()) | set(kinds))

    ticked = [time.monotonic()]

    def run() -> None:
        while True:
            time.sleep(INTERVAL_SECONDS)
            scanner.tick(buffers)
            ticked[0] = time.monotonic()

    threading.Thread(target=run, daemon=True).start()
    return create_app(lambda: [scanning(time.monotonic() - ticked[0])])


def scanning(since: float) -> Check:
    """The scanner works while it finishes a pass every few intervals: a pass that
    hangs, or a thread that died, stops the clock."""
    return Check("ingest.scanner", since < 3 * INTERVAL_SECONDS, f"last pass {int(since)} s ago")


app = build() if os.environ.get("INGEST_KEY_FILE") else None
