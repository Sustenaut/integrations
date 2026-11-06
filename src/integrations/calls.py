"""Every call to an outside service goes through a `Caller`, which makes it and
writes one record of it.

A record says who called what, how long it took and how it ended. It never holds
a body, a full URL or a token, so a sink can keep it anywhere. `Network` is the
only thing that reaches the network; a client takes a `Caller` and nothing else,
so its tests run on a `Replay` or a client's fake.

The operation's class sets its timeout and how often it is tried, so a call a
person is waiting on is not retried behind their back. A retry is only for a call
that is safe to repeat, or that says it is. It waits a random time up to 2**n
seconds (full jitter, so callers that failed together don't retry together), or what
the provider's Retry-After says, up to the class's timeout. A call never runs past its
class's timeout times its tries, waits included.
"""

from __future__ import annotations

import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from copy import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from . import __version__, registry

IDEMPOTENT = {"GET", "HEAD", "PUT", "DELETE"}
RETRY_STATUSES = {429, 502, 503, 504}
# The statuses whose Retry-After says how long to wait.
WAIT_STATUSES = {429, 503}


@dataclass(frozen=True)
class Policy:
    timeout: float
    tries: int


# Each class's timeout (seconds) and tries, from classes.json, which clients/js/caller.js
# is generated from too, so both languages call the same way.
POLICIES = {name: Policy(c["timeout"], c["tries"]) for name, c in registry.classes().items()}


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
    etag: str | None = None
    retry_after: str | None = None

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
    """The provider did not answer, or failed: another try may work. `response` is
    the last answer, if there was one, for a client that reads a failure's body."""

    def __init__(self, plugin: str, operation: str, detail: str, response: Response | None = None):
        super().__init__(plugin, operation, detail)
        self.response = response


class Refused(IntegrationError):
    """The provider answered no: trying again will not change that."""


class Transport(Protocol):
    def send(self, request: Request, timeout: float) -> Response:
        ...


class Network:
    def send(self, request: Request, timeout: float) -> Response:
        if not request.url.startswith("https://"):
            return Response(None, error="not https")
        # Cloudflare refuses urllib's own User-Agent with a 403 before the request
        # reaches whoever it was for.
        outgoing = urllib.request.Request(request.url, data=request.body, method=request.method,
                                          headers={"User-Agent": f"integrations/{__version__}", **request.headers})
        try:
            with urllib.request.urlopen(outgoing, timeout=timeout) as answer:  # noqa: S310 - https only
                return Response(answer.status, answer.read(), etag=answer.headers.get("ETag"))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), retry_after=e.headers.get("Retry-After") if e.headers else None)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return Response(None, error=f"no answer: {getattr(e, 'reason', e)}")


def recorded_key(request: Request) -> str:
    return f"{request.method} {request.url}"


class Replay:
    """Recorded responses, by method and URL; one not recorded has no answer.
    A list of responses answers in turn, for a call whose answer changes. An answer's
    "headers" may give its ETag and Retry-After."""

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
        headers = found.get("headers", {})
        body = found["text"].encode("utf-8") if "text" in found else json.dumps(found.get("json")).encode("utf-8")
        return Response(found["status"], body, etag=headers.get("ETag"), retry_after=headers.get("Retry-After"))


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


class StdoutSink:
    """One JSON line per call, for a service's log until the call log exists."""

    def write(self, record: CallRecord) -> None:
        print(json.dumps(asdict(record), sort_keys=True), flush=True)


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


def _retry_after(value: str | None) -> float | None:
    """Retry-After's seconds, from a number of them or an HTTP date; None if it says neither."""
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _policy(operation: dict) -> Policy:
    """The operation's class's timeout and tries, or its own where it names them."""
    policy = POLICIES[operation["class"]]
    return Policy(operation.get("timeout", policy.timeout), operation.get("tries", policy.tries))


class Caller:
    def __init__(self, repo: str, app: str = "", *, sink: Sink | None = None, transport: Transport | None = None,
                 modes: dict[str, str] | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
                 random: Callable[[], float] = random.random):
        self.repo, self.app = repo, app
        self.sink = sink or NullSink()
        self.transport = transport or Network()
        self.modes = modes or {}
        self.clock, self.sleep, self.new_id, self.random = clock, sleep, new_id, random
        self.classes = {(p.id, o["name"]): o for p in registry.plugins().values() for o in p.operations}

    def for_repo(self, repo: str) -> Caller:
        """The same caller, on behalf of a project, so its records are that project's."""
        other = copy(self)
        other.repo = repo
        return other

    def mode(self, plugin: str) -> str:
        return self.modes.get(plugin, "required")

    def call(self, plugin: str, operation: str, request: Request, *, request_id: str | None = None,
             refused: Callable[[Response], bool] | None = None) -> Response:
        """Make the call and write its record. A provider's refusal comes back
        as its response, for the client to read. Raises `Unavailable` when
        there is no answer, or only a failure, after every try. `refused` is for
        a provider that says no in a successful response, so the record says so."""
        registered = self.classes.get((plugin, operation))
        if registered is None:
            raise ValueError(f"{plugin} {operation} is not a registered operation")
        policy = _policy(registered)
        tries = policy.tries if request.repeatable or request.method in IDEMPOTENT else 1
        started = self.clock()
        deadline = started + policy.timeout * tries
        response = self.transport.send(request, policy.timeout)
        retries = 0
        while retries + 1 < tries and self._worth_retrying(response):
            wait = self._wait(response, retries, policy.timeout)
            left = deadline - self.clock() - wait
            if left <= 0:
                break
            self.sleep(wait)
            retries += 1
            response = self.transport.send(request, min(policy.timeout, left))
        latency = int((self.clock() - started) * 1000)
        failed = response.status is None or response.status >= 500 or response.status == 429
        self._keep(CallRecord(
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), repo=self.repo, app=self.app, plugin=plugin,
            operation=operation, request_id=request_id or self.new_id(), latency_ms=latency, status=response.status,
            outcome="unavailable" if failed else "ok" if response.ok and not self._says_no(response, refused)
            else "refused", retries=retries,
            bytes_in=len(response.body), bytes_out=len(request.body or b""), mode=self.mode(plugin),
            route=registered["route"]))
        if failed:
            raise Unavailable(plugin, operation, response.error or f"HTTP {response.status}",
                              response if response.status is not None else None)
        return response

    def note(self, plugin: str, operation: str, *, request_id: str, latency_ms: int, status: int | None,
             outcome: str) -> None:
        """A record of time that wasn't a call to a provider: how long a request took
        the service, or the app that made it."""
        registered = self.classes.get((plugin, operation))
        if registered is None:
            raise ValueError(f"{plugin} {operation} is not a registered operation")
        self._keep(CallRecord(
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), repo=self.repo, app=self.app, plugin=plugin,
            operation=operation, request_id=request_id, latency_ms=latency_ms, status=status, outcome=outcome,
            retries=0, bytes_in=0, bytes_out=0, mode=self.mode(plugin), route=registered["route"]))

    @staticmethod
    def _says_no(response: Response, refused: Callable[[Response], bool] | None) -> bool:
        """A body that can't be read isn't a refusal here: the client deals with it."""
        try:
            return refused is not None and refused(response)
        except ValueError:
            return False

    def _wait(self, response: Response, retries: int, timeout: float) -> float:
        """Seconds before the next try: the provider's Retry-After, up to the timeout, else
        a random time up to 2**retries."""
        asked = _retry_after(response.retry_after) if response.status in WAIT_STATUSES else None
        return min(asked, timeout) if asked is not None else self.random() * 2 ** retries

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
