"""The wrapper every client call goes through, and the first clients."""

from __future__ import annotations

import base64
import io
import json
import time
import tomllib
import urllib.error
from dataclasses import asdict
from pathlib import Path

import jsonschema
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations import __version__, registry
from integrations.calls import Caller, Check, JsonlSink, Network, NullSink, Request, Response, health, read_modes
from integrations.clients.github_app_token import AppToken, FakeAppToken
from integrations.clients.github_edit_pr import Change, EditPullRequests, FakeEditPullRequests, next_branch
from integrations.transports import Cassette, Recorder
from pytest_bdd import given, parsers, scenarios, then, when

from .providers import providers, records

scenarios("calls.feature")

RECORD_SCHEMA = registry.load(registry.SCHEMAS / "call-record.schema.json")


# --- the caller -----------------------------------------------------------------

class Dropping:
    """The fake providers, but for the tries a Given says get no answer."""

    def __init__(self, ctx):
        self.ctx, self.tries = ctx, 0

    def send(self, request, timeout):
        self.tries += 1
        if self.tries in getattr(self.ctx, "dropped", set()):
            return Response(None, error="no answer")
        return providers(self.ctx).send(request, timeout)


def made_caller(ctx, repo: str | None = None) -> Caller:
    """The scenario's caller, as its Givens set it up: new ids are id-1, and the
    jitter's random source gives 1.0, the top of its range, so the waits are 2**n."""
    ctx.naps = getattr(ctx, "naps", [])
    clock = getattr(ctx, "clock", None)

    def sleep(seconds):
        ctx.naps.append(seconds)
        if clock is not None:
            ctx.now += seconds

    extra = {"clock": clock} if clock is not None else {}
    return Caller(repo or ctx.repo, ctx.app, sink=getattr(ctx, "call_sink", None) or records(ctx),
                  transport=getattr(ctx, "transport", None) or Dropping(ctx), modes=getattr(ctx, "modes", None),
                  sleep=sleep, new_id=lambda: "id-1", random=lambda: getattr(ctx, "random", 1.0), **extra)


@given(parsers.parse('a caller for "{repo}" in the app "{app}"'))
def a_caller(ctx, repo, app):
    ctx.repo, ctx.app = repo, app
    ctx.headers_sent, ctx.body = {}, None


@given(parsers.parse("the caller's random source gives {value:g}"))
def random_source(ctx, value):
    ctx.random = value


@given("the caller takes its modes from the repository")
def modes_from_repository(ctx):
    ctx.modes = read_modes(ctx.directory)


@given("the caller keeps its records the default way")
def default_sink(ctx):
    ctx.call_sink = NullSink()


@given(parsers.parse('the caller keeps its records in the file "{path}"'))
def file_sink(ctx, path):
    ctx.call_sink = JsonlSink(ctx.directory / path)


@given(parsers.parse('the caller keeps its records in a sink that fails, saying "{text}"'))
def failing_sink(ctx, text):
    class Broken:
        def write(self, record):
            raise OSError(text)

    ctx.call_sink = Broken()


@given(parsers.parse("try {number:d} of the call has no answer"))
def try_has_no_answer(ctx, number):
    ctx.dropped = getattr(ctx, "dropped", set()) | {number}


@given("a provider that never answers, each try taking all the time it is given")
def silent_provider(ctx):
    ctx.now, ctx.timeouts = 0.0, []
    ctx.clock = lambda: ctx.now

    class Silent:
        def send(self, request, timeout):
            ctx.timeouts.append(timeout)
            ctx.now += timeout
            return Response(None, error="no answer: timed out")

    ctx.transport = Silent()


@given(parsers.parse("a provider that answers {status:d} after {seconds:g} seconds, asking for a retry after {wait}"))
def slow_provider(ctx, status, seconds, wait):
    ctx.now, ctx.timeouts = 0.0, []
    ctx.clock = lambda: ctx.now

    class Slow:
        def send(self, request, timeout):
            ctx.timeouts.append(timeout)
            ctx.now += seconds
            return Response(status, b"{}", retry_after=wait)

    ctx.transport = Slow()


@given(parsers.parse('the call carries the header "{name}" set to "{value}"'))
def call_header(ctx, name, value):
    ctx.headers_sent = {**getattr(ctx, "headers_sent", {}), name: value}


