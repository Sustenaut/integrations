"""storage.api: one tenant's files, named heads and buffers in the storage service, each
call made through a `Caller`, so it has its class's timeout and retries and writes its
record. `storage_routes.ROUTES` is generated from the service's own spec; this is the
part written by hand, as clients/js/src/storage.js is.

    from integrations.clients.storage import Storage

    storage = Storage(caller, "opportunity", token_file=Path("/var/run/secrets/storage/token"))
    stored = storage.upload_file(data, name="offer.pdf", subject="card-42")
    files = storage.list_files(subject="card-42")

Storage answering no is a `StorageError` (`NotFound` for a 404) with its status and its
{"error"}; storage failing or not answering is `StorageUnavailable`, with them where it
answered.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

from ..calls import Caller, Refused, Request, Response, Unavailable
from .storage_routes import ROUTES

PLUGIN = "storage.api"
BASE_URL = "https://storage.sustenaut.eu"
BUFFER_ROUTES = {"inbox": ("Inbox", "path"), "rejected": ("Rejected", "path"), "batches": ("Batches", "name")}


def reason(response: Response | None) -> str:
    """Storage's {"error": ...}, or "" when the body has none."""
    try:
        error = (response.json() or {}).get("error") if response is not None else None
    except (ValueError, AttributeError):
        return ""
    return "" if error is None else str(error)[:200]


class StorageError(Refused):
    """Storage said no: `status` is its HTTP status, `error` its reason."""

    def __init__(self, operation: str, status: int, error: str):
        super().__init__(PLUGIN, operation, f"HTTP {status} {error}".rstrip())
        self.status, self.error = status, error


class NotFound(StorageError):
    pass


class StorageUnavailable(Unavailable):
    """Storage failed (5xx, 429) or didn't answer (`status` None)."""

    def __init__(self, cause: Unavailable):
        super().__init__(cause.plugin, cause.operation, cause.detail, cause.response)
        self.status = cause.response.status if cause.response is not None else None
        self.error = reason(cause.response)


