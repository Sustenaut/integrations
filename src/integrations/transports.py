"""Record and replay: the transports a test runs its calls on, and the one a
process gets when it is given none.

- `Cassette(path)` answers from a recording, `integrations.cassette/1` (or the
  older `{"responses": ...}` format), and raises `NoSnapshot` for a
  call it has no recording of.
- `Recorder(inner, path)` sends through `inner`, keeps every call, and `save()`
  writes them as a cassette, normalised by each plugin's `record` rules
  (`registry.record`): only the headers worth keeping, volatile values as
  placeholders, secrets never.
- `Faulty(inner, rules)` makes calls fail on purpose: no answer, a status, a slow
  answer, a malformed body.
- `current_transport()` is the transport a `Caller` given none uses: the one a
  test harness installed with `use()`, else the one `INTEGRATIONS_MODE` names,
  under the faults `INTEGRATIONS_FAULTS` names; `cassette_reports()` says which
  recorded calls the cassettes it made answered, for a harness to check at exit.
- `use_clock()` is the clock, sleep and jitter a `Caller` given none of them uses.

The same in clients/js/src/transports.js, and the vectors in tests/vectors/cassettes/
run on both.

Where cassettes go. A cassette is written (`Recorder.save()`, `promote()`,
`write_cassette()`), and a cassette the environment names is read, only at a path
that resolves, symlinks followed, inside the cassettes directory; anything else
raises `CassettePathError` before a byte is read or written. The cassettes
directory is `INTEGRATIONS_CASSETTES` when it is set, else the directory holding
`INTEGRATIONS_CASSETTE` when that is set, else the directory of the path given
(to the `Recorder` when it is made, or to `promote()` and `write_cassette()`), each
resolved. So `../outside.json` under `INTEGRATIONS_CASSETTES`, or a cassette that is
a symlink out of its directory, is refused.

Placeholders. A value that changes between two recordings of the same calls (a
sha, a date, an id, an ETag) is written `⟦integrations:KIND:N⟧`, the same
placeholder wherever the same value is, and a different one for a different
value. On replay each placeholder is answered as a stand-in of its kind, made
from its name (a 40-hex sha, an ISO date, a numeric id, a ghs_ token), and a
stand-in the code sends back is read as its placeholder again, so a sha one call
answers and the next sends matches. Real text holding `⟦integrations:` is written
`⟦integrations:escape⟧`, and read back as it was.

A placeholder first seen in a request is a value the code made itself: on replay
it matches the first value of its kind the code sends there, and from then on is
that value, in the answers and in what the code must send again, as a recorded
value is. A value already another placeholder's matches none.

Names. A recording made against a test org names its repositories for real
(`SustenautTest/rec-pubpr-…-1`) where a scenario says `o/r`: the Recorder's
`names`, and `INTEGRATIONS_NAMES` (a JSON object from each real name to the one
the scenario gives it), are written as the scenario's names wherever they appear
as a whole word, so the cassette, and a replay, only ever see `o/r`. A name may be
a label, `⟦integrations:commit:start⟧` for a commit's real sha: the names are
applied before the volatile patterns, so that sha is written as its label, not as
an ordinary `⟦integrations:sha:N⟧`, and is kept in the cassette's map (`volatile`,
its kind `commit`). A label is replayed as itself, so the code under test is handed
the label for the sha and sends it back, in a URL percent-encoded or not; it is
never loose, and is not escaped where the map knows it (real text holding any other
`⟦integrations:` is escaped as before). Labels are letters, digits, `_` and `-`,
starting with a letter, so one never reads as a numbered placeholder.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import re
import sys
import time
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

from . import __version__, registry
from .calls import Network, Request, Response, Transport

FORMAT = "integrations.cassette/1"
# A request body over this many bytes is kept as its digest, and matched by it.
DIGEST_OVER = 64 * 1024
MODE = "INTEGRATIONS_MODE"
CASSETTE = "INTEGRATIONS_CASSETTE"
CASSETTES = "INTEGRATIONS_CASSETTES"
MARKER = "PUBTEST_RUN"
NAMES = "INTEGRATIONS_NAMES"
FAULTS = "INTEGRATIONS_FAULTS"
MODES = ("live", "replay", "record")

PREFIX = "⟦integrations:"
ESCAPE = "⟦integrations:escape⟧"
SECRET = "⟦integrations:secret⟧"
# A placeholder's name is a number, `?` (any value of its kind), or a label a scenario gave
# a value (`⟦integrations:commit:start⟧`), which is replayed as itself.
LABEL = r"[A-Za-z][A-Za-z0-9_-]*"
PLACEHOLDER = re.compile(rf"⟦integrations:(?:escape|secret|([a-z][a-z0-9-]*):(\d+|\?|{LABEL}))⟧")
# A known value is replaced where it is a whole value or a path segment from this
# long, and inside other text from MIN_INSIDE, so a short one can't hit by chance.
MIN_WHOLE = 4
MIN_INSIDE = 8
# A secret shorter than this is not looked for in what would be written.
MIN_SECRET = 4

URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#.*)?$")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
HTTP_DATE = re.compile(r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT")
SHA = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"gh[opsur]_[A-Za-z0-9_]+")
DIGITS = re.compile(r"[0-9]+")
GRAPHQL_NAME = re.compile(r"^\s*(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)")
STAND_IN_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


class NoSnapshot(LookupError):
    """A call the cassette has no recording of. Its message says which, the
    nearest recorded call and how they differ, and how to record it."""


class UnusedCalls(NoSnapshot):
    """Recorded calls the code never made: the code changed, or the cassette is stale."""


class SecretInRecording(ValueError):
    """A secret the recorder saw would be written: the recording is not saved."""


class CassettePathError(ValueError):
    """A cassette path that resolves outside the cassettes directory: nothing is
    read or written there."""


class ModeError(RuntimeError):
    """INTEGRATIONS_MODE asks for something this process may not do."""


# --- values and placeholders --------------------------------------------------

def kind_of(value: Any, default: str = "text") -> str | None:
    """The kind of a volatile value, from its shape; None for one never replaced."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return "id"
    if isinstance(value, float):
        return "number"
    if not isinstance(value, str) or not value:
        return None
    for kind, shape in (("date", ISO_DATE), ("http-date", HTTP_DATE), ("sha", SHA), ("token", TOKEN),
                        ("digits", DIGITS)):
        if shape.fullmatch(value):
            return kind
    return default


