"""The integrations service. github.edit-pr's route acts as the person whose token
comes with the request: the token is used for that request and forgotten, never
stored or logged, and the pull request is theirs.

It is not an open proxy: it serves only the repositories a deployment names, and
GitHub checks the token on every call.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request as HttpRequest, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..calls import Caller, IntegrationError, Sink, StdoutSink, Transport, Unavailable
from ..clients.github_edit_pr import GitHubRefused, OneCommitProposer, RepositoryReader
from .settings import Settings

REQUEST_ID_HEADER = "X-Request-Id"
REPO_PATTERN = r"^[\w.-]+/[\w.-]+$"
REQUEST_ID = re.compile(r"[\w-]{1,64}")
EDIT_PR = "github.edit-pr"
MAX_TIMINGS = 200
MAX_FILES = 2000
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
    repo: str = Field(pattern=REPO_PATTERN, description="owner/name")
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


class Snapshot(BaseModel):
    branch: str
    commit: str
    can_push: bool = Field(description="Whether this token may write to the repository.")
    paths: list[str] = Field(description="Every file's path at the commit.")
    truncated: bool = Field(description="GitHub left some paths out, as the repository is very large.")


class FilesRequest(BaseModel):
    repo: str = Field(pattern=REPO_PATTERN, description="owner/name")
    commit: str = Field(min_length=1, description="The commit to read from.")
    paths: list[str] = Field(max_length=MAX_FILES, description="The paths to read.")


class Files(BaseModel):
    files: dict[str, str | None] = Field(description="Each path, to its text or to null if it isn't there.")


class Timing(BaseModel):
    request_id: str = Field(pattern=f"^{REQUEST_ID.pattern}$")
    total_ms: int = Field(ge=0, le=3_600_000)


class Timings(BaseModel):
    repo: str = Field(pattern=REPO_PATTERN, description="owner/name")
    timings: list[Timing] = Field(max_length=MAX_TIMINGS, description="How long each request took the app, as it saw it.")


class Problem(BaseModel):
    detail: str | list[dict]


def create_app(settings: Settings | None = None, *, transport: Transport | None = None,
               sink: Sink | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # A token could only reach a page's URL by mistake, so the interactive docs,
    # which load scripts from a CDN, are off. The docs are built from the spec.
    app = FastAPI(title="Integrations service", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(settings.allowed_origins), allow_methods=["GET", "POST"],
                       allow_headers=["authorization", "content-type", REQUEST_ID_HEADER.lower()],
                       expose_headers=[REQUEST_ID_HEADER], allow_credentials=False)
    caller = Caller("integrations", "service", sink=sink or StdoutSink(), transport=transport)
    edit_pr_limit = RateLimit(settings.edit_pr_per_minute)
    edit_pr_read_limit = RateLimit(settings.edit_pr_reads_per_minute)
    problems = {code: {"model": Problem} for code in (401, 403, 404, 413, 422, 429, 502, 503)}

    def served(repo: str, limit: RateLimit) -> None:
        if repo not in settings.edit_pr_repos:
            raise HTTPException(403, f"{repo} is not served here")
        if not limit.allow():
            raise HTTPException(429, "too many requests: try again in a minute")

    @contextmanager
    def githubs_answer(repo: str, request: HttpRequest, response: Response):
        """One id for the request and every call it makes, GitHub's refusal or silence
        as the person should hear it, and a record of how long the whole request took,
        so that what isn't GitHub's time can be told from what is."""
        asked = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = asked if REQUEST_ID.fullmatch(asked) else uuid.uuid4().hex
        response.headers[REQUEST_ID_HEADER] = request_id
        started = time.monotonic()
        status, outcome = 200, "ok"
        try:
            yield caller.for_repo(repo), request_id
        except GitHubRefused as e:
            status, outcome = e.status, "refused"
            raise HTTPException(e.status, f"GitHub said {e.status}: {e.message}") from None
        except Unavailable:
            status, outcome = 503, "unavailable"
            raise HTTPException(503, "GitHub did not answer: try again") from None
        except IntegrationError:
            status, outcome = 502, "unavailable"
            raise HTTPException(502, "GitHub's answer could not be used") from None
        finally:
            caller.for_repo(repo).note(EDIT_PR, "service-total", request_id=request_id, status=status, outcome=outcome,
                                       latency_ms=int((time.monotonic() - started) * 1000))

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
              responses=problems)
    def propose(proposal: Proposal, request: HttpRequest, response: Response,
                credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
        """Open a pull request with one commit, as the person whose token this is."""
        served(proposal.repo, edit_pr_limit)
        token = credentials.credentials
        with githubs_answer(proposal.repo, request, response) as (for_repo, request_id):
            proposer = OneCommitProposer(for_repo, lambda: token, request_id)
            return proposer.propose(repo=proposal.repo, branch=proposal.branch, title=proposal.title,
                                    changes=proposal.changes, body=proposal.body, base=proposal.base,
                                    message=proposal.message, parent=proposal.parent, expect=proposal.expect,
                                    auto_merge=proposal.autoMerge)

    @app.get("/v1/github/edit-pr/snapshot", tags=["github.edit-pr"], operation_id="snapshot", response_model=Snapshot,
             responses=problems)
    def snapshot(request: HttpRequest, response: Response, repo: str = Query(pattern=REPO_PATTERN, description="owner/name"),
                 branch: str | None = Query(default=None, description="By default, the repository's default branch."),
                 credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
        """The newest commit on a branch, every file's path at it, and whether this token may write."""
        served(repo, edit_pr_read_limit)
        token = credentials.credentials
        with githubs_answer(repo, request, response) as (for_repo, request_id):
            return RepositoryReader(for_repo, lambda: token, request_id).snapshot(repo, branch)

    @app.post("/v1/github/edit-pr/files", tags=["github.edit-pr"], operation_id="readFiles", response_model=Files,
              responses=problems)
    def read_files(asked: FilesRequest, request: HttpRequest, response: Response,
                   credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
        """The text of many files at one commit, in one call."""
        served(asked.repo, edit_pr_read_limit)
        token = credentials.credentials
        with githubs_answer(asked.repo, request, response) as (for_repo, request_id):
            return {"files": RepositoryReader(for_repo, lambda: token, request_id).files(asked.repo, asked.commit, asked.paths)}

    @app.post("/v1/github/edit-pr/timings", tags=["github.edit-pr"], operation_id="reportTimings", status_code=202,
              responses=problems)
    def report_timings(reported: Timings) -> dict:
        """How long requests took the app. It carries only ids and numbers, and needs no
        token, so a browser can send it as a page closes."""
        served(reported.repo, edit_pr_read_limit)
        for timing in reported.timings:
            caller.for_repo(reported.repo).note(EDIT_PR, "client-total", request_id=timing.request_id, status=None,
                                                outcome="ok", latency_ms=timing.total_ms)
        return {"accepted": len(reported.timings)}

    return app
