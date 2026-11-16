"""The integrations command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from . import __version__, registry
from .calls import Caller, IntegrationError
from .clients.slack import SlackPost
from .consumer import FILE, BadConsumer, Consumer, read
from .scan import Hit, Scanner


def print_report(report) -> None:
    for hit in report.undeclared:
        print(f"{hit.path}:{hit.line}: undeclared: {hit.signature} ({', '.join(hit.plugins)})")
    for line in report.stale:
        print(f"{FILE}: stale: {line}")
    for line in report.unregistered:
        print(line)
    for line in report.advisory_stale:
        warn(f"{FILE}: advisory: stale: {line}")


def warn(line: str) -> None:
    """A finding that does not fail the scan: in a passing job's log nobody
    reads it, so in CI it is also an annotation."""
    print(line)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning file={FILE}::{line}")


def suggest(scanner: Scanner, root: Path) -> dict:
    """A first integrations.json from what is there. A signature several
    plugins share goes to the first of them and is named on stderr, for a
    person to place."""
    empty = Consumer([], [], [], [], [])
    report = scanner.scan(root, empty)
    paths: dict[str, set[str]] = defaultdict(set)
    for hit in report.hits:
        if len(hit.plugins) > 1:
            print(f"choose: {hit.path}:{hit.line} {hit.signature}: {', '.join(hit.plugins)}", file=sys.stderr)
        paths[hit.plugins[0]].add(hit.path)
    for line in report.unregistered:
        print(f"place: {line}", file=sys.stderr)
    return {"uses": [{"plugin": p, "paths": sorted(paths[p])} for p in sorted(paths)]}


def scan(path: str, draft: bool) -> int:
    root = Path(path)
    if not root.is_dir():
        print(f"integrations: {path} is not a directory", file=sys.stderr)
        return 2
    plugins = registry.plugins()
    scanner = Scanner(plugins, registry.not_providers())
    if draft:
        print(json.dumps(suggest(scanner, root), indent=2))
        return 0
    try:
        consumer = read(root, set(plugins))
    except BadConsumer as e:
        print(f"integrations: {root / FILE}: {e}", file=sys.stderr)
        return 2
    report = scanner.scan(root, consumer)
    print_report(report)
    if not report.clean:
        print(f"{len(report.undeclared)} undeclared, {len(report.stale)} stale, "
              f"{len(report.unregistered)} unregistered", file=sys.stderr)
        return 1
    advisory = len(report.advisory_stale)
    print(f"{len(consumer.uses)} uses declared, {len(report.hits)} signature hits, all accounted for"
          + (f", with {advisory} advisory findings" if advisory else ""))
    return 0


def slack_post(channel: str, text: str) -> int:
    """For a shell script: the token is in SLACK_TOKEN, never on the command line."""
    token = os.environ.get("SLACK_TOKEN")
    if not token:
        print("integrations: SLACK_TOKEN is not set", file=sys.stderr)
        return 2
    caller = Caller(os.environ.get("GITHUB_REPOSITORY", "unknown"), "cli")
    try:
        SlackPost(caller, lambda: token).post(channel, text)
    except IntegrationError as e:
        print(f"integrations: {e}", file=sys.stderr)
        return 1
    return 0


def ci_usage(action: str, since_days: int | None, except_board: bool = False) -> int:  # pragma: no cover - the pods' wiring
    from .ci_usage import main as ci  # noqa: PLC0415
    from .ci_usage.jobs import LOOKBACK_DAYS  # noqa: PLC0415

    if action == "cluster":
        return ci.cluster()
    days = since_days or LOOKBACK_DAYS
    if not 1 <= days <= 90:
        print("integrations: --since-days is 1 to 90", file=sys.stderr)
        return 2
    return ci.jobs(days, except_board)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integrations", description=__doc__)
    parser.add_argument("--version", action="version", version=f"integrations {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("scan", help="compare a repository's calls to outside services with its integrations.json")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--suggest", action="store_true", help="print a first integrations.json instead of checking")
    p = commands.add_parser("slack", help="post to Slack from a script")
    actions = p.add_subparsers(dest="action", required=True)
    p = actions.add_parser("post", help="post a message as the bot in SLACK_TOKEN")
    p.add_argument("--channel", required=True, help="a channel's or a person's id")
    p.add_argument("--text", required=True)
    p = commands.add_parser("ci-usage", help="collect CI's wait times and compute load into storage's ci-usage buffer")
    actions = p.add_subparsers(dest="action", required=True)
    p = actions.add_parser("jobs", help="write each finished GitHub Actions job of the installation's repositories, once")
    p.add_argument("--since-days", type=int, default=None,
                   help="look back this many days instead of the usual few, for a backfill (at most 90: GitHub keeps no more)")
    p.add_argument("--except-board", action="store_true",
                   help="leave out the release board's repositories (release-state's state.json): a backfill of the others")
    actions.add_parser("cluster", help="write one sample of the k3s server's usage")
    args = parser.parse_args(argv)
    if args.command == "slack":
        return slack_post(args.channel, args.text)
    if args.command == "ci-usage":
        return ci_usage(args.action, args.since_days if args.action == "jobs" else None,
                        args.except_board if args.action == "jobs" else False)
    return scan(args.path, args.suggest)