@given(parsers.parse('the call\'s body is "{body}"'))
def call_body(ctx, body):
    ctx.body = body.encode()


@given("the call says it is safe to repeat")
def call_repeatable(ctx):
    ctx.repeatable = True


@given(parsers.parse('the call gives the request id "{request_id}"'))
def call_request_id(ctx, request_id):
    ctx.request_id = request_id


@given("the call takes any JSON body as a refusal")
def call_refused_rule(ctx):
    ctx.refused_rule = lambda response: bool(response.json())


def request(ctx, asked: str) -> Request:
    method, url = asked.split(" ", 1)
    return Request(method, url, getattr(ctx, "headers_sent", {}), getattr(ctx, "body", None),
                   getattr(ctx, "repeatable", False))


@when(parsers.parse('the caller calls "{plugin}" "{operation}" with "{asked}"'))
def the_caller_calls(ctx, plugin, operation, asked):
    made = getattr(ctx, "caller", None) or made_caller(ctx)
    ctx.caller = made
    ctx.attempt(lambda: made.call(plugin, operation, request(ctx, asked), request_id=getattr(ctx, "request_id", None),
                                  refused=getattr(ctx, "refused_rule", None)))


@when(parsers.parse('the caller for "{repo}" calls "{plugin}" "{operation}" with "{asked}"'))
def the_caller_for_calls(ctx, repo, plugin, operation, asked):
    made = getattr(ctx, "caller", None) or made_caller(ctx)
    ctx.caller = made
    ctx.attempt(lambda: made.for_repo(repo).call(plugin, operation, request(ctx, asked)))


@when(parsers.parse('the caller notes "{operation}" of "{plugin}" taking {ms:d} ms with the status {status:d}'))
def the_caller_notes(ctx, operation, plugin, ms, status):
    made = made_caller(ctx)
    ctx.attempt(lambda: made.note(plugin, operation, request_id="r", latency_ms=ms, status=status, outcome="ok"))


@then(parsers.parse('the caller\'s sleeps were "{naps}"'))
def sleeps_were(ctx, naps):
    assert ctx.naps == json.loads(naps), f"the caller slept {ctx.naps}"


@then(parsers.parse('the tries were given "{timeouts}" seconds'))
def tries_given(ctx, timeouts):
    assert ctx.timeouts == json.loads(timeouts), f"the tries were given {ctx.timeouts}"


@then(parsers.parse('"{seconds}" seconds passed'))
def seconds_passed(ctx, seconds):
    assert ctx.now == json.loads(seconds), f"{ctx.now} seconds passed"


@then(parsers.parse('no call record mentions "{text}"'))
def no_record_mentions(ctx, text):
    kept = [json.dumps(asdict(r)) for r in records(ctx).records]
    assert kept, "no call was recorded"
    assert not [r for r in kept if text in r], f"a record mentions {text}: {kept}"


@then(parsers.parse('the file "{path}" holds {count:d} call records, each meeting its schema'))
def file_holds_records(ctx, path, count):
    lines = (ctx.directory / path).read_text().splitlines()
    assert len(lines) == count, f"{path} holds {len(lines)} lines"
    for line in lines:
        jsonschema.validate(json.loads(line), RECORD_SCHEMA)


# --- the fake providers' requests ---------------------------------------------

def _requests(ctx, name):
    fakes = getattr(ctx, "fakes", {})
    assert name in fakes, f"there is no fake {name} service"
    asked = fakes[name].requests
    assert asked, f"{name} was asked nothing"
    return asked


@then(parsers.parse('the "{name}" service was asked nothing but "{method}"'))
def asked_only(ctx, name, method):
    asked = [f"{r['method']} {r['path']}" for r in _requests(ctx, name)]
    assert all(a.startswith(f"{method} ") for a in asked), f"{name} was asked {asked}"


@then(parsers.parse('the token sent to the "{name}" service has the claim "{claim}" set to "{value}"'))
def token_claim(ctx, name, claim, value):
    tokens = [{k.lower(): v for k, v in r["headers"].items()}.get("authorization", "") for r in _requests(ctx, name)]
    claims = []
    for token in tokens:
        _head, part, _signature = token.removeprefix("Bearer ").split(".")
        claims.append(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))))
    assert any(str(c.get(claim)) == value for c in claims), f"the claims were {claims}"


# --- the network transport ------------------------------------------------------

