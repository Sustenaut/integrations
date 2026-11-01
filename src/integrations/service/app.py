"""The integrations service. github.edit-pr's route acts as the person whose token
comes with the request: the token is used for that request and forgotten, never
stored or logged, and the pull request is theirs.

It is not an open proxy: it serves only the repositories a deployment names, and
GitHub checks the token on every call.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request as HttpRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..calls import Caller, IntegrationError, Sink, StdoutSink, Transport, Unavailable
from ..clients.github_edit_pr import GitHubRefused, OneCommitProposer
from .settings import Settings

REQUEST_ID_HEADER = "X-Request-Id"
bearer = HTTPBearer(description="The person's own GitHub token. It is used for this request and never kept.")


class RateLimit:
    """At most `limit` calls in any `window` seconds, for one plugin."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.calls: list[float] = []
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            self.calls = [t for t in self.calls if now - t < self.window]
            if len(self.calls) >= self.limit:
                return False
            self.calls.append(now)
            return True


class Proposal(BaseModel):
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$", description="owner/name")
    branch: str = Field(min_length=1, description="The new branch. A branch that exists already is refused.")
    title: str = Field(min_length=1)
    changes: dict[str, str | None] = Field(description="Each file's path, to its new text or to null to delete it.")
    body: str = ""
    base: str | None = Field(default=None, description="By default, the repository's default branch.")
    message: str | None = Field(default=None, description="The commit's message. By default, the title and the body.")
    parent: str | None = Field(default=None, description="The commit to build on, instead of the newest on base.")
    expect: dict[str, str | None] | None = Field(
        default=None, description="Each path to the blob sha it was read at, or null if it was missing.")
    autoMerge: bool = Field(default=False, description="Merge the pull request once its required checks pass.")


class Opened(BaseModel):
    status: Literal["opened"]
    url: str
    number: int
    branch: str
    commit: str
    autoMerge: str | None = Field(default=None, description='"on", "merged", or why GitHub refused.')


class NothingToChange(BaseModel):
    status: Literal["nothing to change"]


class Stale(BaseModel):
    status: Literal["stale"]
    path: str


class Problem(BaseModel):
    detail: str | list[dict]


def create_app(settings: Settings | None = None, *, transport: Transport | None = None,
               sink: Sink | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # A token could only reach a page's URL by mistake, so the interactive docs,
    # which load scripts from a CDN, are off. The docs are built from the spec.
    app = FastAPI(title="Integrations service", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(settings.allowed_origins), allow_methods=["POST"],
                       allow_headers=["authorization", "content-type", REQUEST_ID_HEADER.lower()],
                       allow_credentials=False)
    caller = Caller("integrations", "service", sink=sink or StdoutSink(), transport=transport)
    edit_pr_limit = RateLimit(settings.edit_pr_per_minute)

    @app.middleware("http")
    async def limit_body(request: HttpRequest, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return JSONResponse({"detail": "the request is too large"}, status_code=413)
        return await call_next(request)

    if settings.docs_dir:
        app.mount("/docs", StaticFiles(directory=settings.docs_dir, html=True), name="docs")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs/")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/github/edit-pr/proposals", tags=["github.edit-pr"], operation_id="propose",
              response_model=Opened | NothingToChange | Stale, response_model_exclude_none=True,
              responses={code: {"model": Problem} for code in (401, 403, 404, 413, 422, 429, 502, 503)})
    def propose(proposal: Proposal, request: HttpRequest,
                credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
        """Open a pull request with one commit, as the person whose token this is."""
        if proposal.repo not in settings.edit_pr_repos:
            raise HTTPException(403, f"{proposal.repo} is not served here")
        if not edit_pr_limit.allow():
            raise HTTPException(429, "too many proposals: try again in a minute")
        token = credentials.credentials
        proposer = OneCommitProposer(caller, lambda: token, request.headers.get(REQUEST_ID_HEADER))
        try:
            return proposer.propose(repo=proposal.repo, branch=proposal.branch, title=proposal.title,
                                    changes=proposal.changes, body=proposal.body, base=proposal.base,
                                    message=proposal.message, parent=proposal.parent, expect=proposal.expect,
                                    auto_merge=proposal.autoMerge)
        except GitHubRefused as e:
            raise HTTPException(e.status, f"GitHub said {e.status}: {e.message}") from None
        except Unavailable:
            raise HTTPException(503, "GitHub did not answer: try again") from None
        except IntegrationError:
            raise HTTPException(502, "GitHub's answer could not be used") from None

    return app
