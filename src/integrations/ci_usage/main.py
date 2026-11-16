"""What the CronJobs run: `integrations ci-usage jobs` and `integrations ci-usage cluster`.
Settings come from the environment, the reader app's id, key and installation id from
files in a mounted Secret, never the command line.

CI_USAGE_STORAGE_URL         storage (https://storage.sustenaut.eu)
CI_USAGE_STORAGE_TOKEN_FILE  the pod's projected token for it
CI_JOBS_APP_DIR              the reader app's app-id, app-key and installation-id files
CI_JOBS_STATE                OWNER/REPO:PATH of the state.json whose board --except-board leaves out
CI_USAGE_CGROUPS             where the node's /sys/fs/cgroup is mounted
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..calls import Caller, StdoutSink

BUFFER_TENANT = "ci-usage"
STATE_TENANT = "ci-jobs"


def _storage(caller: Caller, tenant: str):
    from ..clients.storage import Storage  # noqa: PLC0415

    return Storage(caller, tenant, base_url=os.environ.get("CI_USAGE_STORAGE_URL", "https://storage.sustenaut.eu"),
                   token_file=Path(os.environ.get("CI_USAGE_STORAGE_TOKEN_FILE",
                                                  "/var/run/secrets/storage.sustenaut.eu/token")))


def jobs(since_days: int, except_board: bool = False) -> int:  # pragma: no cover - the pod's wiring, with real keys
    from ..clients.github_app_token import AppToken  # noqa: PLC0415
    from .jobs import GitHub, Poller, board, installed  # noqa: PLC0415

    caller = Caller("integrations", "ci-jobs", sink=StdoutSink())
    app = Path(os.environ.get("CI_JOBS_APP_DIR", "/etc/ci-jobs-app"))

    def read(name: str) -> str:
        return (app / name).read_text(encoding="utf-8").strip()

    github = GitHub(caller, AppToken(caller, read("app-id"), read("app-key"), read("installation-id")))
    repositories = installed(github)
    if except_board:
        # A backfill of the repositories an earlier one, which read the board's alone, missed.
        repository, _, path = os.environ.get("CI_JOBS_STATE", "Sustenaut/release-state:state.json").partition(":")
        on_board = set(board(json.loads(github.file(repository, path))))
        repositories = [r for r in repositories if r not in on_board]
    storage = _storage(caller, BUFFER_TENANT)
    Poller(github, storage.for_tenant(STATE_TENANT), storage, repositories).poll(since_days)
    return 0


def cluster() -> int:  # pragma: no cover - the pod's wiring, in the cluster
    from .cluster import Cgroups, Kube, Sampler  # noqa: PLC0415

    caller = Caller("integrations", "ci-usage", sink=StdoutSink())
    cgroups = Cgroups(Path(os.environ.get("CI_USAGE_CGROUPS", "/host/sys/fs/cgroup")))
    Sampler(Kube(), cgroups, _storage(caller, BUFFER_TENANT)).sample()
    return 0