def stand_in(kind: str, n: int | str) -> Any:
    """The value a placeholder is replayed as: made from its name alone, of its kind's
    shape, and different for every name. A labelled one is itself."""
    if not isinstance(n, int):
        return _placeholder(kind, n)
    seed = hashlib.sha256(f"integrations:{kind}:{n}".encode()).hexdigest()
    number = 7_000_000_000 + n * 1000 + int(seed[:6], 16) % 1000
    at = STAND_IN_EPOCH + timedelta(days=n, seconds=int(seed[:8], 16) % 86400)
    if kind == "sha":
        return seed[:40]
    if kind in ("token", "secret"):
        return f"ghs_{seed[:36]}"
    if kind == "date":
        return at.strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "http-date":
        return format_datetime(at, usegmt=True)
    if kind == "id":
        return number
    if kind == "number":
        return number + 0.5
    if kind == "digits":
        return str(number)
    return f"{kind}-{seed[:12]}"


def text_of(value: Any) -> str:
    """A value as it reads inside text: a URL, a header, a string."""
    return value if isinstance(value, str) else json.dumps(value)


def _placeholder(kind: str, n: int | str) -> str:
    return f"⟦integrations:{kind}:{n}⟧"


def _pieces(text: str) -> list[tuple[bool, str]]:
    """The text split into placeholders (True) and what lies between them."""
    out, at = [], 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > at:
            out.append((False, text[at:m.start()]))
        out.append((True, m.group(0)))
        at = m.end()
    if at < len(text):
        out.append((False, text[at:]))
    return out


def _sub_outside(text: str, pattern: re.Pattern, fn: Callable[[str], str]) -> str:
    """`pattern` replaced by `fn(match)`, but never inside a placeholder."""
    return "".join(p if held else pattern.sub(lambda m: fn(m.group(0)), p) for held, p in _pieces(text))


def is_label(name: str) -> bool:
    """Whether `name` is a labelled placeholder, `⟦integrations:KIND:LABEL⟧`."""
    m = PLACEHOLDER.fullmatch(name)
    return bool(m and m.group(2) and m.group(2) != "?" and not m.group(2).isdigit())


def label(kind: str, name: str) -> str:
    """The placeholder a scenario's label for a value is: `⟦integrations:commit:start⟧`.
    Raises ValueError for a label that is not a letter, then letters, digits, _ and -."""
    if not re.fullmatch(LABEL, name or "") or not re.fullmatch(r"[a-z][a-z0-9-]*", kind):
        raise ValueError(f'"{name}" is no label: a letter, then letters, digits, _ and -')
    return _placeholder(kind, name)


def _escape(text: str, labels: frozenset[str] | set[str] = frozenset()) -> str:
    """Real text holding the prefix, escaped; a label the map knows is kept as it is."""
    if PREFIX not in text:
        return text
    if not labels:
        return text.replace(PREFIX, ESCAPE)
    keep = re.compile("|".join(re.escape(t) for t in sorted(labels, key=len, reverse=True)))
    out, at = [], 0
    for m in keep.finditer(text):
        out += [text[at:m.start()].replace(PREFIX, ESCAPE), m.group(0)]
        at = m.end()
    out.append(text[at:].replace(PREFIX, ESCAPE))
    return "".join(out)


def names_from(env: dict[str, str] | None = None) -> dict[str, str]:
    """INTEGRATIONS_NAMES: a JSON object from each real name a recording meets to the
    name the scenario gives it; {} when it is not set. Raises ValueError when it is
    not such an object."""
    env = os.environ if env is None else env
    raw = env.get(NAMES)
    if not raw:
        return {}
    try:
        names = json.loads(raw)
    except ValueError:
        names = None
    if not isinstance(names, dict) or not all(isinstance(k, str) and k and isinstance(v, str)
                                              for k, v in names.items()):
        raise ValueError(f"{NAMES} is a JSON object from each real name to the scenario's, not {raw!r}")
    return names


def _renamer(names: dict[str, str]) -> Callable[[str], str] | None:
    """Each real name in `names`, where it is a whole word (not inside a longer name),
    as the scenario's; the longest first."""
    names = {k: v for k, v in names.items() if k != v}
    if not names:
        return None
    pattern = re.compile(r"(?<![A-Za-z0-9_.-])(?:" + "|".join(
        re.escape(k) for k in sorted(names, key=lambda k: (-len(k), k))) + r")(?![A-Za-z0-9_-])")
    return lambda text: _sub_outside(text, pattern, lambda v: names[v])


class _Map:
    """Values to placeholders, for one cassette. The writer's assigns them to real
    values as it meets them; the reader's knows each placeholder's stand-in and reads
    a stand-in back as it, and any other volatile value as `⟦integrations:KIND:?⟧`."""

    rename: Callable[[str], str] | None = None

    def __init__(self) -> None:
        self.known: dict[str, str] = {}  # a value's text -> its placeholder
        self.labels: set[str] = set()  # the labelled placeholders, which text keeps as they are
        self._inside: re.Pattern | None = None

    def known_text(self, text: str) -> str | None:
        return self.known.get(text) if len(text) >= MIN_WHOLE else None

    def inside(self) -> re.Pattern | None:
        if self._inside is None:
            long = sorted((k for k in self.known if len(k) >= MIN_INSIDE), key=lambda k: (-len(k), k))
            self._inside = re.compile("|".join(re.escape(k) for k in long)) if long else re.compile(r"(?!)")
        return self._inside

    def _learnt(self) -> None:
        self._inside = None


class _Writer(_Map):
    def __init__(self, names: dict[str, str] | None = None) -> None:
        super().__init__()
        self.rename = _renamer(names or {})
        self.labels = {v for v in (names or {}).values() if is_label(v)}
        self.by_value: dict[str, str] = {}
        self.entries: dict[str, dict[str, str]] = {}
        self.counts: dict[str, int] = {}
        self.secrets: dict[str, str] = {}  # a secret value -> where it was seen

    def labelled(self, text: str, where: str) -> None:
        """Each label in `text` is in the cassette's map, first seen at `where`."""
        for m in PLACEHOLDER.finditer(text):
            if m.group(0) in self.labels and m.group(0) not in self.entries:
                self.entries[m.group(0)] = {"kind": m.group(1), "first": where}

    def volatile(self, value: Any, kind: str, where: str) -> str:
        key = json.dumps(value)
        if key not in self.by_value:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            name = _placeholder(kind, self.counts[kind])
            self.by_value[key] = name
            self.entries[name] = {"kind": kind, "first": where}
            if kind != "secret" and text_of(value) not in self.known:
                self.known[text_of(value)] = name
            self._learnt()
        return self.by_value[key]

    def secret(self, value: Any, side: str, where: str) -> str:
        self.saw_secret(value, where)
        if side == "request" or value is None:
            return SECRET
        return self.volatile(["secret", value], "secret", where)

    def saw_secret(self, value: Any, where: str) -> None:
        if isinstance(value, (dict, list)):
            for v in value.values() if isinstance(value, dict) else value:
                self.saw_secret(v, where)
            return
        if value is None or isinstance(value, bool):
            return
        text = text_of(value)
        if len(text) >= MIN_SECRET:
            self.secrets.setdefault(text, where)


