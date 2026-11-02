"""Call records as an Avro file. The same records always give the same bytes, so
storing or loading a file twice is harmless."""

from __future__ import annotations

import hashlib
import io
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


def _row(record: dict) -> dict:
    at = datetime.strptime(record["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return {**record, "at": at}


def encode(records: list[dict]) -> bytes:
    out = io.BytesIO()
    marker = hashlib.sha256(repr(sorted(map(repr, records))).encode()).digest()[:16]
    fastavro.writer(out, SCHEMA, [_row(r) for r in records], codec="deflate", sync_marker=marker,
                    metadata={"integrations.schema": "call-record"})
    return out.getvalue()


def decode(data: bytes) -> list[dict]:
    rows = []
    for row in fastavro.reader(io.BytesIO(data)):
        rows.append({**row, "at": row["at"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return rows
