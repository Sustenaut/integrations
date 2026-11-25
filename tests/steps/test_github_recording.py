"""Recording against the GitHub test org: GitHub's test steps
(integrations.testing.github), against a stand-in for the org, and the janitor."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integrations import cli
from integrations.calls import Caller, Request
from integrations.testing import github
from integrations.testing.github import hold, push, repositories, token
from integrations.transports import NAMES, Cassette, Recorder, names_from, use
from pubtest.core import dig, same, show
from pytest_bdd import given, parsers, scenarios, then, when

from .github_standin import StandInGitHub
from .providers import records

scenarios("github-recording.feature")

REPOSITORY = Path(__file__).resolve().parents[2]

# GitHub's test steps, as a suite that loads integrations.testing.github gets them.
globals().update({k: v for k, v in vars(github).items() if k.startswith("pytestbdd_")})


def standin(ctx) -> StandInGitHub:
    return ctx.standin


@given("a stand-in GitHub test org")
def a_standin(ctx):
    a_named_standin(ctx, "SustenautTest")


@given(parsers.parse('a stand-in GitHub test org "{org}"'))
def a_named_standin(ctx, org):
    ctx.standin = StandInGitHub(org, tokens=("ghs_recorder",))
    github.network = ctx.standin
    ctx.cleanup(lambda: setattr(github, "network", None))


@given(parsers.parse('the stand-in test org holds "{name}", made {hours:d} hours ago'))
def standin_holds(ctx, name, hours):
    made = datetime.now(timezone.utc) - timedelta(hours=hours)
    standin(ctx).add_repo(name, made.strftime("%Y-%m-%dT%H:%M:%SZ"))


@given(parsers.parse('the stand-in test org holds {count:d} repositories "{pattern}", made {hours:d} hours ago'))
def standin_holds_many(ctx, count, pattern, hours):
    for n in range(1, count + 1):
        standin_holds(ctx, pattern.replace("N", str(n)), hours)


@given("the stand-in test org says the first name it is asked for is taken")
def first_name_taken(ctx):
    standin(ctx).taken = 1


@given(parsers.parse('the stand-in test org answers "{rule}" with {status:d}'))
def standin_refuses(ctx, rule, status):
    standin(ctx).refuse[rule] = status


@given(parsers.parse('the recorder app\'s id "{app}", installation id "{installation}" and a key of its own are '
                     'in the environment'))
def app_credentials(ctx, monkeypatch, app, installation):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    monkeypatch.setenv("GITHUB_TEST_RECORDER_APP_ID", app)
    monkeypatch.setenv("GITHUB_TEST_RECORDER_INSTALLATION_ID", installation)
    monkeypatch.setenv("GITHUB_TEST_RECORDER_APP_KEY", pem)
    standin(ctx).public_key, standin(ctx).app_id = key.public_key(), app


def shown(ctx, value):
    """`value` with each repository's real name as the scenario's: `<r>` for its own
    name in the org, so a scenario need not know the run's number."""
    text = json.dumps(value)
    for repository in repositories(ctx).values():
        if repository.recorded != repository.name:
            text = text.replace(repository.recorded.split("/", 1)[1], "<r>")
    return json.loads(text)


@when(parsers.parse('I ask which name "{name}" was made under'))
def made_under(ctx, name):
    ctx.attempt(lambda: repositories(ctx)[name].recorded)


@when("I read the stand-in test org")
def read_standin(ctx):
    def read():
        found = standin(ctx)
        logical = {r.recorded.split("/", 1)[1]: r.name for r in repositories(ctx).values()}
        out = {}
        for name, repo in found.repos.items():
            branches = {b: {"files": found.files(name, b), "commits": len(found.ancestors(sha))}
                        for b, sha in repo["refs"].items()}
            out[logical.get(name, name)] = {"private": repo["private"], "description": repo["description"],
                                            "branches": branches}
        return out
    ctx.attempt(read)