class _Reader(_Map):
    def __init__(self, volatile: dict[str, dict]) -> None:
        super().__init__()
        self.values: dict[str, Any] = {}  # a placeholder -> its stand-in, or the value it was bound to
        self.raw = False  # True: a volatile value no placeholder stands for is left as it is
        for name, entry in volatile.items():
            m = PLACEHOLDER.fullmatch(name)
            if not m or not m.group(1) or m.group(2) == "?":
                continue
            if is_label(name):
                self.labels.add(name)
            value = stand_in(m.group(1), int(m.group(2)) if m.group(2).isdigit() else m.group(2))
            self.values[name] = value
            self.known[text_of(value)] = name

    def volatile(self, value: Any, kind: str, where: str) -> Any:
        known = self.known.get(text_of(value))
        if known:
            return known
        return value if self.raw else _placeholder(kind, "?")

    def raw_copy(self) -> _Reader:
        """The same map, leaving a volatile value it does not know as it is."""
        other = _Reader({})
        other.values, other.known, other.labels, other.raw = self.values, self.known, self.labels, True
        return other

    def bind(self, name: str, value: Any) -> None:
        """`name` is `value` from now on: its stand-in no longer stands for it."""
        old = self.values.get(name)
        if old is not None and self.known.get(text_of(old)) == name:
            del self.known[text_of(old)]
        self.values[name] = value
        self.known[text_of(value)] = name
        self._learnt()

    def secret(self, value: Any, side: str, where: str) -> str:
        return SECRET

    def fill_text(self, text: str) -> str:
        """Placeholders in recorded text as their stand-ins, the escape as it was."""
        def one(m: re.Match) -> str:
            if m.group(0) == ESCAPE:
                return PREFIX
            return text_of(self.stand_in(m.group(0)))
        return PLACEHOLDER.sub(one, text)

    def stand_in(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        m = PLACEHOLDER.fullmatch(name)
        kind = m.group(1) if m and m.group(1) else "secret"
        n: int | str = 0
        if m and m.group(2) and m.group(2) != "?":
            n = int(m.group(2)) if m.group(2).isdigit() else m.group(2)
        return stand_in(kind, n)

    def fill(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        if isinstance(value, str):
            if PLACEHOLDER.fullmatch(value) and value != ESCAPE:
                return self.stand_in(value)
            return self.fill_text(value)
        return value


# --- JSONPath-lite --------------------------------------------------------------

def parse_path(text: str) -> list[tuple[str, str]]:
    """`$.a.b`, `$..key`, `$.a[*].b`, `$.a.*` as steps: ("child", name),
    ("any", name) for a name at any depth, ("index", "*")."""
    steps, rest = [], text[1:] if text.startswith("$") else text
    for m in re.finditer(r"\.\.([A-Za-z0-9_*-]+)|\.([A-Za-z0-9_*-]+)|\[\*\]", rest):
        if m.group(1):
            steps.append(("any", m.group(1)))
        elif m.group(2):
            steps.append(("child", m.group(2)))
        else:
            steps.append(("index", "*"))
    return steps


def path_matches(steps: list[tuple[str, str]], path: list) -> bool:
    if not steps:
        return not path
    how, name = steps[0]
    if how == "index":
        return bool(path) and isinstance(path[0], int) and path_matches(steps[1:], path[1:])
    if how == "child":
        return bool(path) and (name == "*" or path[0] == name) and path_matches(steps[1:], path[1:])
    return any((name == "*" or path[i] == name) and path_matches(steps[1:], path[i + 1:]) for i in range(len(path)))


def _dotted(path: list) -> str:
    return ".".join(str(p) for p in path)


# --- normalising ----------------------------------------------------------------

class _Normaliser:
    """One plugin's rules, applied with a map: to a recorded call (the writer's
    map) or to a request made on replay (the reader's)."""

    def __init__(self, rules: dict, values: _Map):
        self.rules, self.map = rules, values
        self.volatile_paths = [parse_path(p) for p in rules["volatile"]["json"]]
        self.secret_paths = [parse_path(p) for p in rules["secret"]["json"]]
        self.patterns = [(kind, re.compile(rx)) for kind, rx in rules["volatile"]["values"].items()]
        self.keep = {h.lower(): h for h in rules["keep_headers"]}
        self.volatile_headers = {h.lower() for h in rules["volatile"]["headers"]}
        self.secret_headers = {h.lower() for h in rules["secret"]["headers"]}
        self.secret_query = set(rules["secret"]["query"])

    def text(self, text: str, where: str) -> str:
        text = _escape(text, self.map.labels)
        if self.map.rename is not None:  # before the volatile patterns: a labelled sha is its label
            text = self.map.rename(text)
            if isinstance(self.map, _Writer) and self.map.labels:
                self.map.labelled(text, where)
        whole = self.map.known_text(text)
        if whole:
            return whole
        text = _sub_outside(text, self.map.inside(), lambda v: self.map.known[v])
        for kind, pattern in self.patterns:
            text = _sub_outside(text, pattern, lambda v, kind=kind: self.map.volatile(v, kind, where))
        return text

    def url(self, url: str, where: str) -> str:
        for token in self.map.labels:  # a label sent back in a URL, percent-encoded or not
            for encoded in (quote(token, safe=":"), quote(token, safe="")):
                url = url.replace(encoded, token)
        m = URL.match(url)
        if not m:
            return self.text(url, where)
        scheme, host, path, query = m.group(1).lower(), m.group(2).lower(), m.group(3) or "/", m.group(4)
        path = "/".join(self.text(segment, where) if segment else "" for segment in path.split("/"))
        pairs = []
        for pair in (query or "").split("&"):
            if not pair:
                continue
            name, eq, value = pair.partition("=")
            if name in self.secret_query:
                value = self.map.secret(value, where.split(".")[2] if where.count(".") >= 2 else "request", where)
            else:
                value = self.text(value, where)
            pairs.append((name, eq, value))
        pairs.sort(key=lambda p: (p[0], p[2]))
        return f"{scheme}://{host}{path}" + ("?" + "&".join(n + e + v for n, e, v in pairs) if pairs else "")

    def headers(self, headers: dict[str, str], side: str, where: str) -> dict[str, str]:
        lower = {name.lower(): value for name, value in headers.items()}
        for name in self.rules["secret"]["headers"]:  # by the rules' names, in their order, in both languages
            if name.lower() in lower:
                self.secret_header(lower[name.lower()], f"{where}.{name}")
        kept: dict[str, str] = {}
        for out in self.rules["keep_headers"]:
            low = out.lower()
            if low in self.secret_headers or low not in lower or out in kept:
                continue
            value = lower[low]
            if low in self.volatile_headers:
                kept[out] = self.map.volatile(value, kind_of(value, "header") or "header", f"{where}.{out}")
            else:
                kept[out] = self.text(value, f"{where}.{out}")
        return kept

    def secret_header(self, value: str, where: str) -> None:
        if isinstance(self.map, _Writer):
            self.map.saw_secret(value, where)
            parts = value.split(None, 1)
            if len(parts) == 2:
                self.map.saw_secret(parts[1], where)

    def json(self, value: Any, side: str, where: str, path: list) -> Any:
        if path and any(path_matches(s, path) for s in self.secret_paths) and value is not None:
            return self.map.secret(value, side, f"{where}.{_dotted(path)}")
        if isinstance(value, dict):
            return {k: self.json(v, side, where, [*path, k]) for k, v in value.items()}
        if isinstance(value, list):
            return [self.json(v, side, where, [*path, i]) for i, v in enumerate(value)]
        if path and any(path_matches(s, path) for s in self.volatile_paths):
            kind = kind_of(value)
            if kind:
                return self.map.volatile(value, kind, f"{where}.{_dotted(path)}")
        if isinstance(value, str):
            return self.text(value, f"{where}.{_dotted(path)}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return self.map.known_text(json.dumps(value)) or value
        return value

    def body(self, raw: bytes | None, side: str, where: str) -> dict | None:
        if not raw:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"base64": base64.b64encode(raw).decode("ascii")}
        if self.rules["match"].get("body", "json") == "json" and text.lstrip()[:1] in ("{", "["):
            try:
                return {"json": self.json(json.loads(text), side, f"{where}.body", [])}
            except ValueError:
                pass
        return {"text": self.text(text, f"{where}.body")}

    def request(self, request: Request, where: str) -> dict:
        body = self.body(request.body, "request", where)
        if request.body and len(request.body) > DIGEST_OVER:
            body = digest(body)
        return {"method": request.method.upper(), "url": self.url(request.url, f"{where}.url"),
                "headers": self.headers(request.headers, "request", f"{where}.headers"), "body": body}

    def response(self, response: Response, where: str) -> dict:
        if response.status is None:
            return {"status": None, "headers": {}, "body": None, "error": response.error or "no answer"}
        headers = self.headers(_headers_of(response), "response", f"{where}.headers")
        return {"status": response.status, "headers": headers, "body": self.body(response.body, "response", where)}


def _headers_of(response: Response) -> dict[str, str]:
    headers = dict(response.headers)
    lower = {k.lower() for k in headers}
    if response.etag and "etag" not in lower:
        headers["ETag"] = response.etag
    if response.retry_after and "retry-after" not in lower:
        headers["Retry-After"] = response.retry_after
    return headers


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spaces: what a body's digest and its match read."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(body: dict | None) -> dict | None:
    """A normalised request body as its SHA-256 and length."""
    if body is None or "sha256" in body:
        return body
    if "json" in body:
        data = canonical(body["json"]).encode("utf-8")
    elif "text" in body:
        data = body["text"].encode("utf-8")
    else:
        data = base64.b64decode(body["base64"])
    return {"sha256": hashlib.sha256(data).hexdigest(), "length": len(data)}


def _graphql(url: str, body: dict | None, rules: dict) -> dict | None:
    """A GraphQL body as its operation's name and variables, when the plugin matches so."""
    if rules["match"].get("graphql") != "query-name+variables" or not body or "json" not in body:
        return body
    query = body["json"].get("query") if isinstance(body["json"], dict) else None
    if not isinstance(query, str):
        return body
    name = body["json"].get("operationName")
    if not name:
        m = GRAPHQL_NAME.match(query)
        name = m.group(1) if m else " ".join(query.split())
    return {"graphql": {"operation": name, "variables": body["json"].get("variables")}}


# --- the cassette ---------------------------------------------------------------

@dataclass
class _Call:
    plugin: str | None
    operation: str | None
    request: dict
    response: dict
    sticky: bool = False  # an old-format answer that answers again once the others are used
    used: int = 0


def _old_answer(found: dict) -> dict:
    if "error" in found:
        return {"status": None, "headers": {}, "body": None, "error": found["error"]}
    body = {"text": found["text"]} if "text" in found else {"json": found.get("json")}
    return {"status": found["status"], "headers": dict(found.get("headers", {})), "body": body}


def _load(source: Path | str | dict) -> tuple[dict, str]:
    if isinstance(source, dict):
        return source, source.get("scenario") or source.get("description") or "a cassette"
    path = Path(source)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, data.get("scenario") or path.stem


class Cassette:
    """A Transport that answers from a recording: `path` (or the data itself), in
    `integrations.cassette/1`, or the `{"responses": {"METHOD URL": answer}}` files
    format (the shared vectors among them).

    A request finds its recorded call by plugin, operation, method, URL (scheme, host,
    path, the query sorted) and body, each normalised as the plugin's record rules
    say, and is answered with the first such call not answered yet. Each is answered
    once; an old-format list answers in turn, its last one for good. With none left,
    it raises `NoSnapshot`. `mode` is the INTEGRATIONS_MODE it runs under: in
    record mode `close()` only warns."""

    def __init__(self, path: Path | str | dict, plugins: Any = registry, *, mode: str | None = None):
        data, self.scenario = _load(path)
        self.plugins = plugins
        self.mode = mode if mode is not None else os.environ.get(MODE, "live")
        self.sent: list[Request] = []
        if "responses" in data:
            self.calls = self._old(data["responses"])
            volatile: dict = {}
        elif data.get("format") == FORMAT:
            current = data.get("current") or {}
            volatile = current.get("volatile", {})
            self.calls = [_Call(c.get("plugin"), c.get("operation"), c["request"], c["response"])
                          for c in current.get("calls", [])]
        else:
            raise ValueError(f"{self.scenario} is not a cassette: no format {FORMAT} and no responses")
        self.reader = _Reader(volatile)
        self.loose = {name for name, e in volatile.items()
                      if e.get("first", "").split(".")[2:3] == ["request"] and not is_label(name)}

    @staticmethod
    def _old(responses: dict) -> list[_Call]:
        calls = []
        for key, found in responses.items():
            method, _, url = key.partition(" ")
            answers = found if isinstance(found, list) else [found]
            for i, answer in enumerate(answers):
                calls.append(_Call(None, None, {"method": method, "url": url, "body": "*"}, _old_answer(answer),
                                   sticky=i == len(answers) - 1))
        return calls

    def _rules(self, plugin: str | None) -> dict:
        return self.plugins.record(plugin or None)

    def _recorded_key(self, call: _Call, rules: dict) -> tuple:
        request = call.request
        if call.plugin is None:  # an old-format answer: its URL as a request's would read
            return request["method"].upper(), _Normaliser(rules, _Reader({})).url(request["url"], "url"), "*"
        return request["method"].upper(), self._loosen(request["url"]), self._body_key(request["url"],
                                                                                        request.get("body"), rules)

    def _loosen(self, text: str) -> str:
        """A placeholder first seen in a request, and not yet bound, is a value the code
        made itself, which matches any value of its kind."""
        return PLACEHOLDER.sub(lambda m: _placeholder(m.group(1), "?") if m.group(0) in self.loose else m.group(0),
                               text)

    def _body_key(self, url: str, body: dict | None, rules: dict) -> str:
        if body is not None and "sha256" not in body and self.loose:
            body = json.loads(self._loosen(json.dumps(body, ensure_ascii=False)))
        return canonical(_graphql(url, body, rules))

    def _live_key(self, request: Request, rules: dict) -> tuple:
        normaliser = _Normaliser(rules, self.reader)
        asked = normaliser.request(request, "calls.?.request")
        asked["url"] = self._loosen(asked["url"])
        return asked["method"], asked["url"], self._body_key(asked["url"], asked["body"], rules), asked

    def send(self, request: Request, timeout: float) -> Response:
        self.sent.append(request)
        rules = self._rules(request.plugin)
        method, url, body, asked = self._live_key(request, rules)
        found = None
        for call in self.calls:
            if call.plugin is not None and request.plugin and (call.plugin, call.operation) != (
                    request.plugin, request.operation):
                continue
            want = self._recorded_key(call, rules)
            if want[:2] != (method, url) or want[2] not in ("*", body):
                continue
            if not call.used:
                bound = self._bindings(call, request, rules)
                if bound is None:
                    continue
                for name, value in bound.items():
                    self.reader.bind(name, value)
                    self.loose.discard(name)
                found = call
                break
            if call.sticky and found is None:
                found = call
        if found is None:
            raise NoSnapshot(self._no_snapshot(request, asked, body, rules))
        found.used += 1
        return self._answer(found.response)

    def _bindings(self, call: _Call, request: Request, rules: dict) -> dict[str, Any] | None:
        """The values `request` sends where `call`'s recorded request holds a loose
        placeholder: each one binds it. {} when there is none; None when they do not
        fit the map: one placeholder sent as two values, or a value that is already
        another placeholder's."""
        if call.plugin is None or not self.loose:
            return {}
        recorded = call.request
        body = recorded.get("body")
        if body is not None and "sha256" in body:
            return {}
        texts = [recorded["url"], canonical(_graphql(recorded["url"], body, rules))]
        if not any(m.group(0) in self.loose for t in texts for m in PLACEHOLDER.finditer(t)):
            return {}
        raw = _Normaliser(rules, self.reader.raw_copy()).request(request, "calls.?.request")
        sent = [raw["url"], canonical(_graphql(raw["url"], raw["body"], rules))]
        found: dict[str, Any] = {}
        for i, (text, live) in enumerate(zip(texts, sent)):
            pattern, groups = _binding_pattern(text, self.loose, json_text=i == 1)
            m = pattern.fullmatch(live)
            if m is None:
                return {}
            for group, (name, how) in groups.items():
                value = _bound_value(m.group(group), how)
                if isinstance(value, str) and PLACEHOLDER.fullmatch(value):
                    if value != name:
                        return None
                    continue
                if name in found and found[name] != value:
                    return None
                found[name] = value
        seen: set[str] = set()
        for name, value in found.items():
            known = self.reader.known.get(text_of(value))
            if (known is not None and known != name) or text_of(value) in seen:
                return None
            seen.add(text_of(value))
        return found

    def _answer(self, recorded: dict) -> Response:
        if recorded.get("status") is None:
            return Response(None, error=recorded.get("error") or "no answer")
        headers = {k: self.reader.fill_text(v) for k, v in (recorded.get("headers") or {}).items()}
        body = recorded.get("body")
        if body is None:
            data = b""
        elif "json" in body:
            data = json.dumps(self.reader.fill(body["json"])).encode("utf-8")
        elif "text" in body:
            data = self.reader.fill_text(body["text"]).encode("utf-8")
        else:
            data = base64.b64decode(body["base64"])
        lower = {k.lower(): v for k, v in headers.items()}
        return Response(recorded["status"], data, etag=lower.get("etag"), retry_after=lower.get("retry-after"),
                        headers=headers)

    def _describe(self, plugin: str | None, operation: str | None, method: str, url: str) -> str:
        return f"{plugin or '*'} {operation or '*'} {method} {url}"

    def _no_snapshot(self, request: Request, asked: dict, body: str, rules: dict) -> str:
        lines = [f"no recorded call in {self.scenario} for "
                 f"{self._describe(request.plugin, request.operation, asked['method'], asked['url'])}"]
        best, best_score = None, -1
        for i, call in enumerate(self.calls):
            want = self._recorded_key(call, rules)
            score = (4 * (call.plugin in (None, request.plugin) and call.operation in (None, request.operation))
                     + 2 * (want[0] == asked["method"]) + 3 * (want[1] == asked["url"]) + (want[2] in ("*", body)))
            score = 2 * score + (not call.used)
            if score > best_score:
                best, best_score = (i, call, want), score
        if best is None:
            lines.append("nearest: none, the cassette has no calls")
        else:
            i, call, want = best
            used = f", already answered {call.used} time{'s' * (call.used != 1)}" if call.used else ""
            lines.append(f"nearest: call {i}, "
                         f"{self._describe(call.plugin, call.operation, want[0], want[1])}{used}")
            lines += _differences(want, (asked["method"], asked["url"], body))
        lines.append(f'record it: pubtest record "{self.scenario}"')
        return "\n".join(lines)

    def report(self) -> dict:
        """What was replayed: how many calls the cassette holds, which of them (by
        index) answered, how many requests were sent and how many were answered."""
        return {"calls": len(self.calls), "used": [i for i, c in enumerate(self.calls) if c.used],
                "sent": len(self.sent), "answered": sum(c.used for c in self.calls)}

    def unused(self) -> list[str]:
        """The recorded calls never made, as "plugin operation METHOD url"."""
        return [self._describe(c.plugin, c.operation, c.request["method"].upper(), c.request["url"])
                for c in self.calls if not c.used]

    def close(self) -> None:
        """Raises `UnusedCalls` when a recorded call was never made; in record mode it
        only warns, as the recording being made replaces this one."""
        left = self.unused()
        if not left:
            return
        message = "\n".join([f"{len(left)} recorded call{'s' * (len(left) != 1)} in {self.scenario} never made:",
                             *(f"  {c}" for c in left), f'record it: pubtest record "{self.scenario}"'])
        if self.mode == "record":
            warnings.warn(message, stacklevel=2)
            return
        raise UnusedCalls(message)


def _binding_pattern(text: str, loose: set[str], *, json_text: bool) -> tuple[re.Pattern, dict[str, tuple]]:
    """`text` as a pattern a request's text matches, each loose placeholder in it a
    group: in JSON, a whole value (a string or a number) or a piece of a string."""
    out, groups, pieces = [], {}, _pieces(text)
    i = 0
    while i < len(pieces):
        held, piece = pieces[i]
        if not held or piece not in loose:
            out.append(re.escape(piece))
            i += 1
            continue
        group = f"g{len(groups)}"
        before = "".join(out)
        after = pieces[i + 1][1] if i + 1 < len(pieces) else ""
        if json_text and before.endswith('"') and after.startswith('"'):
            out[-1] = out[-1][:-1]  # the quote goes into the group: a string, or the number it stood for
            pieces[i + 1] = (pieces[i + 1][0], after[1:])
            out.append(rf'(?P<{group}>"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)')
            groups[group] = (piece, "json")
        elif json_text:
            out.append(rf'(?P<{group}>(?:[^"\\]|\\.)+?)')
            groups[group] = (piece, "inside")
        else:
            out.append(rf"(?P<{group}>.+?)")
            groups[group] = (piece, "text")
        i += 1
    return re.compile("".join(out), re.S), groups


def _bound_value(text: str, how: str) -> Any:
    if how == "json":
        return json.loads(text)
    if how == "inside":
        return json.loads(f'"{text}"')
    return text


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _json_differences(want: Any, got: Any, path: str, out: list[str]) -> None:
    if len(out) >= 3:
        return
    if isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            if key not in got:
                out.append(f"{path}.{key}: recorded {_short(canonical(want[key]))}, not sent")
            elif key not in want:
                out.append(f"{path}.{key}: sent {_short(canonical(got[key]))}, not recorded")
            else:
                _json_differences(want[key], got[key], f"{path}.{key}", out)
            if len(out) >= 3:
                return
    elif isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        for i, (w, g) in enumerate(zip(want, got)):
            _json_differences(w, g, f"{path}[{i}]", out)
    elif canonical(want) != canonical(got):
        out.append(f"{path}: recorded {_short(canonical(want))}, sent {_short(canonical(got))}")


def _sized(body: Any) -> str:
    if isinstance(body, dict) and "sha256" in body:
        return f"{body['length']} bytes, sha256 {body['sha256'][:12]}"
    return _short(canonical(body))


def _differences(want: tuple, got: tuple) -> list[str]:
    out = []
    if want[0] != got[0]:
        out.append(f"method: recorded {want[0]}, sent {got[0]}")
    if want[1] != got[1]:
        out.append(f"url: recorded {want[1]}, sent {got[1]}")
    if want[2] not in ("*", got[2]):
        w, g = json.loads(want[2]), json.loads(got[2])
        if isinstance(w, dict) and "sha256" in w or isinstance(g, dict) and "sha256" in g:
            out.append(f"body: recorded {_sized(w)}, sent {_sized(g)}")
            return out
        if isinstance(w, dict) and isinstance(g, dict) and len(w) == 1 and w.keys() == g.keys():
            kind = next(iter(w))
            if kind in ("json", "graphql"):
                found: list[str] = []
                _json_differences(w[kind], g[kind], "$", found)
                out += [f"body {line}" for line in found]
                return out
            out.append(f"body: recorded {_short(canonical(w[kind]))}, sent {_short(canonical(g[kind]))}")
        else:
            out.append(f"body: recorded {_short(canonical(w))}, sent {_short(canonical(g))}")
    return out


# --- the recorder ---------------------------------------------------------------

class Recorder:
    """A Transport that sends through `inner` and keeps every call, in order, with
    how long it took; `save()` writes them to `path` as a cassette.

    What is written follows each plugin's record rules: the headers it keeps (of
    requests and answers), text, JSON and binary bodies (base64), a request body
    over 64 KiB as its digest, volatile values as placeholders. A secret is never
    written: secret headers are dropped, secret JSON paths and query parameters
    replaced, and `save()` refuses (`SecretInRecording`) when a secret it saw would
    still be written anywhere.

    `names` (from each real name to the scenario's), over INTEGRATIONS_NAMES as it is
    when the recording is written, are written as the scenario's names.

    `save()` writes only inside the cassettes directory (`cassettes_dir()`), which
    is settled when the recorder is made: `INTEGRATIONS_CASSETTES`, else the
    directory of `INTEGRATIONS_CASSETTE`, else that of `path`. A path that resolves
    outside it raises `CassettePathError`."""

    def __init__(self, inner: Transport, path: Path | str | None = None, plugins: Any = registry, *,
                 scenario: str | None = None, consumer: str | None = None,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] | None = None,
                 names: dict[str, str] | None = None):
        self.inner, self.path, self.plugins = inner, Path(path) if path else None, plugins
        self.names = dict(names or {})
        self.root = cassettes_dir(self.path)
        self.scenario, self.consumer, self.clock = scenario, consumer, clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.calls: list[tuple[Request, Response, int]] = []

    def send(self, request: Request, timeout: float) -> Response:
        started = self.clock()
        response = self.inner.send(request, timeout)
        self.calls.append((request, response, int(round((self.clock() - started) * 1000))))
        return response

    def recording(self) -> dict:
        """The calls kept so far, normalised: a cassette's `current`. Raises
        `SecretInRecording` when a secret would be in it."""
        values = _Writer({**names_from(), **self.names})
        calls: list[dict] = []
        for _ in range(2):  # the second time, every value the first met is known everywhere
            calls = []
            for i, (request, response, elapsed) in enumerate(self.calls):
                normaliser = _Normaliser(self.plugins.record(request.plugin or None), values)
                calls.append({"kind": "http", "plugin": request.plugin or None, "operation": request.operation or None,
                              "request": normaliser.request(request, f"calls.{i}.request"),
                              "response": normaliser.response(response, f"calls.{i}.response"),
                              "elapsed_ms": elapsed})
        current = {"recorded": {"at": self.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "integrations": __version__,
                                "consumer": self.consumer},
                   "volatile": values.entries, "calls": calls}
        _no_secrets(current, values.secrets)
        return current

    def save(self, path: Path | str | None = None) -> Path:
        """Writes the recording to `path`. Into an existing cassette it goes as the
        candidate, `current`, and the file's previous `current` becomes its
        `baseline` unless it has one: a baseline is the last accepted recording,
        which a re-record never overwrites and only `promote()` moves."""
        given = Path(path) if path else self.path
        if given is None:
            raise ValueError("a recording needs a path to be saved to")
        root = self.root if self.root is not None else cassettes_dir(given)
        target = cassette_path(given, root)
        current = self.recording()
        old: dict = {}
        if target.exists():
            old = json.loads(target.read_text(encoding="utf-8"))
        baseline = None
        if old.get("format") == FORMAT:
            baseline = old.get("baseline") if old.get("baseline") is not None else old.get("current")
        scenario = self.scenario or old.get("scenario") or given.stem
        return write_cassette(target, {"format": FORMAT, "scenario": scenario, "current": current, "baseline": baseline},
                              root)


def _no_secrets(current: dict, secrets: dict[str, str]) -> None:
    for i, call in enumerate(current["calls"]):
        for side in ("request", "response"):
            written = json.dumps(call[side], ensure_ascii=False)
            for secret, seen in secrets.items():
                if secret in written or json.dumps(secret, ensure_ascii=False)[1:-1] in written:
                    raise SecretInRecording(f"a secret seen at {seen} would be written in calls.{i}.{side}: "
                                            "the recording is not saved")


def cassettes_dir(given: Path | str | None = None) -> Path | None:
    """The directory cassettes are written in, and an environment-named one read
    from, resolved: `INTEGRATIONS_CASSETTES` when it is set, else the directory
    holding `INTEGRATIONS_CASSETTE`, else the directory of `given`; None with none
    of them. A cassette's own name is not resolved here, so a cassette that is a
    symlink out of its directory still lands outside it."""
    if os.environ.get(CASSETTES):
        return Path(os.environ[CASSETTES]).resolve()
    named = os.environ.get(CASSETTE) or given
    return Path(named).absolute().parent.resolve() if named else None


def _refused(path: Path | str, target: Path, root: Path) -> CassettePathError:
    return CassettePathError(f"the cassette {path} is at {target}, outside the cassettes directory {root}: "
                             f"cassettes go in {CASSETTES}, else beside {CASSETTE}")


def cassette_path(path: Path | str, root: Path | str | None = None) -> Path:
    """`path` resolved, symlinks followed, when that is inside `root` (by default
    `cassettes_dir(path)`); else `CassettePathError`."""
    base = Path(root).resolve() if root is not None else cassettes_dir(path)
    target = Path(path).resolve()
    if base is None or not target.is_relative_to(base):
        raise _refused(path, target, base)
    return target


def write_cassette(path: Path | str, data: dict, root: Path | str | None = None) -> Path:
    """Writes `data` as a cassette at `path`, resolved, symlinks followed, and only
    when that is inside `root` (by default `cassettes_dir(path)`): else
    `CassettePathError`, and nothing is written. Returns where it wrote."""
    base = Path(root).resolve() if root is not None else cassettes_dir(path)
    if base is None:
        raise _refused(path, Path(path).resolve(), base)
    inside = os.path.realpath(str(base))
    target = os.path.realpath(os.path.abspath(os.fspath(path)))
    if not target.startswith(inside + os.sep):
        raise _refused(path, Path(target), base)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as out:
        out.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return Path(target)


def promote(path: Path | str) -> None:
    """Accepts the cassette's `current` recording: it becomes the baseline. The
    cassette must resolve inside `cassettes_dir(path)`, else `CassettePathError`."""
    root = cassettes_dir(path)
    target = cassette_path(path, root)
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path} is not an {FORMAT} cassette")
    data["baseline"] = data["current"]
    write_cassette(target, data, root)