class Answer:
    def __init__(self, status, etag, body):
        self.status, self.headers, self.body = status, {"ETag": etag}, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def network(ctx, monkeypatch, answer):
    ctx.outgoing = []

    def urlopen(outgoing, timeout):
        ctx.outgoing.append((outgoing, timeout))
        return answer(outgoing)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)


@given(parsers.parse('the network answers {status:d} with the ETag "{etag}" and the body "{body}"'))
def network_answers(ctx, monkeypatch, status, etag, body):
    network(ctx, monkeypatch, lambda outgoing: Answer(status, etag, body.encode()))


@given(parsers.parse('the network refuses with {status:d}, saying "{body}"'))
def network_refuses(ctx, monkeypatch, status, body):
    def refuse(outgoing):
        raise urllib.error.HTTPError(outgoing.full_url, status, "no", {}, io.BytesIO(body.encode()))
    network(ctx, monkeypatch, refuse)


@given(parsers.parse('the network is down, saying "{reason}"'))
def network_down(ctx, monkeypatch, reason):
    def down(outgoing):
        raise urllib.error.URLError(reason)
    network(ctx, monkeypatch, down)


def answered(response: Response) -> dict:
    return {"status": response.status, "body": response.body.decode(), "error": response.error,
            "etag": response.etag, "retry_after": response.retry_after}


@when(parsers.parse('the network transport sends "{asked}" with a timeout of {timeout:d} seconds'))
def network_sends(ctx, asked, timeout):
    ctx.attempt(lambda: answered(Network().send(request(ctx, asked), timeout)))


@then(parsers.parse('the network was sent "{method}" with the body "{body}" and a timeout of {timeout:d} seconds'))
def network_sent(ctx, method, body, timeout):
    seen = [(o.method, o.data, t) for o, t in ctx.outgoing]
    assert (method, body.encode(), timeout) in seen, f"the network was sent {seen}"


@then("the network was told the caller is integrations at its version")
def network_user_agent(ctx):
    seen = [o.get_header("User-agent") for o, _ in ctx.outgoing]
    assert seen == [f"integrations/{__version__}"], f"the network was told {seen}"


# --- recordings -----------------------------------------------------------------

@given(parsers.parse('a recording of answers where "{asked}" is {status:d} with:'))
def a_recording(ctx, asked, status, docstring):
    ctx.recording = Recorder(Cassette({"responses": {asked: {"status": status, "json": json.loads(docstring)}}}))


@when(parsers.parse('the recording sends "{asked}" with the header "{name}" set to "{value}"'))
def recording_sends_with(ctx, asked, name, value):
    method, url = asked.split(" ", 1)
    ctx.attempt(lambda: ctx.recording.send(Request(method, url, {name: value}), 1))


@when(parsers.parse('the recording sends "{asked}"'))
def recording_sends(ctx, asked):
    method, url = asked.split(" ", 1)
    ctx.attempt(lambda: ctx.recording.send(Request(method, url), 1))


@when(parsers.parse('the recording is saved as "{path}"'))
def recording_saved(ctx, path):
    ctx.attempt(lambda: ctx.recording.save(ctx.directory / path))


@when(parsers.parse('a replay of "{path}" sends "{asked}"'))
def replay_sends(ctx, path, asked):
    method, url = asked.split(" ", 1)
    ctx.attempt(lambda: Cassette(ctx.directory / path).send(Request(method, url), 1))


@when(parsers.parse("I read an empty answer with the status {status:d} as JSON"))
def empty_answer(ctx, status):
    ctx.attempt(lambda: Response(status).json())


# --- modes, health, the package -----------------------------------------------

@when("I read the repository's modes")
def read_repository_modes(ctx):
    ctx.attempt(lambda: read_modes(ctx.directory))


@given("the plugins' modes:")
def plugins_modes(ctx, datatable):
    ctx.modes = {row[0]: row[1] for row in datatable[1:]}


@given("the checks:")
def the_checks(ctx, datatable):
    ctx.checks = [Check(row[0], row[1] == "true", row[2]) for row in datatable[1:]]


@when("I judge the health")
def judge_health(ctx):
    def judged():
        made = health(ctx.checks, ctx.modes)
        return {"healthy": made.healthy, "unhealthy": [c.plugin for c in made.unhealthy],
                "alerts": [c.plugin for c in made.alerts]}
    ctx.attempt(judged)


