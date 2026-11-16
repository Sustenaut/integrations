"""The ci-jobs poller: each finished GitHub Actions job of the repositories on the release
board, as one row of platform.ci_jobs, written once.

Every ten minutes (k8s/ci-jobs.yaml) it lists each repository's runs created in the last
LOOKBACK_DAYS, day by day (GitHub answers at most 1,000 runs to one query), and reads the
jobs of each run it hasn't finished with: a run is finished with once it has completed and
every job of it is written, until it changes again (a re-run). A job is written once it has
completed. GitHub's list of runs filters only by when a run was created, so the look back
is what catches a re-run of a run some days old.

Its state is a head in the ci-jobs tenant: the job ids it has written and the runs it has
finished with, within the look back. It saves it after writing the rows, so a pod stopped
in between writes those rows again, with the same job_id: a reader keeps one row per job_id.
The head moves with If-Match, so of two runs at once only one saves.

It reads as the GitHub App "Sustenaut release reader" (actions and contents: read),
whose id, key and installation id are files in a mounted Secret, and writes to storage as
its pod's service account (k8s:integrations/ci-jobs). It prints counts only: the rows name
private repositories.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

from ..avrofile import encode_ci_jobs
from ..calls import Caller, Refused, Request
from . import place

PLUGIN, OPERATION = "github.board-read", "read"
API = "https://api.github.com"
FOLDER = "inbox/ci-jobs/"
HEAD = "written"
LOOKBACK_DAYS = 3
PER_PAGE = 100
RATE_LIMIT_WAITS = 60


def when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHub:
    """GitHub's REST API, as the reader app: one call each, through the caller."""

    def __init__(self, caller: Caller, token: Callable[[], str], *, sleep: Callable[[float], None] = time.sleep):
        self.caller, self.token, self.sleep = caller, token, sleep

    def get(self, path: str) -> Any:
        """One answer. A rate limit is waited out, a minute at a time, up to an hour: the
        installation's 5,000 calls an hour are the backfill's pace, not a failure."""
        for _ in range(RATE_LIMIT_WAITS + 1):
            response = self.caller.call(PLUGIN, OPERATION, Request("GET", f"{API}{path}", {
                "Authorization": f"Bearer {self.token()}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}))
            if response.ok:
                return response.json()
            if response.status not in (403, 429) or b"rate limit" not in response.body.lower():
                break
            self.sleep(60)
        raise Refused(PLUGIN, OPERATION, f"HTTP {response.status} for {path.split('?')[0]}")

    def pages(self, path: str, key: str) -> list[dict]:
        found: list[dict] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            answer = self.get(f"{path}{separator}per_page={PER_PAGE}&page={page}")
            items = answer.get(key) or []
            found += items
            if len(items) < PER_PAGE:
                return found
            page += 1

    def file(self, repository: str, path: str) -> bytes:
        answer = self.get(f"/repos/{repository}/contents/{quote(path)}")
        return base64.b64decode(answer["content"])


def board(state: dict) -> list[str]:
    """The repositories release-state's state.json puts on the board, OWNER/NAME."""
    return [f"{state['owner']}/{name}" for name, roles in sorted(state["repos"].items()) if roles.get("board")]


@dataclass
class State:
    """Which jobs are written, and which runs are finished with (by when they last changed)."""

    written: dict[str, str] = field(default_factory=dict)
    finished: dict[str, str] = field(default_factory=dict)

    @classmethod
    def decode(cls, data: bytes) -> State:
        given = json.loads(data)
        return cls(dict(given.get("written", {})), dict(given.get("finished", {})))

    def encode(self) -> bytes:
        return json.dumps({"written": self.written, "finished": self.finished}, sort_keys=True).encode()

    def forget_before(self, start: datetime) -> None:
        """Only what the next look back can see again is worth keeping."""
        self.written = {k: v for k, v in self.written.items() if when(v) >= start}
        self.finished = {k: v for k, v in self.finished.items() if when(v.split("|")[0]) >= start}


def row(repository: str, run: dict, job: dict, observed: datetime) -> dict:
    created, started, completed = when(job["created_at"]), when(job.get("started_at")), when(job["completed_at"])
    picked = bool(job.get("runner_name")) and started is not None
    return {
        "observed_at": observed, "job_id": int(job["id"]), "run_id": int(job["run_id"]),
        "run_attempt": int(job.get("run_attempt") or run.get("run_attempt") or 1), "repository": repository,
        "workflow": job.get("workflow_name") or run.get("name") or "", "job_name": job.get("name") or "",
        "event": run.get("event") or "", "head_branch": job.get("head_branch") or run.get("head_branch"),
        "labels": list(job.get("labels") or []), "runner_name": job.get("runner_name") or None,
        "runner_group": job.get("runner_group_name") or None, "created_at": created,
        "started_at": started if picked else None, "completed_at": completed,
        "wait_seconds": max(0, int((started - created).total_seconds())) if picked else None,
        "run_seconds": max(0, int((completed - started).total_seconds())) if picked else None,
        "conclusion": job.get("conclusion"),
    }


def days(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """[start, end) in whole UTC days, the first and last cut to fit."""
    out = []
    day = start
    while day < end:
        following = min(end, (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        out.append((day, following))
        day = following
    return out


@dataclass
class Poller:
    github: GitHub
    store: Any  # a Storage of the ci-jobs tenant: get_head, upload_file, move_head
    buffer: Any  # a Storage of the ci-usage tenant: put_buffer
    repositories: list[str]
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    report: Callable[[str], None] = print

    def load(self) -> tuple[State, str | None]:
        from ..clients.storage import NotFound  # noqa: PLC0415

        try:
            data, etag = self.store.get_head(HEAD)
        except NotFound:
            return State(), None
        return State.decode(data), etag

    def save(self, state: State, read: str | None) -> str:
        """Saves it, and gives the head's new hash, for the next save's If-Match."""
        stored = self.store.upload_file(state.encode(), name="written.json")
        self.store.move_head(HEAD, stored["id"], if_match=read)
        return stored["id"]

    def poll(self, since_days: int = LOOKBACK_DAYS) -> int:
        """One pass: the rows of every job finished since it last looked, written as one file
        a day of runs. Returns how many rows it wrote."""
        now = self.clock().replace(microsecond=0)
        start = (now - timedelta(days=since_days)).replace(microsecond=0)
        state, read = self.load()
        saved = state.encode()
        written = 0
        for begin, end in days(start, now + timedelta(seconds=1)):
            rows = [r for repository in self.repositories for r in self._rows(repository, begin, end, state, now)]
            if not rows:
                continue
            data = encode_ci_jobs(rows)
            self.buffer.put_buffer(place(FOLDER, f"ci-jobs-{begin:%Y%m%d}", now, data), data)
            for r in rows:
                state.written[str(r["job_id"])] = stamp(r["completed_at"])
            written += len(rows)
            # Saved day by day, so a backfill stopped partway goes on where it was.
            read, saved = self.save(state, read), state.encode()
        state.forget_before(now - timedelta(days=LOOKBACK_DAYS + 1))
        if state.encode() != saved:
            self.save(state, read)
        self.report(f"{written} jobs written from {len(self.repositories)} repositories")
        return written

    def _rows(self, repository: str, begin: datetime, end: datetime, state: State, now: datetime) -> list[dict]:
        runs = self.github.pages(f"/repos/{repository}/actions/runs?created={stamp(begin)}..{stamp(end)}",
                                 "workflow_runs")
        rows = []
        for run in runs:
            key, changed = str(run["id"]), f"{run.get('created_at')}|{run.get('updated_at')}"
            if state.finished.get(key) == changed:
                continue
            jobs = self.github.pages(f"/repos/{repository}/actions/runs/{run['id']}/jobs?filter=all", "jobs")
            done = [j for j in jobs if j.get("status") == "completed" and j.get("completed_at")]
            rows += [row(repository, run, j, now) for j in done if str(j["id"]) not in state.written]
            if run.get("status") == "completed" and len(done) == len(jobs):
                state.finished[key] = changed
        return rows
