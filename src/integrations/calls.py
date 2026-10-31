"""Every call to an outside service goes through a `Caller`, which makes it and
writes one record of it.

A record says who called what, how long it took and how it ended. It never holds
a body, a full URL or a token, so a sink can keep it anywhere. `Network` is the
only thing that reaches the network; a client takes a `Caller` and nothing else,
so its tests run on a `Replay` or a client's fake.

The operation's class sets its timeout and how often it is tried, so a call a
person is waiting on is not retried behind their back. A retry is only for a call
that is safe to repeat, or that says it is.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from . import registry

IDEMPOTENT = {"GET", "HEAD", "PUT", "DELETE"}
RETRY_STATUSES = {429, 502, 503, 504}


@dataclass(frozen=True)
class Policy:
    timeout: float
    tries: int


POLICIES = {"H": Policy(10, 1), "E": Policy(60, 4), "S": Policy(30, 3), "M": Policy(30, 1)}


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    # A call that is safe to repeat though its method does not say so.
    repeatable: bool = False


@dataclass(frozen=True)
class Response:
    """What came back: a status and a body, or the error that stopped it."""

    status: int | None
    body: bytes = b""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else None


class IntegrationError(Exception):
    """A call did not do what was asked."""

    def __init__(self, plugin: str, operation: str, detail: str):
        super().__init__(f"{plugin} {operation}: {detail}")
        self.plugin, self.operation, self.detail = plugin, operation, detail


class Unavailable(IntegrationError):
    """The provider did not answer, or failed: another try may work."""


class Refused(IntegrationError):
    """The provider answered no: trying again will not change that."""


class Transport(Protocol):
    def send(self, request: Request, timeout: float) -> Response:
        ...


class Network:
    def send(self, request: Request, timeout: float) -> Response:
        if not request.url.startswith("https://"):
            return Response(None, error="not https")
        outgoing = urllib.request.Request(request.url, data=request.body, headers=request.headers,
                                          method=request.method)
        try:
            with urllib.request.urlopen(outgoing, timeout=timeout) as answer:  # noqa: S310 - https only
                return Response(answer.status, answer.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return Response(None, error=f"no answer: {getattr(e, 'reason', e)}")


def recorded_key(request: Request) -> str:
    return f"{request.method} {request.url}"


class Replay:
    """Recorded responses, by method and URL; one not recorded has no answer.
    A list of responses answers in turn, for a call whose answer changes."""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.sent: list[Request] = []

    @classmethod
    def from_file(cls, path: Path) -> Replay:
        return cls(json.loads(path.read_text(encoding="utf-8"))["responses"])

    def send(self, request: Request, timeout: float) -> Response:
        self.sent.append(request)
        found = self.responses.get(recorded_key(request))
        if isinstance(found, list):
            found = found.pop(0) if len(found) > 1 else found[0]
        if found is None:
            return Response(None, error="not recorded")
        if "error" in found:
            return Response(None, error=found["error"])
        return Response(found["status"], json.dumps(found.get("json")).encode("utf-8"))


class Recording:
    """`inner`'s answers, kept for a replay file. A token travels in a header,
    so it is never kept."""

    def __init__(self, inner: Transport):
        self.inner = inner
        self.responses: dict[str, Any] = {}

    def send(self, request: Request, timeout: float) -> Response:
        response = self.inner.send(request, timeout)
        if response.error:
            self.responses[recorded_key(request)] = {"error": response.error}
        else:
            self.responses[recorded_key(request)] = {"status": response.status, "json": response.json()}
        return response

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"responses": self.responses}, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")


@dataclass(frozen=True)
class CallRecord:
    """One call, as schemas/call-record.schema.json describes it."""

    at: str
    repo: str
    app: str
    plugin: str
    operation: str
    request_id: str
    latency_ms: int
    status: int | None
    outcome: str
    retries: int
    bytes_in: int
    bytes_out: int
    mode: str
    route: str


class Sink(Protocol):
    def write(self, record: CallRecord) -> None:
        ...


class NullSink:
    def write(self, record: CallRecord) -> None:
        """Keep nothing: the default until the call log exists."""


class MemorySink:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def write(self, record: CallRecord) -> None:
        self.records.append(record)


class JsonlSink:
    def __init__(self, path: Path):
        self.path = path

    def write(self, record: CallRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def read_modes(root: Path) -> dict[str, str]:
    """Each plugin's mode in the integrations.json under `root`. A plugin
    declared both ways counts as required."""
    data = json.loads((root / "integrations.json").read_text(encoding="utf-8"))
    pairs = [(u["plugin"], u.get("mode", "required")) for u in data.get("uses", [])]
    pairs += [(p, s.get("mode", "required")) for s in data.get("terraform", []) for p in s["plugins"]]
    modes: dict[str, str] = {}
    for plugin, mode in pairs:
        modes[plugin] = "required" if "required" in (mode, modes.get(plugin)) else mode
    return modes


class Caller:
    def __init__(self, repo: str, app: str = "", *, sink: Sink | None = None, transport: Transport | None = None,
                 modes: dict[str, str] | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, new_id: Callable[[], str] = lambda: uuid.uuid4().hex):
        self.repo, self.app = repo, app
        self.sink = sink or NullSink()
        self.transport = transport or Network()
        self.modes = modes or {}
        self.clock, self.sleep, self.new_id = clock, sleep, new_id
        self.classes = {(p.id, o["name"]): o for p in registry.plugins().values() for o in p.operations}

    def mode(self, plugin: str) -> str:
        return self.modes.get(plugin, "required")

    def call(self, plugin: str, operation: str, request: Request, *, request_id: str | None = None) -> Response:
        """Make the call and write its record. A provider's refusal comes back
        as its response, for the client to read. Raises `Unavailable` when
        there is no answer, or only a failure, after every try."""
        registered = self.classes.get((plugin, operation))
        if registered is None:
            raise ValueError(f"{plugin} {operation} is not a registered operation")
        policy = POLICIES[registered["class"]]
        tries = policy.tries if request.repeatable or request.method in IDEMPOTENT else 1
        started = self.clock()
        response = self.transport.send(request, policy.timeout)
        retries = 0
        while retries + 1 < tries and self._worth_retrying(response):
            self.sleep(2 ** retries)
            retries += 1
            response = self.transport.send(request, policy.timeout)
        latency = int((self.clock() - started) * 1000)
        failed = response.status is None or response.status >= 500 or response.status == 429
        self._keep(CallRecord(
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), repo=self.repo, app=self.app, plugin=plugin,
            operation=operation, request_id=request_id or self.new_id(), latency_ms=latency, status=response.status,
            outcome="unavailable" if failed else "ok" if response.ok else "refused", retries=retries,
            bytes_in=len(response.body), bytes_out=len(request.body or b""), mode=self.mode(plugin),
            route=registered["route"]))
        if failed:
            raise Unavailable(plugin, operation, response.error or f"HTTP {response.status}")
        return response

    @staticmethod
    def _worth_retrying(response: Response) -> bool:
        return response.status is None or response.status in RETRY_STATUSES

    def _keep(self, record: CallRecord) -> None:
        """A sink that fails loses the record, not the call."""
        try:
            self.sink.write(record)
        except Exception as e:  # noqa: BLE001 - whatever a sink raises
            print(f"integrations: could not keep a call record: {e}", file=sys.stderr)


@dataclass(frozen=True)
class Check:
    """Whether a plugin works now, from its client's `check()`."""

    plugin: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Health:
    healthy: bool
    unhealthy: tuple[Check, ...] = ()
    alerts: tuple[Check, ...] = ()


def health(checks: list[Check], modes: dict[str, str]) -> Health:
    """A required plugin that fails makes the service unhealthy; an advisory
    one only alerts."""
    failed = [c for c in checks if not c.ok]
    unhealthy = tuple(c for c in failed if modes.get(c.plugin, "required") == "required")
    alerts = tuple(c for c in failed if c not in unhealthy)
    return Health(not unhealthy, unhealthy, alerts)
