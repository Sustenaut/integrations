"""Find every signature of a registered provider in a repository, and compare
what it finds with the repository's integrations.json.

What is read: code and configuration, not prose. A comment line is skipped, so
a link in a licence header is not a call. A signature several plugins share
is declared when any plugin holding it covers the file.

Signatures only find what the registry already knows, so three checks catch a
provider it does not: a URL in code on a host no plugin names, a secret a
workflow reads that no plugin names, and a Terraform provider no plugin names.
Each must be registered, or listed in integrations.json with a reason.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .consumer import FILE, Consumer, glob_regex
from .registry import Plugin

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "target", "__pycache__", ".terraform",
             ".pytest_cache", ".hypothesis", "coverage", ".integrations"}
CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".rs", ".java",
                 ".sh", ".bash", ".tf", ".yml", ".yaml", ".mk"}
CODE_NAMES = {"Makefile", "Dockerfile"}
CONFIG_SUFFIXES = {".json", ".toml", ".ini", ".cfg", ".env", ".tfvars", ".html", ".htm",
                   ".ftl", ".njk", ".liquid", ".properties", ".example"}
LOCKS = {"package-lock.json", "Cargo.lock", ".terraform.lock.hcl", "uv.lock", "yarn.lock",
         "pnpm-lock.yaml"}
MAX_BYTES = 2_000_000
MAX_LINE = 20_000
COMMENT = re.compile(r"\s*(?:#(?!!)|//|/\*|\*|<!--|;|--\s)")
URL = re.compile(r"https?://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})(?![\w-])")
WORKFLOW_SECRET = re.compile(r"\bsecrets\.([A-Z][A-Z0-9_]*)")
TF_SOURCE = re.compile(r'^\s*source\s*=\s*"(?:registry\.terraform\.io/)?([A-Za-z0-9-]+/[A-Za-z0-9-]+)"')
USES = r"\buses:\s*['\"]?"


@dataclass(frozen=True)
class Signature:
    kind: str
    text: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    signature: str
    plugins: tuple[str, ...]


@dataclass
class Report:
    hits: list[Hit]
    undeclared: list[Hit]
    stale: list[str]
    unregistered: list[str]

    @property
    def clean(self) -> bool:
        return not (self.undeclared or self.stale or self.unregistered)


def host_regex(host: str) -> str:
    """A host, or with `*.` any name under it, as a whole name: `x.example`
    is not found in `notx.example` or `x.example.evil`."""
    if host.startswith("*."):
        name = re.escape(host[2:])
        return rf"(?<![\w-])(?:[\w-]+\.)+{name}(?![\w-]|\.[a-z])"
    return rf"(?<![\w.-]){re.escape(host)}(?![\w-]|\.[a-z])"


def command_regex(name: str) -> str:
    """A command on a shell line, or as the first string of an argv list."""
    word = re.escape(name)
    return rf"""(?:(?<![\w./-]){word}(?=\s+[-\w"'$])|["']{word}["']\s*,)"""


def compile_signature(kind: str, text: str) -> Signature:
    regex = {
        "hosts": host_regex,
        "env": lambda t: rf"(?<![A-Za-z0-9_])(?:{t})(?![A-Za-z0-9_])",
        "commands": command_regex,
        "actions": lambda t: USES + re.escape(t),
        "terraform_providers": lambda t: r'source\s*=\s*"(?:registry\.terraform\.io/)?(?i:' + re.escape(t) + ')"',
        "patterns": lambda t: t,
    }[kind](text)
    return Signature(kind, text, re.compile(regex))


def is_scanned(path: Path, name: str) -> bool:
    if name in LOCKS or name == FILE or name.endswith((".min.js", ".lock")):
        return False
    if path.suffix in CODE_SUFFIXES | CONFIG_SUFFIXES or name in CODE_NAMES:
        return True
    return path.suffix == "" and _has_shebang(path)


def _has_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def is_code(path: Path) -> bool:
    return path.suffix in CODE_SUFFIXES or path.name in CODE_NAMES or path.suffix == ""


def walk(root: Path, consumer: Consumer):
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if not consumer.excluded(relative):
                yield path, relative


def host_matches(host: str, names: list[str]) -> bool:
    """Whether `host` is one of `names` or under one of them."""
    return any(host == n or host.endswith("." + n) for n in names)


class Scanner:
    def __init__(self, plugins: dict[str, Plugin], not_providers: dict[str, list[str]]):
        self.plugins = plugins
        self.not_providers = not_providers
        owners: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.files: dict[str, list[str]] = defaultdict(list)
        for plugin in plugins.values():
            for kind, texts in plugin.detect.items():
                for text in texts:
                    if kind == "files":
                        self.files[text].append(plugin.id)
                    else:
                        owners[(kind, text)].append(plugin.id)
        self.signatures = [(compile_signature(k, t), tuple(ids)) for (k, t), ids in owners.items()]
        self.any = re.compile("|".join(f"(?:{s.regex.pattern})" for s, _ in self.signatures))
        self.host_regexes = [s.regex for s, _ in self.signatures if s.kind == "hosts"]
        self.secret_regexes = [s.regex for s, _ in self.signatures if s.kind == "env"]
        self.tf_providers = {s.text for s, _ in self.signatures if s.kind == "terraform_providers"}
        self.file_regexes = {pattern: glob_regex(pattern) for pattern in self.files}

    def registered_host(self, host: str) -> bool:
        return any(r.search(host) for r in self.host_regexes)

    def registered_secret(self, name: str) -> bool:
        return any(r.fullmatch(name) for r in self.secret_regexes)

    def scan_file(self, path: Path, relative: str, consumer: Consumer):
        """Yield the hits in one file and the unregistered things it reaches."""
        try:
            if path.stat().st_size > MAX_BYTES:
                return
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        code = is_code(path)
        yaml = path.suffix in (".yml", ".yaml")
        terraform = path.suffix == ".tf"
        for number, line in enumerate(text.splitlines(), 1):
            if len(line) > MAX_LINE or COMMENT.match(line):
                continue
            if self.any.search(line):
                for signature, ids in self.signatures:
                    if signature.kind == "actions" and not yaml:
                        continue
                    if signature.kind == "terraform_providers" and not terraform:
                        continue
                    if signature.regex.search(line):
                        yield Hit(relative, number, signature.text, ids)
            if code:
                for host in URL.findall(line):
                    if not self._known_host(host.lower(), consumer):
                        yield f"{relative}:{number}: host {host} is not registered"
            if yaml:
                for name in WORKFLOW_SECRET.findall(line):
                    if not self._known_secret(name, consumer):
                        yield f"{relative}:{number}: secret {name} is not registered"
            if terraform and (m := TF_SOURCE.match(line)):
                source = m.group(1).lower()
                if source not in self.tf_providers and source not in self.not_providers["terraform_providers"]:
                    yield f"{relative}:{number}: Terraform provider {source} is not registered"

    def _known_host(self, host: str, consumer: Consumer) -> bool:
        return (self.registered_host(host)
                or host_matches(host, self.not_providers["hosts"])
                or host_matches(host, consumer.own_hosts)
                or host_matches(host, consumer.other_hosts))

    def _known_secret(self, name: str, consumer: Consumer) -> bool:
        return (self.registered_secret(name)
                or name in self.not_providers["secrets"]
                or name in consumer.other_secrets)

    def scan(self, root: Path, consumer: Consumer) -> Report:
        hits, unregistered = [], []
        for path, relative in walk(root, consumer):
            for pattern, regex in self.file_regexes.items():
                if regex.match(relative):
                    hits.append(Hit(relative, 1, pattern, tuple(self.files[pattern])))
            if not is_scanned(path, path.name):
                continue
            for found in self.scan_file(path, relative, consumer):
                (hits if isinstance(found, Hit) else unregistered).append(found)
        return Report(hits, self._undeclared(hits, consumer), self._stale(hits, consumer),
                      sorted(set(unregistered)))

    @staticmethod
    def _undeclared(hits: list[Hit], consumer: Consumer) -> list[Hit]:
        return [hit for hit in hits
                if not any(u.plugin in hit.plugins and u.covers(hit.path) for u in consumer.uses)]

    def _stale(self, hits: list[Hit], consumer: Consumer) -> list[str]:
        stale = []
        for use in consumer.uses:
            if self.plugins[use.plugin].manual:
                continue
            if not use.paths:
                stale.append(f"{use.plugin}: it names no paths, and only a manual plugin may")
            own = [h for h in hits if use.plugin in h.plugins]
            for pattern, regex in zip(use.paths, use.regexes):
                if not any(regex.match(h.path) for h in own):
                    stale.append(f"{use.plugin}: nothing in {pattern} uses it")
        return stale