# --- faults ---------------------------------------------------------------------

def _glob(pattern: str) -> re.Pattern:
    return re.compile("^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$")


class Fault:
    """One rule of `Faulty`: which calls (`match`), which of them (`nth`) and what
    happens to them (`effect`), with a `body` and `headers` for a status."""

    def __init__(self, rule: dict):
        self.rule = rule
        words = rule.get("match", "*").split()
        self.plugin, at = words[0] if words else "*", 1
        self.operation = None
        if len(words) > at and not words[at].isupper():
            self.operation, at = words[at], at + 1
        self.method = words[at] if len(words) > at else None
        self.path = _glob(words[at + 1]) if len(words) > at + 1 else None
        self.nth = str(rule.get("nth", "*"))
        self.effect, _, self.argument = str(rule["effect"]).partition(" ")
        if self.effect not in ("no-answer", "status", "slow", "malformed", "then-recorded"):
            raise ValueError(f"no fault {rule['effect']!r}: no-answer, status N, slow S, malformed or then-recorded")
        self.seen = 0

    def matches(self, request: Request) -> bool:
        plugin = request.plugin or ""
        if self.plugin != "*" and plugin != self.plugin and not plugin.startswith(self.plugin + "."):
            return False
        if self.operation and self.operation != request.operation:
            return False
        if self.method and self.method != request.method.upper():
            return False
        m = URL.match(request.url)
        return not self.path or bool(self.path.match(m.group(3) or "/" if m else request.url))

    def applies(self) -> bool:
        if self.nth == "*":
            return True
        if self.nth == "first":
            return self.seen == 1
        if self.nth.startswith("after "):
            return self.seen > int(self.nth[6:])
        return self.seen == int(self.nth)