@when("I read INTEGRATIONS_NAMES")
def read_names(ctx):
    ctx.attempt(lambda: shown(ctx, names_from()))


@when(parsers.parse('I push to "{branch}" of "{name}" as someone else'))
def push_unnamed(ctx, request, branch, name):
    ctx.attempt(lambda: push(ctx, name, branch, {}, request))


@when(parsers.parse('I ask how many repositories the stand-in test org was asked to make, and what it holds'))
def asked_to_make(ctx):
    found = standin(ctx)
    ctx.attempt(lambda: {"asked": sum(r["method"] == "POST" and r["path"].endswith("/repos") for r in found.requests),
                         "holds": len(found.repos)})


@when(parsers.parse('I set up the GitHub repository "{name}" holding "{files}"'))
def set_up(ctx, request, name, files):
    ctx.attempt(lambda: hold(ctx, name, json.loads(files), request))


@when("the scenario's recording ends")
def recording_ends(ctx):
    def end():
        ctx.github_recording.close()
        return {"left": sorted(standin(ctx).repos), NAMES: os.environ.get(NAMES)}
    ctx.attempt(end)


@when(parsers.parse('I set up the GitHub repository "{name}" holding "{files}", and the scenario\'s recording ends'))
def set_up_and_end(ctx, request, name, files):
    def both():
        try:
            hold(ctx, name, json.loads(files), request)
            refused = None
        except Exception as e:  # noqa: BLE001 - what it said is the point
            refused = str(e)
        ctx.github_recording.close()
        return {"refused": refused, "left": sorted(standin(ctx).repos)}
    ctx.attempt(both)


@when("I ask how the stand-in test org was called")
def how_called(ctx):
    def calls():
        out = []
        for r in standin(ctx).requests:
            auth = "a JWT" if r["auth"].count(".") == 2 else r["auth"]
            out.append({"call": f"{r['plugin']} {r['operation']} {r['method']} {r['path']}", "auth": auth})
        return out
    ctx.attempt(calls)


@when("I ask for the token the code under test calls GitHub with")
def the_token(ctx):
    ctx.attempt(lambda: token(ctx))


@when("I list the scenario's GitHub repositories")
def list_repositories(ctx):
    ctx.attempt(lambda: {name: {"recorded": r.recorded, "files": r.files, "pushes": r.pushes}
                         for name, r in repositories(ctx).items()})


# --- the code under test ----------------------------------------------------------------

@given("the code under test records its calls")
def code_records(ctx):
    ctx.recorder = Recorder(standin(ctx))
    block = use(ctx.recorder)
    block.__enter__()
    ctx.cleanup(lambda: block.__exit__(None, None, None))


@given(parsers.parse('the code under test replays the cassette in "{file}"'))
def code_replays(ctx, file):
    ctx.cassette = Cassette(REPOSITORY / file, mode="replay")
    block = use(ctx.cassette)
    block.__enter__()
    ctx.cleanup(lambda: block.__exit__(None, None, None))