@when("I read the package's version and pyproject.toml's")
def versions(ctx):
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    ctx.attempt(lambda: sorted({__version__, pyproject["project"]["version"]}))


@when(parsers.parse('I check the settings schema of "{plugin}"'))
def settings_schema(ctx, plugin):
    ctx.attempt(lambda: jsonschema.Draft202012Validator.check_schema(
        registry.load(registry.PLUGINS / f"{plugin}.json")["settings"]))


# --- github.edit-pr ---------------------------------------------------------------

@given(parsers.parse('GitHub edits of "{repo}" with the token "{token}"'))
def github_edits(ctx, repo, token):
    ctx.edits = lambda: EditPullRequests(made_caller(ctx), lambda: token, repo)


@given("the edit:")
def the_edit(ctx, datatable):
    old = lambda cell: None if cell == "nothing" else cell  # noqa: E731
    ctx.changes = [Change(row[0], old(row[1]), row[2]) for row in datatable[1:]]


@when(parsers.parse('I propose the edit on the stem "{stem}" as "{title}" with "{body}"'))
def propose_edit(ctx, stem, title, body):
    ctx.attempt(lambda: ctx.edits().propose(stem=stem, changes=ctx.changes, title=title, body=body,
                                            request_id=getattr(ctx, "request_id", None)))


@when("I check GitHub edits")
def check_edits(ctx):
    ctx.attempt(lambda: asdict(ctx.edits().check()))


@when(parsers.parse("I take the next branch on the stem \"{stem}\" after '{taken}'"))
def take_next_branch(ctx, stem, taken):
    ctx.attempt(lambda: next_branch(stem, json.loads(taken)))


@given(parsers.parse('a fake of GitHub edits of "{repo}" whose base holds "{path}" as "{text}"'))
def fake_edits(ctx, repo, path, text):
    ctx.fake_edits = FakeEditPullRequests(files={path: text}, repo=repo)


@given(parsers.parse("a fake of GitHub edits that is {state}"))
def fake_edits_set(ctx, state):
    ctx.fake_edits = FakeEditPullRequests(working=state == "working")


@when(parsers.parse('the fake proposes changing "{path}" from "{old}" to "{new}" on the stem "{stem}"'))
def fake_proposes_change(ctx, path, old, new, stem):
    ctx.attempt(lambda: ctx.fake_edits.propose(stem=stem, changes=[Change(path, old, new)], title="T", body="B"))


@when(parsers.parse('the fake proposes adding "{path}" holding "{new}" on the stem "{stem}"'))
def fake_proposes_adding(ctx, path, new, stem):
    ctx.attempt(lambda: ctx.fake_edits.propose(stem=stem, changes=[Change(path, None, new)], title="T", body="B"))


@when("I ask the fake for its proposals' branches")
def fake_branches(ctx):
    ctx.attempt(lambda: [p["branch"] for p in ctx.fake_edits.proposals])


@when("I check the fake of GitHub edits")
def check_fake_edits(ctx):
    ctx.attempt(lambda: ctx.fake_edits.check())


# --- github.app-token ---------------------------------------------------------------

@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@given(parsers.parse('a GitHub App "{app_id}" with a private key, installed as "{installation}"'))
def github_app(ctx, pem, app_id, installation):
    ctx.now = time.time()
    ctx.app_token = AppToken(made_caller(ctx), app_id, pem, installation, clock=lambda: ctx.now)


@given(parsers.parse("the clock reads {now:g}"))
def clock_reads(ctx, now):
    ctx.now = now


@when("I ask for the app's token")
def ask_app_token(ctx):
    ctx.attempt(ctx.app_token)


@when(parsers.parse("{minutes:d} minutes pass"))
def minutes_pass(ctx, minutes):
    ctx.now += minutes * 60


@when("I check the app's token")
def check_app_token(ctx):
    ctx.attempt(ctx.app_token.check)


@when("I ask the fake app for its token")
def ask_fake_app_token(ctx):
    ctx.attempt(FakeAppToken())


@given(parsers.parse("a fake app token that is {state}"))
def fake_app_token(ctx, state):
    ctx.fake_app_token = FakeAppToken(working=state == "working")


@when("I check the fake app token")
def check_fake_app_token(ctx):
    ctx.attempt(ctx.fake_app_token.check)
