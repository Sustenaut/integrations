"""pubtest's record/replay harness, end to end (harness-recording.feature,
harness-replay.feature). They live here, not in pubtest, because they need a
real Caller and integrations' transports, and pubtest's own tests must not
depend on integrations, whose suite runs on pubtest. Each scenario runs a small
suite of its own, with its own pubrules.json, so this suite never turns
cassettes on for itself."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pubtest.core import Result
from pubtest.steps import phrase
from pytest_bdd import given, scenarios, then, when

import integrations

scenarios("harness-recording.feature", "harness-replay.feature")

# `the program under test is "pubtest"` runs the pubtest this suite installed.
os.environ.setdefault("PUBTEST_BIN_DIR", str(Path(sys.executable).parent))


@then(phrase('the cassette "{path}" was recorded with this integrations'))
def recorded_with_this(ctx, path):
    current = json.loads((ctx.directory / path).read_text(encoding="utf-8"))["current"]
    assert current["recorded"]["integrations"] == integrations.__version__, current[
        "recorded"
    ]


INNER_SUITE = """import json
import os
from datetime import datetime, timezone
from pathlib import Path

from integrations.calls import Caller, Request, Response
from pytest_bdd import scenarios, when

from pubtest import cassettes
from pubtest.steps import phrase

scenarios(".")
os.environ["PATH"] = str(Path(__file__).parent.parent / "bin") + os.pathsep + os.environ["PATH"]


