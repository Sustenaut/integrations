"""A sender's heartbeat in Cloud Monitoring: the point that says it is alive.

Every CALL_LOG_METRIC_SECONDS, each running instance of a sender writes 1 to the gauge
custom.googleapis.com/sustenaut/heartbeat, on the resource `global`, with exactly three
labels: `project` (the service, its call log's project: the dataset's name without
`calls_`, `_` for `-`), `sender` (the instance's name without its generated suffix, as
observability names a sender) and `instance` (the pod's or revision's name).
shared-infra's sla-monitoring.tf alerts when no instance of a sender has written for its
SLA tier's window. Unset, nothing is written, so a release can come before anything sets it.

The point goes through the integrations Caller as google.monitoring write, with a Google
token for the account the credential gives (CALL_LOG_METRIC_CREDENTIAL_FILE, else
GOOGLE_APPLICATION_CREDENTIALS: a key, or an external_account credential through Workload
Identity), which needs roles/monitoring.metricWriter. A point that doesn't go is counted
and said in the log, never raised: a heartbeat that fails is a heartbeat missed, which is
what the alert is for.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .calls import Caller, Request, Transport, Unavailable
from .ingest.google_token import google_token, project_of

METRIC = "custom.googleapis.com/sustenaut/heartbeat"
PLUGIN, OPERATION = "google.monitoring", "write"
SCOPE = "https://www.googleapis.com/auth/monitoring.write"
API = "https://monitoring.googleapis.com/v3"
SECONDS_VARIABLE = "CALL_LOG_METRIC_SECONDS"
CREDENTIAL_VARIABLES = ("CALL_LOG_METRIC_CREDENTIAL_FILE", "GOOGLE_APPLICATION_CREDENTIALS")
# Cloud Monitoring takes a point of a series at most once every 5 seconds.
SHORTEST_SECONDS = 10
# The generated suffixes observability's sender_windows fact takes off: a Cloud Run
# revision's, a Deployment pod's, another generated pod name's and a StatefulSet pod's.
SUFFIX = re.compile(r"^(.+?)(?:-[0-9]{5}-[a-z0-9]{3}|-[bcdfghjklmnpqrstvwxz2456789]{6,10}-"
                    r"[bcdfghjklmnpqrstvwxz2456789]{5}|-[bcdfghjklmnpqrstvwxz2456789]{5}|-[0-9]+)?$")


def sender_of(instance: str) -> str:
    """The sender an instance is, its generated suffix taken off."""
    match = SUFFIX.match(instance)
    return match.group(1) if match else instance


def project_of_dataset(dataset: str) -> str:
    """The call log project a dataset is: calls_visit_ee is visit_ee."""
    return dataset.removeprefix("calls_").replace("-", "_")


def seconds_from(environ=os.environ) -> float | None:
    """CALL_LOG_METRIC_SECONDS: None when unset or empty, else seconds, at least SHORTEST_SECONDS."""
    text = environ.get(SECONDS_VARIABLE, "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not SHORTEST_SECONDS <= value < float("inf"):
        raise ValueError(f"{SECONDS_VARIABLE} is a number of seconds, at least {SHORTEST_SECONDS}, not {text!r}")
    return value


def credential_from(environ=os.environ, read: Callable[[str], str] | None = None) -> dict:
    """The Google credential the heartbeat writes with, from the first variable that names a file."""
    read = read or (lambda path: Path(path).read_text(encoding="utf-8"))
    for name in CREDENTIAL_VARIABLES:
        if path := environ.get(name, "").strip():
            try:
                return json.loads(read(path))
            except (OSError, ValueError) as e:
                raise ValueError(f"{name} names {path}, which isn't a Google credential: {type(e).__name__}") from None
    raise ValueError(f"{SECONDS_VARIABLE} is set, so the heartbeat needs a Google credential: set "
                     f"{' or '.join(CREDENTIAL_VARIABLES)}")


class Heartbeat:
    """Writes one point a heartbeat for each project, as `instance`."""

    def __init__(self, projects: list[str], instance: str, every_seconds: float, *, transport: Transport,
                 token: Callable[[], str], google_project: str, clock: Callable[[], float] = time.time,
                 start: bool = True):
        self.projects, self.instance, self.every_seconds = sorted(set(projects)), instance, every_seconds
        self.sender, self.token, self.google_project, self.clock = sender_of(instance), token, google_project, clock
        # Its own calls are the call log's plumbing, so nothing records them.
        self.caller = Caller("integrations", f"heartbeat:{instance}", transport=transport)
        self.sent = self.failures = 0
        if start:  # pragma: no cover - the process's own thread
            threading.Thread(target=self._run, daemon=True).start()

    def body(self) -> dict:
        """The points of one beat: one series a project."""
        at = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"timeSeries": [{
            "metric": {"type": METRIC, "labels": {"project": project, "sender": self.sender, "instance": self.instance}},
            "resource": {"type": "global", "labels": {"project_id": self.google_project}},
            "points": [{"interval": {"endTime": at}, "value": {"int64Value": "1"}}],
        } for project in self.projects]}

    def beat(self) -> bool:
        """One beat. True when Cloud Monitoring took it; never raises."""
        if not self.projects:
            return True
        why = ""
        try:
            answer = self.caller.call(PLUGIN, OPERATION, Request(
                "POST", f"{API}/projects/{self.google_project}/timeSeries",
                {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
                json.dumps(self.body()).encode()))
            if answer.ok:
                self.sent += 1
                return True
            why = f"HTTP {answer.status}"
        except Unavailable as e:
            why = f"no answer: {e}"
        except Exception as e:  # noqa: BLE001 - a token that can't be had is a missed beat too
            why = f"{type(e).__name__}: {e}"
        self.failures += 1
        print(f"integrations: the heartbeat of {self.instance} didn't go: {why}", file=sys.stderr, flush=True)
        return False

    def _run(self) -> None:  # pragma: no cover - the process's own thread
        while True:
            self.beat()
            time.sleep(self.every_seconds)


def from_environment(datasets: list[str], instance: str, transport: Transport, environ=os.environ,
                     start: bool = True, read: Callable[[str], str] | None = None,
                     clock: Callable[[], float] = time.time) -> Heartbeat | None:
    """The heartbeat CALL_LOG_METRIC_SECONDS asks for, of these datasets' projects, or None when unset."""
    every = seconds_from(environ)
    if every is None:
        return None
    credential = credential_from(environ, read)
    caller = Caller("integrations", f"heartbeat:{instance}", transport=transport)
    token = google_token(caller, credential, clock, scope=SCOPE, plugin=(PLUGIN, OPERATION))
    return Heartbeat([project_of_dataset(d) for d in datasets], instance, every, transport=transport, token=token,
                     google_project=project_of(credential), clock=clock, start=start)
