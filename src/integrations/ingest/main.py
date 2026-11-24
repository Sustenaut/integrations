"""What the ingest pod runs: integrations.ingest.main:app. Its scanner starts with
the app. Settings come from the environment, and a key from a mounted Secret.
INGEST_SIGNALS, when set, also loads observability's signals: {"tenant": ...,
"dataset": ...}, the tenant its evaluator writes them to and the dataset whose
signals table they load into. INGEST_NEED_STATES, in the same shape, loads the
convergence engine's need states into that dataset's need_states table, and
INGEST_PUBRULES_CHECKS the platform's nightly pubrules checks into its pubrules_checks table.

INGEST_BUFFERS names any number more, as a list: [{"tenant": ..., "dataset": ..., "table":
...}], each the Kind whose table it names (inbox.KINDS), so one dataset may hold several
(platform's ci_jobs and cluster_usage, both from the tenant ci-usage, beside its
pubrules_checks). The three variables above keep working as they are.

INGEST_NOTIFY_KEY turns on storage's notices (notify.py): without it, POST /ingest/notify
refuses everything and the sweep alone finds the files. INGEST_SWEEP_SECONDS is how often
the sweep lists every buffer, INGEST_REPORT_SECONDS how often the pod's own reports load
when no batch takes them, and INGEST_BATCH_GAP_SECONDS how far apart a notified buffer's
batches are (inbox.py); unset, they are 120, 120 and 120."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..calls import Caller, Check, Network, StdoutSink
from .app import create_app
from .bigquery import BigQueryLoader, JobBudget
from .google_token import google_token, project_of
from .inbox import (CALLS, GAP_SECONDS, INTERVAL_SECONDS, KINDS, NEED_STATES, PUBRULES_CHECKS, SIGNALS, Inbox, Kind,
                    Scanner, dataset_of, tenant_of)
from .notify import Notices

# The scan loop's longest sleep between looks at what storage told of.
STEP_SECONDS = 1.0


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


def pubrules_checks(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_PUBRULES_CHECKS: the platform's nightly checks of each repository against pubrules."""
    return buffer(text, "INGEST_PUBRULES_CHECKS", PUBRULES_CHECKS)


def buffers(text: str) -> tuple[dict[str, str], dict[str, Kind]]:
    """INGEST_BUFFERS: each buffer's tenant and Kind, by its key, `<dataset>.<table>`."""
    if not text.strip():
        return {}, {}
    given = json.loads(text)
    shape = 'INGEST_BUFFERS is [{"tenant": ..., "dataset": ..., "table": ...}]'
    if not isinstance(given, list):
        raise ValueError(shape)
    tenants: dict[str, str] = {}
    kinds: dict[str, Kind] = {}
    for entry in given:
        if not isinstance(entry, dict) or set(entry) != {"tenant", "dataset", "table"} or not all(
                isinstance(v, str) and v for v in entry.values()):
            raise ValueError(shape)
        if entry["table"] not in KINDS:
            raise ValueError(f"INGEST_BUFFERS names table {entry['table']}, which is none of {', '.join(sorted(KINDS))}")
        if "." in entry["dataset"]:
            raise ValueError(f"INGEST_BUFFERS names dataset {entry['dataset']}, which can't hold a dot")
        key = f"{entry['dataset']}.{entry['table']}"
        if key in kinds:
            raise ValueError(f"INGEST_BUFFERS names {key} twice")
        tenants[key], kinds[key] = entry["tenant"], KINDS[entry["table"]]
    return tenants, kinds


def named_buffers(signals_text: str, need_states_text: str, pubrules_checks_text: str = "",
                  buffers_text: str = "") -> tuple[dict[str, str], dict[str, Kind]]:
    """All of them. No two may load into the same table of the same dataset, and each of the
    three single variables takes its dataset for its one Kind."""
    tenants: dict[str, str] = {}
    kinds: dict[str, Kind] = {}
    named: dict[str, str] = {}
    tables: dict[tuple[str, str], str] = {}
    for variable, (more_tenants, more_kinds) in (
            ("INGEST_SIGNALS", signals(signals_text)), ("INGEST_NEED_STATES", need_states(need_states_text)),
            ("INGEST_PUBRULES_CHECKS", pubrules_checks(pubrules_checks_text)), ("INGEST_BUFFERS", buffers(buffers_text))):
        for key, kind in more_kinds.items():
            if variable != "INGEST_BUFFERS":
                if key in named:
                    raise ValueError(f"{named[key]} and {variable} name the same dataset")
                named[key] = variable
            table = (dataset_of(key), kind.table)
            if table in tables:
                raise ValueError(f"{tables[table]} and {variable} both load {table[0]}.{table[1]}")
            tables[table] = variable
        tenants.update(more_tenants)
        kinds.update(more_kinds)
    return tenants, kinds