class CodeUnderTest:
    """What a client of GitHub does, through a Caller given no transport: the one the
    harness uses, a Recorder or a Cassette."""

    def __init__(self, ctx, name: str):
        self.caller = Caller("a-repo", "web", sink=records(ctx), sleep=lambda _: None)
        self.base = f"https://api.github.com/repos/{repositories(ctx)[name].recorded}"
        self.auth = {"Authorization": f"Bearer {token(ctx)}"}

    def call(self, operation: str, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        return self.caller.call("github.edit-pr", operation, Request(method, self.base + path, self.auth, data))


@when(parsers.parse('the code under test reads the repository "{name}"'))
def code_reads_repository(ctx, name):
    ctx.attempt(lambda: CodeUnderTest(ctx, name).call("read", "GET", "").json())


@given(parsers.parse('the code under test read "{branch}" of "{name}"'))
def code_read_branch(ctx, branch, name):
    answer = CodeUnderTest(ctx, name).call("read", "GET", f"/git/ref/heads/{branch}")
    assert answer.ok, f"reading {branch} answered {answer.status}"
    ctx.read = {**getattr(ctx, "read", {}), branch: answer.json()["object"]["sha"]}


@when(parsers.parse('the code under test commits "{path}" as "{text}" on what it read of "{branch}" of "{name}"'))
def code_commits(ctx, path, text, branch, name):
    def commit():
        code, parent = CodeUnderTest(ctx, name), ctx.read[branch]
        tree = code.call("read", "GET", f"/git/commits/{parent}").json()["tree"]["sha"]
        made = code.call("open-pr", "POST", "/git/trees",
                         {"base_tree": tree, "tree": [{"path": path, "mode": "100644", "type": "blob",
                                                       "content": text}]}).json()["sha"]
        commit = code.call("open-pr", "POST", "/git/commits",
                           {"message": "mine", "tree": made, "parents": [parent]}).json()["sha"]
        answer = code.call("open-pr", "PATCH", f"/git/refs/heads/{branch}", {"sha": commit, "force": False})
        return {"status": answer.status, "message": answer.json().get("message")}
    ctx.attempt(commit)


@then("the recording's calls are:")
def recording_calls(ctx, datatable):
    calls = ctx.recorder.recording()["calls"]
    keys, rows = datatable[0], datatable[1:]
    got = [{"plugin": c["plugin"], "operation": c["operation"], "method": c["request"]["method"],
            "url": c["request"]["url"], "status": c["response"]["status"]} for c in calls]
    assert len(got) == len(rows), f"{len(got)} calls were recorded: {show(got)}"
    for call, row in zip(got, rows, strict=True):
        for key, cell in zip(keys, row, strict=True):
            assert same(call[key], cell), f"a call's {key} is {call[key]!r}, not {cell}: {show(got)}"


@then(parsers.parse('the recording\'s answer {n:d} has "{key}" equal to "{wanted}"'))
def recording_answer(ctx, n, key, wanted):
    body = ctx.recorder.recording()["calls"][n]["response"]["body"]["json"]
    assert same(dig(body, key), wanted), f"the answer's {key} is {dig(body, key)!r}, not {wanted}"


@then("every call the cassette holds was made")
def cassette_used(ctx):
    assert ctx.cassette.unused() == [], f"never made: {ctx.cassette.unused()}"


@then(parsers.parse('the result\'s "{key}" matches "{pattern}"'))
def result_key_matches(ctx, key, pattern):
    from pubtest.core import matches

    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    got = dig(ctx.result, key)
    assert isinstance(got, str) and matches(pattern, got), f"the result's {key} is {got!r}, not {pattern}"


@then(parsers.parse('the result\'s "{key}" is:'))
def result_key_is(ctx, key, docstring):
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    got, wanted = dig(ctx.result, key), json.loads(docstring)
    assert got == wanted, f"the result's {key} is {show(got)}, not {show(wanted)}"


@then(parsers.parse('the result\'s "{key}" includes "{text}"'))
def result_key_includes(ctx, key, text):
    assert ctx.refused is None, f"it was refused: {ctx.refused.message}"
    got = dig(ctx.result, key)
    assert isinstance(got, str) and text in got, f"the result's {key} is {got!r}"


# --- the janitor ------------------------------------------------------------------------

@when(parsers.parse("I run the janitor on the stand-in test org, for repositories older than {hours:d} hours"))
def run_janitor(ctx, hours):
    def janitor():
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        caller = Caller("Sustenaut/integrations", "test-org-janitor", sink=records(ctx), transport=standin(ctx))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.test_org_sweep(None, hours, caller=caller)
        return {"code": code, "stdout": out.getvalue().splitlines(), "stderr": err.getvalue(),
                "left": sorted(standin(ctx).repos)}
    ctx.attempt(janitor)
