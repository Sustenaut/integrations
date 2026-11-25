"""Record and replay: the transport a caller given none uses, cassettes in a child
process, Caller.opener, faults and clocks a harness gives, and GitHub's test steps in
replay (integrations.testing; recording them is github-recording.feature)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from integrations.calls import Caller, Network, Request
from integrations.testing import plugin as testing_plugin
from integrations.testing import github
from integrations.testing.github import repositories
from integrations.transports import (Cassette, Faulty, Recorder, cassette_reports, current_transport, promote, use,
                                     use_clock)
from pytest_bdd import given, parsers, scenarios, when

from .providers import records

scenarios("transports.feature")

REPOSITORY = Path(__file__).resolve().parents[2]

# GitHub's test steps, as a suite that loads integrations.testing.github gets them.
globals().update({k: v for k, v in vars(github).items() if k.startswith("pytestbdd_")})


def named(transport) -> str:
    name = type(transport).__name__
    return f"{name} around {type(transport.inner).__name__}" if isinstance(transport, (Recorder, Faulty)) else name


@when("I ask for the current transport")
def ask_current(ctx):
    ctx.attempt(lambda: named(current_transport()))


@given(parsers.parse('a cassette from "{file}"'))
def a_cassette(ctx, file):
    """The cassette of a vector, at its "cassette"."""
    ctx.cassette = Cassette(json.loads((REPOSITORY / file).read_text(encoding="utf-8"))["cassette"], mode="replay")


@given(parsers.parse('a cassette in record mode from "{file}"'))
def a_cassette_recording(ctx, file):
    ctx.cassette = Cassette(json.loads((REPOSITORY / file).read_text(encoding="utf-8"))["cassette"], mode="record")


@given(parsers.parse('the cassette gives no answer to "{match}"'))
def no_answer_to(ctx, match):
    ctx.cassette = Faulty(ctx.cassette, [{"match": match, "effect": "no-answer"}])


def a_caller(ctx, **kwargs) -> Caller:
    return Caller("a-repo", "web", sink=records(ctx), new_id=lambda: "id-1", sleep=lambda _: None, **kwargs)


@when(parsers.parse('a caller given no transport calls "{plugin}" "{operation}" with "{asked}" '
                    'while the cassette is in use'))
def call_within_use(ctx, plugin, operation, asked):
    method, url = asked.split(" ", 1)
    caller = a_caller(ctx)
    with use(ctx.cassette):
        ctx.attempt(lambda: caller.call(plugin, operation, Request(method, url)).json())


@when(parsers.parse('a caller given its own transport calls "{plugin}" "{operation}" with "{asked}"'))
def call_with_own_transport(ctx, plugin, operation, asked):
    method, url = asked.split(" ", 1)
    caller = a_caller(ctx, transport=Network())
    ctx.attempt(lambda: caller.call(plugin, operation, Request(method, url)))


@when("I ask for the current transport after the cassette's block")
def current_after_use(ctx):
    with use(ctx.cassette):
        pass
    ctx.attempt(lambda: named(current_transport()))


def propose_like(open_, url: str) -> dict:
    """What a client that takes an opener does with it: send, then read the answer."""
    answer = open_("POST", url, {"Content-Type": "application/json"},
                   '{"title":"A","head":"b","base":"main"}')
    return {"status": answer.status, "json": answer.json()}


@when(parsers.parse('code that takes an opener opens a pull request at "{url}" through the caller\'s opener for '
                    '"{plugin}" "{operation}"'))
def open_through_opener(ctx, url, plugin, operation):
    caller = a_caller(ctx, transport=ctx.cassette)
    ctx.attempt(lambda: propose_like(caller.opener(plugin, operation), url))


@when(parsers.parse('I make an opener for "{plugin}" "{operation}"'))
def make_opener(ctx, plugin, operation):
    ctx.attempt(lambda: Caller("a-repo").opener(plugin, operation))


@when("I save a recorder that was given no path")
def save_without_path(ctx):
    ctx.attempt(lambda: Recorder(Network()).save())


@when(parsers.parse('a recorder made for "{made}" is saved as "{path}"'))
def recorder_saved_as(ctx, made, path):
    ctx.attempt(lambda: Recorder(Network(), made).save(path))


@when(parsers.parse('a recorder made for "{made}" is saved'))
def recorder_saved(ctx, made):
    ctx.attempt(lambda: Recorder(Network(), made).save())


@given(parsers.parse('"{path}" is a symlink to "{target}"'))
def a_symlink(ctx, path, target):
    link = ctx.directory / path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


@when(parsers.parse('I promote "{path}"'))
def promote_file(ctx, path):
    ctx.attempt(lambda: promote(path))


@when("I close the cassette")
def close_cassette(ctx):
    def close():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ctx.cassette.close()
        return " ".join(str(w.message) for w in caught)
    ctx.attempt(close)


@when(parsers.parse('I read "{path}" as a cassette'))
def read_cassette(ctx, path):
    ctx.attempt(lambda: Cassette(ctx.directory / path))


@when(parsers.parse('I promote the cassette at "{key}" in "{path}"'))
def promote_cassette(ctx, key, path):
    def promoted():
        file = ctx.directory / path
        file.write_text(json.dumps(json.loads(file.read_text(encoding="utf-8"))[key]), encoding="utf-8")
        promote(file)
    ctx.attempt(promoted)


@when("I list the scenario's GitHub repositories")
def list_repositories(ctx):
    ctx.attempt(lambda: {name: {"recorded": r.recorded, "files": r.files} for name, r in repositories(ctx).items()})


@when(parsers.parse("pytest configures the integrations.testing plugin with integrations_testing {ini}"))
def configure_plugin(ctx, ini):
    loaded: list[str] = []

    class Manager:
        def import_plugin(self, name):
            loaded.append(name)

    class Config:
        pluginmanager = Manager()

        def getini(self, name):
            assert name == "integrations_testing"
            return ini == "true"

    def configure():
        testing_plugin.pytest_configure(Config())
        return loaded
    ctx.attempt(configure)


@given(parsers.parse('a file "{path}" holding the cassette of "{file}"'))
def file_of_cassette(ctx, path, file):
    cassette = json.loads((REPOSITORY / file).read_text(encoding="utf-8"))["cassette"]
    (ctx.directory / path).write_text(json.dumps(cassette), encoding="utf-8")


def call_current(ctx, plugin, operation, asked):
    method, url = asked.split(" ", 1)
    answer = a_caller(ctx).call(plugin, operation, Request(method, url))
    return {"status": answer.status, "json": answer.json()}


@when(parsers.parse('a caller given no transport calls "{plugin}" "{operation}" with "{asked}", and the process '
                    'reports "{cassette}"'))
def call_and_report(ctx, plugin, operation, asked, cassette):
    def reported():
        call_current(ctx, plugin, operation, asked)
        return [r for r in cassette_reports() if r["cassette"] == cassette][-1]
    ctx.attempt(reported)


@when(parsers.parse('a caller given no transport calls "{plugin}" "{operation}" with "{asked}"'))
def call_through_current(ctx, plugin, operation, asked):
    ctx.attempt(lambda: call_current(ctx, plugin, operation, asked))


def under_harness_clock(ctx, plugin, operation, asked, **given):
    """A 503 then a 200, on a caller made before the harness's clock, which starts at
    100 s, moves on with each sleep, and jitters a quarter of the way."""
    method, url = asked.split(" ", 1)
    now, slept = [100.0], []
    caller = Caller("a-repo", "web", sink=records(ctx), new_id=lambda: "id-1",
                    transport=Cassette({"responses": {f"{method} {url}": [{"status": 503}, {"status": 200, "json": {}}]}}),
                    **given)

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    with use_clock(clock=lambda: now[0], sleep=sleep, random=lambda: 0.25):
        answer = caller.call(plugin, operation, Request(method, url))
    return {"status": answer.status, "slept": slept, "latency_ms": records(ctx).records[-1].latency_ms}


@when(parsers.parse('a caller made before the harness\'s clock calls "{plugin}" "{operation}" with "{asked}", '
                    'answered 503 and then 200'))
def call_under_harness_clock(ctx, plugin, operation, asked):
    ctx.attempt(lambda: under_harness_clock(ctx, plugin, operation, asked))


@when(parsers.parse('a caller with its own sleep, made before the harness\'s clock, calls "{plugin}" "{operation}" '
                    'with "{asked}", answered 503 and then 200'))
def call_with_own_sleep(ctx, plugin, operation, asked):
    own: list[float] = []
    ctx.attempt(lambda: {**under_harness_clock(ctx, plugin, operation, asked, sleep=own.append), "own": own})