class Network:
    def send(self, request, timeout):
        body = {"name": request.url.rstrip("/").rsplit("/", 1)[-1],
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return Response(200, json.dumps(body).encode(), headers={"Content-Type": "application/json"})


cassettes.network = Network


@when(phrase('the inner client gets "{url}"'))
def gets(ctx, url):
    ctx.attempt(lambda: Caller("inner", "suite").call("github.repo-ops", "call", Request("GET", url)).json())
"""

# The same suite with a test customer, inner.watched, whose cleanup, and the cleanup its
# step gives the scenario, each write to cleanups.txt what they see: the mode, whether
# INTEGRATIONS_CASSETTE is the one the scenario's step saw, and whether it failed. The
# customer's cleanup makes a call, as integrations' GitHub customer's does, and its step
# "the watched customer's cleanup fails" breaks it. After the scenarios, the file says
# what INTEGRATIONS_CASSETTE is (WATCHED_CONFTEST).
WATCHED = """

from pytest_bdd import given

from pubtest import plugin

LOG = Path(__file__).parent.parent / "cleanups.txt"


def log(line):
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\\n")


def sees(tape):
    cassette = os.environ.get("INTEGRATIONS_CASSETTE")
    which = "no cassette" if cassette is None else "the scenario's cassette" if cassette == Watched.seen else "another cassette"
    return f"in {os.environ.get('INTEGRATIONS_MODE')} mode, with {which}, after it {'failed' if tape.failed else 'passed'}"


class Watched:
    name = "inner.watched"
    seen = None
    breaks = False

    @staticmethod
    def setup(tape):
        return {"the repository": "watched-1"}

    @staticmethod
    def cleanup(tape, names):
        url = f"https://api.github.com/repos/o/{names['the repository']}"
        answer = Caller("inner", "cleanup").call("github.repo-ops", "call", Request("DELETE", url)).json()
        log(f"the customer's cleanup of {answer['name']}, {sees(tape)}")
        if Watched.breaks:
            raise RuntimeError("the watched cleanup broke")


# Registered as a pubtest.customers entry point's would be.
plugin.CUSTOMERS[Watched.name] = Watched


@given(phrase('the watched customer\\'s repository "{logical}"'))
def watched_repository(ctx, logical):
    Watched.seen, Watched.breaks = os.environ.get("INTEGRATIONS_CASSETTE"), False
    assert logical in ctx.tape.names_of(Watched.name)
    ctx.cleanup(lambda: log(f"the scenario's cleanup, {sees(ctx.tape)}"))


@given("the watched customer's cleanup fails")
def watched_cleanup_fails():
    Watched.breaks = True
"""

WATCHED_CONFTEST = """import os
from pathlib import Path


def pytest_sessionfinish(session):
    with (Path(__file__).parent.parent / "cleanups.txt").open("a", encoding="utf-8") as out:
        out.write(f"after the scenarios, INTEGRATIONS_CASSETTE is {os.environ.get('INTEGRATIONS_CASSETTE', 'unset')}\\n")
"""

INNER_CLIENT = """#!{python}
import json
import sys

from integrations.calls import Caller, Request

print(json.dumps(Caller("inner", "program").call("github.repo-ops", "call", Request("GET", sys.argv[1])).json()))
"""

# What a harness sets for the programs it runs, which an inner suite must not inherit.
HARNESS = (
    "PUBTEST_RUN",
    "INTEGRATIONS_MODE",
    "INTEGRATIONS_CASSETTE",
    "INTEGRATIONS_CASSETTES",
    "INTEGRATIONS_CASSETTE_REPORT",
    "PUBTEST_ONLY",
    "PUBTEST_PLAN",
    "PUBTEST_RECORD",
    "PUBTEST_CASSETTES",
)


@given(phrase('an inner suite with the feature "{name}":'))
def inner_suite(ctx, name, docstring):
    _inner_suite(ctx, name, docstring, INNER_SUITE)


@given(phrase('an inner suite with a watched test customer and the feature "{name}":'))
def inner_suite_watched(ctx, name, docstring):
    _inner_suite(ctx, name, docstring, INNER_SUITE + WATCHED)
    (ctx.directory / "tests" / "conftest.py").write_text(
        WATCHED_CONFTEST, encoding="utf-8"
    )


@given(phrase('an inner suite on the real network with the feature "{name}":'))
def inner_suite_real(ctx, name, docstring):
    _inner_suite(
        ctx, name, docstring, INNER_SUITE.replace("cassettes.network = Network\n", "")
    )


EXAMPLE = """

@when(phrase('the inner client gets the example customer\\'s repository "{logical}"'))
def gets_example(ctx, logical):
    real = ctx.tape.names_of("pubtest.example")[logical]
    url = f"https://api.github.com/repos/pubtest-example/{real}"
    ctx.attempt(lambda: Caller("inner", "suite").call("github.repo-ops", "call", Request("GET", url)).json())
"""

# A test customer found as a package's would be, by its pubtest.customers entry point: the
# dist-info beside it, on the inner suite's pythonpath.
CUSTOMERS = Path(__file__).parent.parent / "harness" / "customers"


@given(
    phrase('an inner suite with the example test customer and the feature "{name}":')
)
def inner_suite_example(ctx, name, docstring):
    _inner_suite(ctx, name, docstring, INNER_SUITE + EXAMPLE, pythonpath=CUSTOMERS)


def _inner_suite(ctx, name, docstring, suite, pythonpath=None):
    root = ctx.directory
    (root / "features").mkdir(exist_ok=True)
    (root / "features" / name).write_text(docstring + "\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "pytest.ini").write_text(
        "[pytest]\nbdd_features_base_dir = ../features/\naddopts = -p no:pubrules -p no:cacheprovider\n"
        + (f"pythonpath = {pythonpath}\n" if pythonpath else ""),
        encoding="utf-8",
    )
    (root / "tests" / "test_inner.py").write_text(suite, encoding="utf-8")
    (root / "bin").mkdir(exist_ok=True)
    client = root / "bin" / "inner-client"
    client.write_text(
        INNER_CLIENT.replace("{python}", sys.executable), encoding="utf-8"
    )
    client.chmod(0o755)


@when("the inner suite runs")
def inner_suite_runs(ctx):
    env = {k: v for k, v in os.environ.items() if k not in HARNESS}
    ctx.acting()
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    done = subprocess.run(
        argv,
        cwd=ctx.directory / "tests",
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    ctx.ran(
        Result(code=done.returncode, stdout=done.stdout, stderr=done.stderr, argv=argv)
    )


@then(phrase('the inner run says "{text}"'))
def inner_run_says(ctx, text):
    said = ctx.outcome.stdout + ctx.outcome.stderr
    assert text in said, said
