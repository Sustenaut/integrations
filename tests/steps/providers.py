"""The providers the suite's clients call, as pubtest's fake services, and
the call records a caller keeps.

A call to https://HOST/PATH goes to pubtest's fake service named HOST (`Given
the "api.github.com" service answers "GET /repos/o/r" with 200 and:`), over
plain HTTP, so pubtest's steps check what was asked and sent. A host with no
fake service, or one a Given says does not answer, gives no answer, as a
provider that is down does.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlsplit

from integrations.calls import Caller, MemorySink, Request, Response


class Providers:
    """A transport to pubtest's fake services, one per host."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.silent: set[str] = set()

    def send(self, request: Request, timeout: float) -> Response:
        url = urlsplit(request.url)
        fake = getattr(self.ctx, "fakes", {}).get(url.hostname)
        if fake is None or url.hostname in self.silent:
            return Response(None, error="no answer")
        target = fake.url + url.path + (f"?{url.query}" if url.query else "")
        outgoing = urllib.request.Request(target, data=request.body, method=request.method, headers=request.headers)
        try:
            with urllib.request.urlopen(outgoing, timeout=timeout) as answer:  # noqa: S310 - the fake, on 127.0.0.1
                return Response(answer.status, answer.read(), etag=answer.headers.get("ETag"),
                                retry_after=answer.headers.get("Retry-After"))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), etag=e.headers.get("ETag") if e.headers else None,
                            retry_after=e.headers.get("Retry-After") if e.headers else None)


def providers(ctx) -> Providers:
    """The scenario's transport to the fake providers."""
    if getattr(ctx, "providers", None) is None:
        ctx.providers = Providers(ctx)
    return ctx.providers


def records(ctx) -> MemorySink:
    """The scenario's sink of call records."""
    if getattr(ctx, "sink", None) is None:
        ctx.sink = MemorySink()
    return ctx.sink


def caller(ctx, repo: str = "a-repo", **kwargs) -> Caller:
    """A caller to the fake providers, keeping its records, that never sleeps
    unless the scenario's own clock says otherwise."""
    kwargs.setdefault("sleep", getattr(ctx, "sleep", lambda _: None))
    return Caller(repo, sink=records(ctx), transport=providers(ctx), **kwargs)
