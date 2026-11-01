"""What a deployment says, from the environment. Which repositories and sites a
deployment serves is its own, so none is named here. With none named, nothing is
allowed."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _list(name: str) -> frozenset[str]:
    return frozenset(v.strip() for v in os.environ.get(name, "").split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    edit_pr_repos: frozenset[str] = frozenset()
    allowed_origins: frozenset[str] = frozenset()
    edit_pr_per_minute: int = 30
    max_body_bytes: int = 5_000_000
    docs_dir: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(edit_pr_repos=_list("INTEGRATIONS_EDIT_PR_REPOS"), allowed_origins=_list("INTEGRATIONS_ORIGINS"),
                   edit_pr_per_minute=int(os.environ.get("INTEGRATIONS_EDIT_PR_PER_MINUTE", "30")),
                   max_body_bytes=int(os.environ.get("INTEGRATIONS_MAX_BODY_BYTES", "5000000")),
                   docs_dir=os.environ.get("INTEGRATIONS_DOCS_DIR"))
