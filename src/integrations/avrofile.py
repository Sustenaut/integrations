"""Call records as an Avro file. The same records always give the same bytes, so
storing or loading a file twice is harmless. A sender's report for the cycle travels in
the file's metadata, and becomes a row of the status table."""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone

import fastavro

SCHEMA = fastavro.parse_schema({
    "type": "record", "name": "call", "fields": [
        {"name": "at", "type": {"type": "long", "logicalType": "timestamp-micros"}},
        {"name": "repo", "type": "string"},
        {"name": "app", "type": "string"},
        {"name": "plugin", "type": "string"},
        {"name": "operation", "type": "string"},
        {"name": "request_id", "type": "string"},
        {"name": "latency_ms", "type": "long"},
        {"name": "status", "type": ["null", "long"]},
        {"name": "outcome", "type": "string"},
        {"name": "retries", "type": "long"},
        {"name": "bytes_in", "type": "long"},
        {"name": "bytes_out", "type": "long"},
        {"name": "mode", "type": "string"},
        {"name": "route", "type": "string"},
    ]})


_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_STATUS_KEY = "integrations.status"
_TIME = {"type": "long", "logicalType": "timestamp-micros"}


def _optional(name: str, kind: str) -> dict:
    return {"name": name, "type": ["null", kind], "default": None}


STATUS_SCHEMA = fastavro.parse_schema({
    "type": "record", "name": "status", "fields": [
        {"name": "at", "type": _TIME},
        {"name": "kind", "type": "string"},
        {"name": "sender", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "cycle_start", "type": _TIME},
        {"name": "cycle_end", "type": _TIME},
        *[_optional(name, "long") for name in (
            "calls", "drops", "buffer_size", "storage_failures", "loaded_files", "rejected_now", "inbox_files",
            "inbox_bytes", "inbox_oldest_seconds", "rejected_files", "rejected_oldest_seconds", "jobs_today",
            "status_jobs_today", "page_count", "page_ms_low", "page_ms_median", "page_ms_high")],
        *[_optional(name, "boolean") for name in ("storage_ok", "inbox_more", "rejected_more", "cold_start")],
        _optional("problems", "string"),
    ]})


def timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(_FORMAT)


def _time(text: str) -> datetime:
    return datetime.strptime(text, _FORMAT).replace(tzinfo=timezone.utc)


def _row(record: dict) -> dict:
    return {**record, "at": _time(record["at"])}


def file_name(seconds: float, sender: str, data: bytes) -> str:
    """Where a file goes in a buffer: named for when, who, and what is in it."""
    moment = datetime.fromtimestamp(seconds, timezone.utc)
    who = re.sub(r"[^A-Za-z0-9.-]", "-", sender)
    return (f"inbox/calls/{moment:%Y-%m-%d}/{moment:%H%M%S}-{who}-"
            f"{hashlib.sha256(data).hexdigest()[:16]}.avro")


def encode(records: list[dict], status: dict | None = None) -> bytes:
    out = io.BytesIO()
    marker = hashlib.sha256(repr(sorted(map(repr, records))).encode()).digest()[:16]
    fastavro.writer(out, SCHEMA, [_row(r) for r in records], codec="deflate", sync_marker=marker,
                    metadata={"integrations.schema": "call-record",
                              **({_STATUS_KEY: json.dumps(status, sort_keys=True)} if status else {})})
    return out.getvalue()


def encode_status(rows: list[dict]) -> bytes:
    out = io.BytesIO()
    marker = hashlib.sha256(repr(sorted(map(repr, rows))).encode()).digest()[:16]
    fastavro.writer(out, STATUS_SCHEMA, [
        {**row, "at": _time(row["at"]), "cycle_start": _time(row["cycle_start"]), "cycle_end": _time(row["cycle_end"])}
        for row in rows], codec="deflate", sync_marker=marker)
    return out.getvalue()


def file_status(data: bytes) -> dict | None:
    """The sender's report in a file, if it has one."""
    text = fastavro.reader(io.BytesIO(data)).metadata.get(_STATUS_KEY)
    return json.loads(text) if text else None


def decode(data: bytes) -> list[dict]:
    rows = []
    for row in fastavro.reader(io.BytesIO(data)):
        rows.append({**row, "at": row["at"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return rows