class Faulty:
    """A Transport that makes calls through `inner` fail on purpose, by `rules`, each
    a dict:

    - `match`: "PLUGIN[ OPERATION][ METHOD PATH]", PATH a pattern in which `*` is
      anything (`"github.edit-pr open-pr POST /repos/*/pulls"`); "*" is any plugin,
      and a PLUGIN is also every plugin under it, before a dot (`github` is every
      github.* plugin);
    - `nth`: which of the calls it matches: "*" (every one, the default), "first",
      N (the Nth, from 1) or "after N";
    - `effect`: "no-answer"; "status N", with the rule's `body` (text, or JSON) and
      `headers`; "slow S", which advances `advance(S)`, a test's clock, and never
      sleeps, then answers, or gives no answer when S is past the call's timeout;
      "malformed", the answer with half its body and then text no parser reads; or
      "then-recorded", which passes the call through.

    The first rule that matches a call and whose `nth` it is decides; a call no rule
    decides passes through. Only no-answer and status keep the call from `inner`, so a
    cassette under it still holds the recorded call."""

    def __init__(self, inner: Transport, rules: list[dict], *, advance: Callable[[float], None] | None = None):
        self.inner, self.rules = inner, [Fault(r) for r in rules]
        self.advance = advance or (lambda seconds: None)
        self.slept = 0.0

    def send(self, request: Request, timeout: float) -> Response:
        chosen = None
        for fault in self.rules:
            if fault.matches(request):
                fault.seen += 1
                if chosen is None and fault.applies():
                    chosen = fault
        if chosen is None or chosen.effect == "then-recorded":
            return self.inner.send(request, timeout)
        if chosen.effect == "no-answer":
            return Response(None, error="no answer: fault injected")
        if chosen.effect == "status":
            body = chosen.rule.get("body")
            data = b"" if body is None else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
            headers = dict(chosen.rule.get("headers", {}))
            lower = {k.lower(): v for k, v in headers.items()}
            return Response(int(chosen.argument), data, etag=lower.get("etag"), retry_after=lower.get("retry-after"),
                            headers=headers)
        if chosen.effect == "slow":
            seconds = float(chosen.argument)
            if seconds >= timeout:
                self.advance(timeout)
                self.slept += timeout
                return Response(None, error=f"no answer: timed out after {timeout:g} s")
            self.advance(seconds)
            self.slept += seconds
            return self.inner.send(request, timeout - seconds)
        answer = self.inner.send(request, timeout)  # malformed
        if answer.status is None:
            return answer
        return replace(answer, body=answer.body[:len(answer.body) // 2] + "⟦malformed".encode("utf-8"))


# --- the current transport --------------------------------------------------------

_installed: ContextVar[tuple] = ContextVar("integrations_transports", default=())
_by_mode: dict[tuple[str, str], Any] = {}
_named: dict[tuple[str, str], str] = {}  # each cassette's INTEGRATIONS_CASSETTE, as it was given
_faulty: dict[tuple[str, str, str], Faulty] = {}
_clocks: ContextVar[tuple] = ContextVar("integrations_clocks", default=())


def check_mode(env: dict[str, str] | None = None) -> str:
    """INTEGRATIONS_MODE, checked: `live` (the default), `replay` or `record`. A mode
    other than live needs PUBTEST_RUN, which only a test harness sets, so a process
    misconfigured into answering from cassettes refuses to start; and
    INTEGRATIONS_CASSETTE, the cassette's file (where it may be is
    `cassettes_dir()`'s, checked by `current_transport()`)."""
    env = os.environ if env is None else env
    mode = env.get(MODE) or "live"
    if mode not in MODES:
        raise ModeError(f"{MODE} is {mode!r}: it is live, replay or record")
    if mode != "live" and not env.get(MARKER):
        raise ModeError(f"{MODE}={mode} without {MARKER}: only a test harness answers from or records cassettes, "
                        "so this process does not start")
    if mode != "live" and not env.get(CASSETTE):
        raise ModeError(f"{MODE}={mode} needs {CASSETTE}, the cassette's file")
    return mode


def fault_rules(env: dict[str, str] | None = None) -> list[dict]:
    """INTEGRATIONS_FAULTS: a JSON list of `Faulty`'s rules, which a harness sets so
    the programs it runs fail as it does; [] when it is not set. Raises `ModeError`
    when it is set without PUBTEST_RUN, and ValueError when it is no list of rules."""
    env = os.environ if env is None else env
    raw = env.get(FAULTS)
    if not raw:
        return []
    if not env.get(MARKER):
        raise ModeError(f"{FAULTS} without {MARKER}: only a test harness makes calls fail on purpose, "
                        "so this process does not start")
    try:
        rules = json.loads(raw)
    except ValueError:
        rules = None
    if not isinstance(rules, list) or not all(isinstance(r, dict) and "effect" in r for r in rules):
        raise ValueError(f"{FAULTS} is a JSON list of Faulty's rules, each with an effect, not {raw!r}")
    for rule in rules:
        Fault(rule)  # an effect that is not one is refused here, not at the call
    return rules


def current_transport() -> Transport:
    """The transport installed by `use()` in this context, else the one
    INTEGRATIONS_MODE picks: `Network()` (live), `Cassette(INTEGRATIONS_CASSETTE)`
    (replay) or `Recorder(Network(), INTEGRATIONS_CASSETTE)` (record), which saves
    when the process exits; under `Faulty` with INTEGRATIONS_FAULTS's rules when it
    names some (`fault_rules()`), which count this process's calls. Raises
    `ModeError` as `check_mode()` does, and `CassettePathError` when
    INTEGRATIONS_CASSETTE resolves outside the cassettes directory
    (`INTEGRATIONS_CASSETTES`, if set)."""
    mode = check_mode()
    installed = _installed.get()
    if installed:
        return installed[-1]
    path = str(cassette_path(os.environ[CASSETTE])) if mode != "live" else ""
    rules = fault_rules()
    key = (mode, path)
    if key not in _by_mode:
        if mode == "live":
            _by_mode[key] = Network()
        elif mode == "replay":
            _by_mode[key] = Cassette(path, mode="replay")
            _named[key] = os.environ[CASSETTE]
        else:
            recorder = Recorder(Network(), path)
            atexit.register(_save_at_exit, recorder)
            _by_mode[key] = recorder
    if not rules:
        return _by_mode[key]
    faulted = (mode, path, os.environ[FAULTS])
    if faulted not in _faulty:
        _faulty[faulted] = Faulty(_by_mode[key], rules)
    return _faulty[faulted]


def cassette_reports() -> list[dict]:
    """For a harness to check at exit what a process it ran replayed: one entry per
    cassette `current_transport()` answered from in this process, its
    INTEGRATIONS_CASSETTE as the process was given it (`cassette`) and its
    `Cassette.report()`."""
    return [{"cassette": _named[key], **transport.report()} for key, transport in list(_by_mode.items())
            if isinstance(transport, Cassette) and key in _named]


def _save_at_exit(recorder: Recorder) -> None:
    if recorder.calls:
        try:
            recorder.save()
        except (OSError, SecretInRecording) as e:
            print(f"integrations: the recording was not saved: {e}", file=sys.stderr)


def current_clock() -> tuple[Callable[[], float], Callable[[float], None], Callable[[], float]]:
    """The clock (seconds), sleep and random a `Caller` given none of them uses: the
    ones `use_clock()` installed in this context, else time.monotonic, time.sleep and
    random.random."""
    import random  # noqa: PLC0415

    clock, sleep, jitter = time.monotonic, time.sleep, random.random
    for installed in _clocks.get():
        clock, sleep, jitter = (installed[0] or clock, installed[1] or sleep, installed[2] or jitter)
    return clock, sleep, jitter


@contextmanager
def use_clock(clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None,
              random: Callable[[], float] | None = None) -> Iterator[None]:
    """Within the block, a Caller given no clock, sleep or random uses these, each
    looked up at each call, so a harness reaches a Caller made before it. Each one
    not given stays as it was. For test harnesses only."""
    token = _clocks.set((*_clocks.get(), (clock, sleep, random)))
    try:
        yield
    finally:
        _clocks.reset(token)


@contextmanager
def use(transport: Transport) -> Iterator[Transport]:
    """Within the block, `current_transport()` is `transport` in this context, so a
    Caller given none calls through it. For test harnesses only."""
    token = _installed.set((*_installed.get(), transport))
    try:
        yield transport
    finally:
        _installed.reset(token)
