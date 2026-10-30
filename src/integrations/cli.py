"""The integrations command."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import __version__, registry
from .consumer import FILE, BadConsumer, Consumer, read
from .scan import Hit, Scanner


def print_report(report) -> None:
    for hit in report.undeclared:
        print(f"{hit.path}:{hit.line}: undeclared: {hit.signature} ({', '.join(hit.plugins)})")
    for line in report.stale:
        print(f"{FILE}: stale: {line}")
    for line in report.unregistered:
        print(line)


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
    print(f"{len(consumer.uses)} uses declared, {len(report.hits)} signature hits, all accounted for")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integrations", description=__doc__)
    parser.add_argument("--version", action="version", version=f"integrations {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("scan", help="compare a repository's calls to outside services with its integrations.json")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--suggest", action="store_true", help="print a first integrations.json instead of checking")
    args = parser.parse_args(argv)
    return scan(args.path, args.suggest)
