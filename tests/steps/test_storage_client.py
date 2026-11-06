"""The storage client (integrations.clients.storage), on the vectors in tests/vectors/storage/
that clients/js/storage.js runs too, and what they don't cover."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from integrations.calls import Caller, MemorySink, Replay
from integrations.clients import storage as client
from integrations.clients.storage import NotFound, Storage, StorageError, StorageUnavailable
from integrations.clients.storage_routes import ROUTES, SPEC

HERE = Path(__file__).resolve().parents[1]
VECTORS = sorted((HERE / "vectors" / "storage").glob("*.json"))
BASE = "https://storage.example"


def value(arg):
    return arg["bytes"].encode() if isinstance(arg, dict) and "bytes" in arg else arg


def plain(result):
    """A result as the vectors write it: bytes as {"text"}, a head as {"body", "hash"}."""
    if isinstance(result, bytes):
        return {"text": result.decode()}
    if isinstance(result, tuple):
        return {"body": plain(result[0]), "hash": result[1]}
    return result


def made(vector, **kw):
    sink, naps = MemorySink(), []
    replay = Replay(vector["responses"])
    caller = Caller("a-repo", "web", sink=sink, transport=replay, sleep=naps.append, new_id=lambda: "id-1",
                    random=lambda: 1.0)
    options = vector["client"]
    storage = Storage(caller, options["tenant"], base_url=BASE, token=options["token"],
                      request_id=options.get("request_id"), user_agent=options.get("user_agent"), **kw)
    return storage, sink, replay, naps


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_a_vector(path):
    vector = json.loads(path.read_text(encoding="utf-8"))
    storage, sink, replay, naps = made(vector)
    kwargs = {("for_" if k == "for" else k): v for k, v in vector["kwargs"].items()}
    expect = vector["expect"]
    raised, result = None, None
    try:
        result = plain(getattr(storage, vector["call"])(*map(value, vector["args"]), **kwargs))
    except (StorageError, StorageUnavailable) as e:
        raised = {"type": type(e).__name__, "status": e.status, "error": e.error}
    assert (raised, result, naps) == (expect["raises"], expect["result"], expect["sleeps"])
    assert len(replay.sent) == len(expect["sent"])
    for sent, want in zip(replay.sent, expect["sent"]):
        assert (sent.method, sent.url) == (want["method"], want["url"])
        assert {k: sent.headers.get(k) for k in want.get("headers", {})} == want.get("headers", {})
        if "json" in want:
            assert json.loads(sent.body) == want["json"]
    records = [asdict(r) for r in sink.records]
    assert [{k: r[k] for k in want} for r, want in zip(records, expect["records"])] == expect["records"]
    assert len(records) == len(expect["records"])


def test_the_routes_are_the_pinned_specs():
    lock = json.loads((HERE.parent / "openapi" / "storage.lock.json").read_text())
    assert SPEC == {k: lock[k] for k in ("repository", "commit", "sha256")}
    assert ROUTES["list"]["query"] == ["prefix", "limit", "cursor"]


def test_an_upload_is_one_multipart_body_with_the_fields_given():
    vector = {"client": {"tenant": "deals", "token": "t"},
              "responses": {f"POST {BASE}/deals/files": {"status": 200, "json": {}}}}
    storage, _, replay, _ = made(vector)
    storage.upload_file(b"%PDF-1.7", name="a.pdf", kept=False, for_="someone")
    sent = replay.sent[0]
    boundary = sent.headers["Content-Type"].split("boundary=")[1]
    body = sent.body.decode()
    assert body.startswith(f"--{boundary}\r\n") and body.endswith(f"--{boundary}--\r\n")
    assert 'name="file"; filename="a.pdf"\r\nContent-Type: application/octet-stream\r\n\r\n%PDF-1.7\r\n' in body
    for field, text in (("name", "a.pdf"), ("kept", "false"), ("for", "someone")):
        assert f'name="{field}"\r\n\r\n{text}\r\n' in body
    assert 'name="label"' not in body and 'name="subject"' not in body


def test_the_token_file_is_read_for_each_request_and_another_tenant_shares_it(tmp_path):
    token = tmp_path / "token"
    token.write_text("first\n")
    vector = {"client": {"tenant": "deals", "token": None},
              "responses": {f"GET {BASE}/deals/head/board/log": {"status": 200, "json": {"log": []}},
                            f"GET {BASE}/other/head/board/log": {"status": 200, "json": {"log": []}}}}
    storage, _, replay, _ = made(vector, token_file=token)
    storage.head_log("board")
    token.write_text("second\n")
    storage.for_tenant("other").head_log("board")
    assert [s.headers["Authorization"] for s in replay.sent] == ["Bearer first", "Bearer second"]
    assert replay.sent[1].url == f"{BASE}/other/head/board/log"


def test_a_token_can_be_a_callable_and_one_is_needed():
    vector = {"client": {"tenant": "deals", "token": lambda: "minted"},
              "responses": {f"GET {BASE}/deals/head/board/log": {"status": 200, "json": {"log": []}}}}
    storage, _, replay, _ = made(vector)
    storage.head_log("board")
    assert replay.sent[0].headers["Authorization"] == "Bearer minted"
    with pytest.raises(ValueError, match="a token or a token_file"):
        Storage(Caller("a-repo"), "deals")


def test_a_place_outside_the_buffers_folders_is_refused_here():
    storage, _, replay, _ = made({"client": {"tenant": "deals", "token": "t"}, "responses": {}})
    for place in ("f/x", "inbox", "other/a.avro"):
        with pytest.raises(ValueError, match="is not in inbox/, batches/ or rejected/"):
            storage.get_buffer(place)
    assert not replay.sent


def test_errors_say_storages_reason_and_are_the_callers_kinds():
    error = NotFound("read", 404, "not found")
    assert isinstance(error, StorageError) and str(error) == "storage.api read: HTTP 404 not found"
    assert str(StorageError("write", 500, "")) == "storage.api write: HTTP 500"
    assert client.reason(None) == "" and client.reason(client.Response(500, b"<html>")) == ""


def test_a_weak_etag_still_gives_the_hash():
    vector = {"client": {"tenant": "deals", "token": "t"},
              "responses": {f"GET {BASE}/deals/head/board": {"status": 200, "text": "{}", "headers": {"ETag": 'W/"abc"'}}}}
    storage, _, _, _ = made(vector)
    assert storage.get_head("board") == (b"{}", "abc")