def seconds(variable: str, text: str | None, default: float) -> float:
    """A number of seconds a variable gives, above 0, or the default when it is unset."""
    if text is None or not text.strip():
        return default
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        raise ValueError(f"{variable} is a number of seconds above 0, not {text!r}")
    return value


def scanned(datasets: dict[str, str], tenants: dict[str, str], kinds: dict[str, Kind]) -> dict[str, tuple[str, Kind]]:
    """Every buffer the scanner scans, by its key, with its tenant and Kind: each call log's
    (INGEST_DATASETS), named like its dataset, and the named buffers."""
    found = {dataset: (tenant_of(dataset), CALLS) for dataset in set(datasets.values())}
    for key, kind in kinds.items():
        found[key] = (tenants.get(key) or tenant_of(dataset_of(key)), kind)
    return found


def build():  # pragma: no cover - the pod's wiring, with real paths and keys
    datasets = json.loads(os.environ["INGEST_DATASETS"])
    tenants, kinds = named_buffers(os.environ.get("INGEST_SIGNALS", ""), os.environ.get("INGEST_NEED_STATES", ""),
                                   os.environ.get("INGEST_PUBRULES_CHECKS", ""), os.environ.get("INGEST_BUFFERS", ""))
    caller = Caller("integrations", "ingest", sink=StdoutSink())
    key = json.loads(Path(os.environ["INGEST_KEY_FILE"]).read_text(encoding="utf-8"))
    storage_token = Path(os.environ["INGEST_STORAGE_TOKEN_FILE"])
    # A key, or an external_account credential (Workload Identity): the same file either way.
    loader = BigQueryLoader(caller, google_token(caller, key), project_of(key))
    inbox = Inbox(Network(), lambda: storage_token.read_text(encoding="utf-8").strip(), os.environ["INGEST_STORAGE_URL"],
                  tenants)
    env = os.environ.get
    scanner = Scanner(inbox, loader, JobBudget(), report=lambda status: print(json.dumps(status), flush=True),
                      sender=env("HOSTNAME", "ingest"), kinds=kinds,
                      report_seconds=seconds("INGEST_REPORT_SECONDS", env("INGEST_REPORT_SECONDS"), INTERVAL_SECONDS),
                      gap_seconds=seconds("INGEST_BATCH_GAP_SECONDS", env("INGEST_BATCH_GAP_SECONDS"), GAP_SECONDS))
    sweep = seconds("INGEST_SWEEP_SECONDS", env("INGEST_SWEEP_SECONDS"), INTERVAL_SECONDS)
    buffers = sorted(set(datasets.values()) | set(kinds))
    notices = Notices(env("INGEST_NOTIFY_KEY", "").strip(), scanned(datasets, tenants, kinds), scanner.notified,
                      time.time)

    ticked = [time.monotonic()]
    threading.Thread(target=scan, args=(scanner, buffers, ticked), kwargs={"sweep_seconds": sweep},
                     daemon=True).start()
    return create_app(lambda: [scanning(time.monotonic() - ticked[0])], notices)


def scan(scanner: Scanner, buffers: list[str], ticked: list[float], *, sleep=time.sleep, clock=time.monotonic,
         sweep_seconds: float = INTERVAL_SECONDS, step: float = STEP_SECONDS) -> None:
    """The scanner's loop: a sweep straight away, then one every sweep_seconds, and
    between them, every step, the batches storage's notices made due. Sweeping first
    means a new pod reports at once and finds what came while it was away, so a deploy
    leaves no gap longer than its restart."""
    due = clock()
    while True:
        if clock() >= due:
            scanner.tick(buffers)
            due = clock() + sweep_seconds
        else:
            scanner.drain()
        ticked[0] = clock()
        sleep(max(0.0, min(step, due - clock())))


def scanning(since: float) -> Check:
    """The scanner works while its loop comes round within a few intervals: a pass that
    hangs, or a thread that died, stops the clock."""
    return Check("ingest.scanner", since < 3 * INTERVAL_SECONDS, f"last pass {int(since)} s ago")


app = build() if os.environ.get("INGEST_KEY_FILE") else None
