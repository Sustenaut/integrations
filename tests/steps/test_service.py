"""The running service, with GitHub as a fake service (providers.py)."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import pytest
from pubtest.core import show
from pubtest.http import asgi
from pubtest.steps.fakes import fake
from pubtest.steps.secrets import produced
from pytest_bdd import given, parsers, scenarios, then, when

from integrations import calls
from integrations.calls import Check, Response
from integrations.clients.github_edit_pr import OneCommitProposer
from integrations.service import main
from integrations.service.app import RateLimit, create_app
from integrations.service.settings import Settings
from integrations.service.spec import spec_text

from .providers import providers, records

scenarios("service.feature")

GITHUB = "api.github.com"
AT = "/repos/o/r"

# What GitHub answers while one commit becomes a pull request.
PROPOSAL = {
    f"GET {AT}": (200, {"default_branch": "main"}),
    f"GET {AT}/git/ref/heads/main": (200, {"object": {"sha": "start"}}),
    f"GET {AT}/git/commits/start": (200, {"tree": {"sha": "base-tree"}}),
    f"POST {AT}/git/trees": (201, {"sha": "next-tree"}),
    f"POST {AT}/git/commits": (201, {"sha": "commit"}),
    f"POST {AT}/git/refs": (201, {}),
    f"POST {AT}/pulls": (201, {"html_url": "https://github.com/o/r/pull/7", "number": 7, "node_id": "N"}),
}

# What GitHub answers about a repository, and its files at a commit.
READ = {
    f"GET {AT}": (200, {"default_branch": "main", "permissions": {"push": True}}),
    f"GET {AT}/git/ref/heads/main": (200, {"object": {"sha": "tip"}}),
    f"GET {AT}/git/trees/tip?recursive=1": (200, {"tree": [
        {"path": "a.txt", "type": "blob"}, {"path": "dir", "type": "tree"}, {"path": "dir/b.txt", "type": "blob"}]}),
    f"GET {AT}/contents/a.txt?ref=tip": (200, "text of a"),
    f"GET {AT}/contents/dir/b%C3%A4.txt?ref=tip": (200, "text of bä"),
    f"GET {AT}/contents/gone.txt?ref=tip": (404, {}),
}


class Silencing:
    """The fake providers, but for the requests a Given says get no answer."""

    def __init__(self, ctx):
        self.ctx = ctx

    def send(self, request, timeout):
        path = request.url.split(GITHUB, 1)[-1]
        if f"{request.method} {path}" in getattr(self.ctx, "unanswered", set()):
            return Response(None, error="no answer")
        return providers(self.ctx).send(request, timeout)


def settings(ctx) -> dict:
    if getattr(ctx, "settings", None) is None:
        ctx.settings = {}
    return ctx.settings


def app(ctx):
    """The service, made at its first request from what the Givens said."""
    if getattr(ctx, "app", None) is None:
        made = Settings.from_env() if getattr(ctx, "from_env", False) else Settings(**settings(ctx))
        ctx.app = create_app(made, transport=Silencing(ctx),
                             sink=None if getattr(ctx, "stdout_sink", False) else records(ctx),
                             checks=getattr(ctx, "checks", list))
    return ctx.app


def connected(service):
    """The service as a server sees it: once the request's body is read, the
    client stays until the answer is sent, rather than leaving at once (which
    pubtest's asgi() says, so Starlette drops a streamed answer's body)."""

    async def wrapped(scope, receive, send):
        given = []

        async def stays():
            if not given:
                given.append(True)
                return await receive()
            await asyncio.Event().wait()

        await service(scope, stays, send)

    return wrapped


@pytest.fixture
def http_client(ctx, caplog, capsys):
    """pubtest's asgi(), with the Content-Length an HTTP client sends, and every
    answer kept for the secret's check. capsys is asked for here so what the
    service prints from the first request on is there for a Then."""
    caplog.set_level(logging.DEBUG)
    ctx.answers = getattr(ctx, "answers", [])

    def send(method, path, headers, body):
        if body is not None:
            headers = {**headers, "Content-Length": str(len(body))}
        answer = asgi(connected(app(ctx)))(method, path, headers, body)
        ctx.answers.append(answer)
        return answer

    return send


def answer_the_rest(ctx, monkeypatch, answers: dict) -> None:
    github = fake(ctx, monkeypatch, GITHUB)
    for request, (status, body) in answers.items():
        if request not in github.answers:
            github.answer(request, status, body if isinstance(body, str) else json.dumps(body))


# --- Givens -------------------------------------------------------------------

@given(parsers.parse('the service serves "{repos}" to "{origins}"'))
def serves(ctx, repos, origins):
    settings(ctx).update(edit_pr_repos=frozenset(repos.split(",")), allowed_origins=frozenset(origins.split(",")))


@given(parsers.re(r'the service serves "(?P<repos>[^"]+)"'))
def serves_repos(ctx, repos):
    settings(ctx).update(edit_pr_repos=frozenset(repos.split(",")))


@given("the service takes its settings from the environment")
def from_env(ctx):
    ctx.from_env = True


@given(parsers.parse("the service allows {count:d} {what} a minute"))
def allows(ctx, count, what):
    key = {"proposals": "edit_pr_per_minute", "reads": "edit_pr_reads_per_minute",
           "timings": "edit_pr_timings_per_minute"}[what]
    settings(ctx)[key] = count


@given(parsers.parse("the service takes bodies of at most {count:d} bytes"))
def max_body(ctx, count):
    settings(ctx)["max_body_bytes"] = count


@given(parsers.parse('the service serves its docs from "{directory}"'))
def docs_from(ctx, directory):
    settings(ctx)["docs_dir"] = directory


@given("the service writes its call records to stdout")
def stdout_sink(ctx):
    ctx.stdout_sink = True


@given(parsers.parse('the service\'s check of "{plugin}" fails, saying "{detail}"'))
def failing_check(ctx, plugin, detail):
    ctx.checks = lambda: [Check(plugin, False, detail)]


@given("GitHub answers the rest of a proposal")
def rest_of_a_proposal(ctx, monkeypatch):
    answer_the_rest(ctx, monkeypatch, PROPOSAL)


@given("GitHub answers the rest of a read")
def rest_of_a_read(ctx, monkeypatch):
    answer_the_rest(ctx, monkeypatch, READ)


@given(parsers.parse('GitHub does not answer "{request}"'))
def github_does_not_answer(ctx, request):
    ctx.unanswered = getattr(ctx, "unanswered", set()) | {request}


@given("GitHub's answer to a proposal cannot be used")
def unusable(monkeypatch):
    def fails(self, **kwargs):
        raise calls.IntegrationError("github.edit-pr", "open-pr", "odd")

    monkeypatch.setattr(OneCommitProposer, "propose", fails)


def earlier(ctx, http_client, request: str, status: int, body: bytes | None) -> None:
    method, path = request.split(" ", 1)
    headers = dict(getattr(ctx, "headers", {}))
    if body is not None:
        headers["Content-Type"] = "application/json"
    answer = http_client(method, path, headers, body)
    assert answer.status == status, f"{request} was answered {answer.status}: {answer.text}"


@given(parsers.parse('"{request}" was answered {status:d}'))
def was_answered(ctx, http_client, request, status):
    earlier(ctx, http_client, request, status, None)


@given(parsers.parse('"{request}" was answered {status:d} to:'))
def was_answered_to(ctx, http_client, request, status, docstring):
    earlier(ctx, http_client, request, status, docstring.encode())


# --- Whens --------------------------------------------------------------------

@when("the settings are read from the environment")
def read_settings(ctx):
    def read():
        made = Settings.from_env()
        return {k: sorted(v) if isinstance(v, frozenset) else v for k, v in vars(made).items()}

    ctx.attempt(read)


@when(parsers.parse('a limit of {limit:d} a minute is asked at the seconds "{seconds}"'))
def limit_asked(ctx, limit, seconds):
    def ask():
        now = [0.0]
        made = RateLimit(limit, 60, lambda: now[0])
        allowed = []
        for second in seconds.split(","):
            now[0] = float(second)
            allowed.append(made.allow())
        return allowed

    ctx.attempt(ask)


@when("the service checks what it needs")
def service_checks(ctx):
    ctx.attempt(main.checks)


@when("the service's spec is made")
def spec_made(ctx):
    ctx.attempt(spec_text)


@when("the service makes its sink")
def makes_sink(ctx, monkeypatch):
    import integrations.storage_sink as storage_sink
    monkeypatch.setattr(storage_sink.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(storage_sink.atexit, "register", lambda f: None)

    def made():
        sink = main.sink()
        found = {"kind": type(sink).__name__}
        if isinstance(sink, storage_sink.StorageSink):
            found.update(base=sink.base, sender=sink.sender, token=sink.token(), datasets=sink.datasets)
        return found

    ctx.attempt(made)


# --- Thens --------------------------------------------------------------------

@then(parsers.parse('the "{name}" service was asked, in turn:'))
def asked_in_turn(ctx, name, docstring):
    """Exactly these requests, "METHOD PATH" a line, in the order they came."""
    asked = [f"{r['method']} {r['path']}" for r in ctx.fakes[name].requests]
    want = [line.strip() for line in docstring.splitlines() if line.strip()]
    assert asked == want, f"{name} was asked:\n" + "\n".join(asked)


@then(parsers.parse('every call recorded has the id the answer\'s "{header}" gives'))
def calls_have_answer_id(ctx, header):
    given = ctx.answer.header(header)
    kept = {r.request_id for r in records(ctx).records}
    assert kept == {given}, f"the answer's {header} is {given!r}; the records' ids are {kept}"


@then(parsers.parse('the answer\'s "{header}" is a new id'))
def answer_new_id(ctx, header):
    got = ctx.answer.header(header)
    assert got is not None and re.fullmatch(r"[0-9a-f]{32}", got), f"the answer's {header} is {got!r}"


@then(parsers.parse('every call recorded has "{key}" at least {least:d}'))
def calls_at_least(ctx, key, least):
    kept = records(ctx).records
    assert kept and all(getattr(r, key) >= least for r in kept), f"{key}: {[getattr(r, key) for r in kept]}"


@then(parsers.parse('no secret appears in what it produced but what it sent "{name}"'))
def no_secret_but_sent(ctx, caplog, capsys, name):
    """pubtest's check, less the requests to `name`, which the secret is for, and
    with every answer of the scenario, the log and the call records."""
    assert ctx.secrets, "no secret was given"
    out = capsys.readouterr()
    where = {k: v for k, v in produced(ctx).items() if k != f"what it sent {name}"}
    where["the answers"] = "".join(json.dumps(a.headers) + a.text for a in getattr(ctx, "answers", []))
    where["stdout and stderr"] = out.out + out.err
    where["the log"] = caplog.text
    where["the calls recorded"] = json.dumps([r.__dict__ for r in records(ctx).records])
    leaks = [f"{s!r} in {w}" for w, text in where.items() for s in ctx.secrets if s in text]
    assert not leaks, "a secret appears: " + "; ".join(leaks)


@then("the service keeps no secret")
def keeps_no_secret(ctx):
    state = app(ctx).state
    assert not hasattr(state, "token")
    kept = repr(vars(state))
    assert not any(s in kept for s in ctx.secrets), kept


@then(parsers.parse('the service printed "{text}"'))
def service_printed(capsys, text):
    out = capsys.readouterr().out
    assert text in out, f"the service printed {out!r}"
    print(out, end="")  # for a later step that reads it too


@then(parsers.parse('the result is what the file "{path}" holds'))
def result_is_file(ctx, path):
    assert ctx.outcome is not None and ctx.refused is None, "the action was refused"
    wanted = (ctx.directory / path).read_text(encoding="utf-8")
    assert ctx.result == wanted, f"the result is not what {path} holds:\n{show(ctx.result)[:2000]}"


@given(parsers.parse('a file "{path}" that asks "{repo}" for {count:d} files'))
def asks_for_files(ctx, path, repo, count):
    (ctx.directory / path).write_text(json.dumps({"repo": repo, "commit": "tip", "paths": ["a"] * count}))


@given(parsers.parse('a file "{path}" that reports {count:d} timings for "{repo}"'))
def reports_timings(ctx, path, count, repo):
    (ctx.directory / path).write_text(json.dumps({"repo": repo, "timings": [{"request_id": "a", "total_ms": 1}] * count}))
