"""Storage's word that a file arrived in a buffer's inbox: POST /ingest/notify.

Storage (Sustenaut/storage), and integrations' call-log Durable Object, which writes into
R2 itself, send one notice for each file they took into a buffer's inbox/:

    POST /ingest/notify
    X-Ingest-Timestamp: 1800000000                      (Unix seconds, when it was signed)
    X-Ingest-Signature: sha256=<hex>                    (HMAC-SHA256 of "<timestamp>." + body)
    {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}

The key is INGEST_NOTIFY_KEY, the same secret on both sides. Without it the route refuses
everything, and the sweep alone finds the files, as before. A notice signed more than
MAX_AGE_SECONDS from the pod's clock, either way, is refused, so one seen once can't be
sent again later.

A notice is a hint, never data: it names a file, and the scanner reads that file from
storage with the pod's own token, and only from a buffer it scans, under the folder the
buffer's kind takes. A forged one could at most make the pod look for a file that isn't
there.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Callable

from .inbox import Kind

ROUTE = "/ingest/notify"
MAX_AGE_SECONDS = 300
# As storage's BUFFER takes an inbox/ file: up to six folders, then an Avro file's name.
NAME = re.compile(r"inbox/(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}/){0,5}[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.avro")
MAX_BYTES = 50 * 1024 * 1024


def signature(key: str, timestamp: str, body: bytes) -> str:
    """What X-Ingest-Signature carries for a body signed at `timestamp`."""
    mac = hmac.new(key.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Notices:
    """Checks a notice and hands its file to the scanner.

    `key` is INGEST_NOTIFY_KEY, empty when it isn't set. `buffers` is each buffer the
    scanner scans, by its key, with its tenant and Kind; `take` gets the buffer's key, the
    file's name and its size."""

    key: str
    buffers: dict[str, tuple[str, Kind]]
    take: Callable[[str, str, int], None]
    clock: Callable[[], float]

    def receive(self, timestamp: str | None, signed: str | None, body: bytes) -> dict:
        if not self.key:
            raise Refused(403, "notices are off: INGEST_NOTIFY_KEY isn't set")
        if not timestamp or not signed or not timestamp.isdigit():
            raise Refused(401, "a notice is signed: X-Ingest-Timestamp and X-Ingest-Signature")
        if not hmac.compare_digest(signed, signature(self.key, timestamp, body)):
            raise Refused(401, "the signature isn't this body's")
        if abs(self.clock() - int(timestamp)) > MAX_AGE_SECONDS:
            raise Refused(401, f"signed more than {MAX_AGE_SECONDS} seconds from now")
        try:
            notice = json.loads(body)
        except ValueError:
            raise Refused(400, "the body is JSON") from None
        if not isinstance(notice, dict) or not isinstance(notice.get("tenant"), str) or not isinstance(
                notice.get("name"), str) or type(notice.get("size")) is not int or not 0 <= notice["size"] <= MAX_BYTES:
            raise Refused(400, 'the body is {"tenant": ..., "name": ..., "size": ...}')
        tenant, name = notice["tenant"], notice["name"]
        if not NAME.fullmatch(name):
            raise Refused(422, f"{name} is no file in an inbox")
        for key, (buffer_tenant, kind) in sorted(self.buffers.items()):
            if buffer_tenant == tenant and name.startswith(kind.folder):
                self.take(key, name, notice["size"])
                return {"buffer": key}
        raise Refused(422, f"no buffer here takes {name} in {tenant}")