class Storage:
    """`token` is a string or a callable giving one; `token_file` is read for each
    request, as Kubernetes renews it. `request_id`, when given, goes with every call as
    X-Request-Id."""

    def __init__(self, caller: Caller, tenant: str, *, base_url: str = BASE_URL,
                 token: str | Callable[[], str] | None = None, token_file: Path | None = None,
                 request_id: str | None = None, user_agent: str | None = None):
        if not token and not token_file:
            raise ValueError("a Storage needs a token or a token_file")
        self.caller, self.tenant, self.base = caller, tenant, base_url.rstrip("/")
        self.token, self.token_file, self.request_id, self.user_agent = token, token_file, request_id, user_agent

    def for_tenant(self, tenant: str) -> Storage:
        """The same client, for another tenant."""
        return Storage(self.caller, tenant, base_url=self.base, token=self.token, token_file=self.token_file,
                       request_id=self.request_id, user_agent=self.user_agent)

    # --- Files

    def upload_file(self, data: bytes, *, name: str, label: str | None = None, subject: str | None = None,
                    kept: bool | None = None, for_: str | None = None, **options: Any) -> dict:
        """Stores one file under its content's hash: {id, name, url, type, size, label, subject, kept}."""
        fields = {"name": name, "label": label, "subject": subject,
                  "kept": None if kept is None else str(kept).lower(), "for": for_}
        return self._call("uploadFile", "write", body=_Form(name, data, fields), **options)[0]

    def get_file(self, id: str, name: str, **options: Any) -> bytes:  # noqa: A002 - the spec's name
        return self._call("getFile", "read", path={"id": id, "name": name}, **options)[0]

    def list_files(self, *, prefix: str = "f/", subject: str | None = None, limit: int | None = None,
                   **options: Any) -> list[dict]:
        """Every file whose name starts with `prefix` (f/, f/<id>/ ...), from every page; with
        `subject`, only the files an upload gave that subject."""
        files = self._pages(prefix, limit, "read", options)
        return files if subject is None else [f for f in files if f.get("metadata", {}).get("subject") == subject]

    # --- Named heads

    def get_head(self, name: str, **options: Any) -> tuple[bytes, str | None]:
        """The file a head points at, and its hash (its ETag)."""
        body, response = self._call("getHead", "read", path={"name": name}, **options)
        return body, re.sub(r'^W/|"', "", response.etag) if response.etag else None

    def move_head(self, name: str, hash: str, *, if_match: str | None = None, **options: Any) -> dict:  # noqa: A002
        """Moves the head to a stored file's hash, if it still points at `if_match`; without
        it, only if it has never moved: {hash, key, time, principal}."""
        headers = {"If-Match": f'"{if_match}"'} if if_match else {"If-None-Match": "*"}
        return self._call("moveHead", "write", path={"name": name}, headers=headers, body={"hash": hash},
                          **options)[0]

    def head_log(self, name: str, **options: Any) -> list[dict]:
        """Where the head points, then where it pointed, newest first."""
        return self._call("getHeadLog", "read", path={"name": name}, **options)[0]["log"]

    # --- Buffers: `place` is the file's place in the tenant, such as inbox/calls/x.avro.

    def put_buffer(self, place: str, data: bytes, **options: Any) -> dict:
        return self._buffer("put", place, options, data)[0]

    def get_buffer(self, place: str, **options: Any) -> Any:
        return self._buffer("get", place, options)[0]

    def delete_buffer(self, place: str, **options: Any) -> None:
        self._buffer("delete", place, options)

    def list_buffer(self, prefix: str, *, limit: int | None = None, **options: Any) -> list[dict]:
        """Every file under `prefix` (inbox/, batches/, rejected/ and folders under them), from every page."""
        return self._pages(prefix, limit, "buffer", options)

    def _buffer(self, verb: str, place: str, options: dict, data: bytes | None = None) -> tuple[Any, Response]:
        folder, _, rest = place.partition("/")
        if folder not in BUFFER_ROUTES or not rest:
            raise ValueError(f"{place} is not in inbox/, batches/ or rejected/")
        name, param = BUFFER_ROUTES[folder]
        return self._call(f"{verb}{name}", "buffer", path={param: rest}, body=data, **options)

    def _pages(self, prefix: str, limit: int | None, operation: str, options: dict) -> list[dict]:
        found: list[dict] = []
        cursor = None
        while True:
            page = self._call("list", operation, query={"prefix": prefix, "limit": limit, "cursor": cursor}, **options)[0]
            found += page["files"]
            cursor = page.get("cursor")
            if not cursor:
                return found

    def _call(self, id: str, operation: str, *, path: dict | None = None, query: dict | None = None,  # noqa: A002
              headers: dict | None = None, body: Any = None, request_id: str | None = None,
              as_operation: str | None = None) -> tuple[Any, Response]:
        """One call: the route's URL, the token, the body as the route takes it, and the
        answer as it gives it. `as_operation` records it as another storage.api operation."""
        route = ROUTES[id]
        path, query = path or {}, query or {}
        request_id = request_id or self.request_id
        operation = as_operation or operation

        def place(match: re.Match) -> str:
            name = match[1]
            value = self.tenant if name == "tenant" else str(path[name])
            if name in route["slashes"]:
                return "/".join(quote(part, safe="") for part in value.split("/"))
            return quote(value, safe="")

        url = self.base + re.sub(r"\{(\w+)\}", place, route["path"])
        asked = [(name, str(query[name])) for name in route["query"] if query.get(name) is not None]
        if asked:
            url += "?" + urlencode(asked)
        sent = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
        if request_id:
            sent["X-Request-Id"] = request_id
        if self.user_agent:
            sent["User-Agent"] = self.user_agent
        data = body
        if isinstance(body, _Form):
            data, sent["Content-Type"] = body.encode()
        elif route["body"]:
            sent["Content-Type"] = route["body"]
            if route["body"] == "application/json" and not isinstance(body, (bytes, str)):
                data = json.dumps(body).encode()
        if isinstance(data, str):
            data = data.encode()
        try:
            response = self.caller.call(PLUGIN, operation, Request(route["method"], url, sent, data),
                                        request_id=request_id)
        except Unavailable as e:
            raise StorageUnavailable(e) from e
        if not response.ok:
            refusal = NotFound if response.status == 404 else StorageError
            raise refusal(operation, response.status, reason(response))
        if route["answer"] == "json":
            return response.json(), response
        if route["answer"] == "bytes":
            return response.body, response
        return None, response

    def _token(self) -> str:
        if self.token:
            return self.token() if callable(self.token) else self.token
        return Path(self.token_file).read_text(encoding="utf-8").strip()


class _Form:
    """A multipart/form-data body: the file, then each field that has a value."""

    def __init__(self, filename: str, data: bytes, fields: dict[str, str | None]):
        self.filename, self.data, self.fields = filename, data, fields

    def encode(self) -> tuple[bytes, str]:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{self.filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + self.data + b"\r\n"]
        for name, value in self.fields.items():
            if value is not None:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
