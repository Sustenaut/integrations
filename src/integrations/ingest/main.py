"""What the ingest pod runs: integrations.ingest.main:app. Its scanner starts with
the app. Settings come from the environment, and a key from a mounted Secret.
INGEST_SIGNALS, when set, also loads observability's signals: {"tenant": ...,
"dataset": ...}, the tenant its evaluator writes them to and the dataset whose
signals table they load into. INGEST_NEED_STATES, in the same shape, loads the
convergence engine's need states into that dataset's need_states table, and
INGEST_PUBKIT_CHECKS the platform's nightly pubkit checks into its pubkit_checks table."""

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
from .inbox import INTERVAL_SECONDS, NEED_STATES, PUBKIT_CHECKS, SIGNALS, Inbox, Kind, Scanner


def buffer(text: str, variable: str, kind: Kind) -> tuple[dict[str, str], dict[str, Kind]]:
    """A {"tenant": ..., "dataset": ...} variable as the scanner takes it: the dataset's
    tenant, and its Kind."""
    if not text.strip():
        return {}, {}
    given = json.loads(text)
    if not isinstance(given, dict) or set(given) != {"tenant", "dataset"} or not all(
            isinstance(v, str) and v for v in given.values()):
        raise ValueError(f'{variable} is {{"tenant": ..., "dataset": ...}}')
    return {given["dataset"]: given["tenant"]}, {given["dataset"]: kind}


def signals(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_SIGNALS: observability's signals."""
    return buffer(text, "INGEST_SIGNALS", SIGNALS)


def need_states(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_NEED_STATES: the convergence engine's need states."""
    return buffer(text, "INGEST_NEED_STATES", NEED_STATES)


def pubkit_checks(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_PUBKIT_CHECKS: the platform's nightly checks of each repository against pubkit."""
    return buffer(text, "INGEST_PUBKIT_CHECKS", PUBKIT_CHECKS)


def named_buffers(signals_text: str, need_states_text: str,
                  pubkit_checks_text: str = "") -> tuple[dict[str, str], dict[str, Kind]]:
    """All of them, which must name different datasets: a dataset holds one Kind of buffer."""
    tenants: dict[str, str] = {}
    kinds: dict[str, Kind] = {}
    named: dict[str, str] = {}
    for variable, (more_tenants, more_kinds) in (
            ("INGEST_SIGNALS", signals(signals_text)), ("INGEST_NEED_STATES", need_states(need_states_text)),
            ("INGEST_PUBKIT_CHECKS", pubkit_checks(pubkit_checks_text))):
        for dataset in more_kinds:
            if dataset in named:
                raise ValueError(f"{named[dataset]} and {variable} name the same dataset")
            named[dataset] = variable
        tenants.update(more_tenants)
        kinds.update(more_kinds)
    return tenants, kinds


def build():  # pragma: no cover - the pod's wiring, with real paths and keys
    datasets = json.loads(os.environ["INGEST_DATASETS"])
    tenants, kinds = named_buffers(os.environ.get("INGEST_SIGNALS", ""), os.environ.get("INGEST_NEED_STATES", ""),
                                   os.environ.get("INGEST_PUBKIT_CHECKS", ""))
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
    threading.Thread(target=scan, args=(scanner, buffers, ticked), daemon=True).start()
    return create_app(lambda: [scanning(time.monotonic() - ticked[0])])


def scan(scanner: Scanner, buffers: list[str], ticked: list[float], *, sleep=time.sleep, clock=time.monotonic) -> None:
    """The scanner's loop: a pass straight away, then one every interval. Passing first
    means a new pod reports at once, so a deploy leaves no gap longer than its restart."""
    while True:
        scanner.tick(buffers)
        ticked[0] = clock()
        sleep(INTERVAL_SECONDS)


def scanning(since: float) -> Check:
    """The scanner works while it finishes a pass every few intervals: a pass that
    hangs, or a thread that died, stops the clock."""
    return Check("ingest.scanner", since < 3 * INTERVAL_SECONDS, f"last pass {int(since)} s ago")


app = build() if os.environ.get("INGEST_KEY_FILE") else None
