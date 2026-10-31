"""The wrapper every client call goes through, and the first clients."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from dataclasses import asdict
from pathlib import Path

import jsonschema
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations import __version__, registry
from integrations.calls import (Caller, Check, JsonlSink, MemorySink, Network, Recording, Refused, Replay, Request,
                                Response, Unavailable, health, read_modes)
from integrations.clients.github_app_token import AppToken, FakeAppToken
from integrations.clients.github_edit_pr import Change, Conflict, EditPullRequests, FakeEditPullRequests, next_branch

RECORD_SCHEMA = registry.load(registry.SCHEMAS / "call-record.schema.json")
PLUGIN = "github.edit-pr"
URL = "https://api.example/x"


def caller(responses=None, **kw):
    sink = MemorySink()
    transport = Replay(responses or {})
    naps = []
    made = Caller("a-repo", "web", sink=sink, transport=transport, sleep=naps.append, new_id=lambda: "id-1", **kw)
    return made, sink, transport, naps


def test_a_record_says_who_called_what_and_how_it_ended():
    made, sink, _, _ = caller({f"POST {URL}": {"status": 201, "json": {"ok": True}}})
    response = made.call(PLUGIN, "open-pr", Request("POST", URL, {"Authorization": "secret"}, b"12345"))
    assert response.json() == {"ok": True}
    record = asdict(sink.records[0])
    jsonschema.validate(record, RECORD_SCHEMA)
    assert (record["repo"], record["app"], record["plugin"], record["operation"]) == ("a-repo", "web", PLUGIN, "open-pr")
    assert (record["request_id"], record["status"], record["outcome"]) == ("id-1", 201, "ok")
    assert (record["bytes_out"], record["bytes_in"], record["retries"]) == (5, len(response.body), 0)
    assert (record["mode"], record["route"]) == ("required", "routed")


def test_a_record_holds_no_body_url_or_token():
    made, sink, _, _ = caller({f"POST {URL}": {"status": 201, "json": {"secret-answer": 1}}})
    made.call(PLUGIN, "open-pr", Request("POST", URL, {"Authorization": "secret-token"}, b"secret-body"))
    text = json.dumps(asdict(sink.records[0]))
    for leak in ("secret", "api.example"):
        assert leak not in text


def test_an_app_may_give_the_request_id_so_a_slow_save_can_be_traced():
    made, sink, _, _ = caller({f"GET {URL}": {"status": 200, "json": {}}})
    made.call(PLUGIN, "open-pr", Request("GET", URL), request_id="from-the-app")
    assert sink.records[0].request_id == "from-the-app"


def test_an_operation_not_in_the_registry_is_refused():
    made, sink, _, _ = caller()
    with pytest.raises(ValueError, match="not a registered operation"):
        made.call(PLUGIN, "nope", Request("GET", URL))
    assert not sink.records


def test_a_refusal_comes_back_to_the_client_and_is_recorded():
    made, sink, _, _ = caller({f"GET {URL}": {"status": 404, "json": {}}})
    assert made.call(PLUGIN, "open-pr", Request("GET", URL)).status == 404
    assert sink.records[0].outcome == "refused"


@pytest.mark.parametrize("answer", [{"error": "no answer: down"}, {"status": 500, "json": {}}, {"status": 429, "json": {}}])
def test_no_answer_or_a_failure_raises_and_is_recorded(answer):
    made, sink, _, _ = caller({f"GET {URL}": answer})
    with pytest.raises(Unavailable):
        made.call(PLUGIN, "open-pr", Request("GET", URL))
    assert sink.records[0].outcome == "unavailable"


def test_a_call_a_person_waits_on_is_not_retried():
    made, _, transport, naps = caller({f"GET {URL}": {"status": 503, "json": {}}})
    with pytest.raises(Unavailable):
        made.call(PLUGIN, "open-pr", Request("GET", URL))
    assert len(transport.sent) == 1 and not naps


def test_a_scheduled_call_is_retried_with_backoff_and_the_retries_recorded():
    answers = [{"status": 503, "json": {}}, {"error": "no answer"}, {"status": 200, "json": {}}]
    made, sink, transport, naps = caller({f"GET {URL}": answers})
    assert made.call(PLUGIN, "check", Request("GET", URL)).ok
    assert len(transport.sent) == 3 and naps == [1, 2]
    assert sink.records[0].retries == 2 and sink.records[0].outcome == "ok"


def test_a_call_not_safe_to_repeat_is_not_retried_unless_it_says_so():
    made, _, transport, _ = caller({f"POST {URL}": {"status": 503, "json": {}}})
    with pytest.raises(Unavailable):
        made.call(PLUGIN, "check", Request("POST", URL))
    assert len(transport.sent) == 1
    with pytest.raises(Unavailable):
        made.call(PLUGIN, "check", Request("POST", URL, repeatable=True))
    assert len(transport.sent) == 4


def test_a_refusal_is_not_retried():
    made, _, transport, _ = caller({f"GET {URL}": {"status": 403, "json": {}}})
    made.call(PLUGIN, "check", Request("GET", URL))
    assert len(transport.sent) == 1


def test_a_sink_that_fails_loses_the_record_not_the_call(capsys):
    class Broken:
        def write(self, record):
            raise OSError("disk full")

    made = Caller("a-repo", sink=Broken(), transport=Replay({f"GET {URL}": {"status": 200, "json": {}}}))
    assert made.call(PLUGIN, "open-pr", Request("GET", URL)).ok
    assert "disk full" in capsys.readouterr().err


def test_the_default_sink_keeps_nothing_and_the_file_sink_appends(tmp_path):
    responses = {f"GET {URL}": {"status": 200, "json": {}}}
    assert Caller("a-repo", transport=Replay(responses)).call(PLUGIN, "open-pr", Request("GET", URL)).ok
    path = tmp_path / "calls.jsonl"
    made = Caller("a-repo", sink=JsonlSink(path), transport=Replay(responses))
    made.call(PLUGIN, "open-pr", Request("GET", URL))
    made.call(PLUGIN, "open-pr", Request("GET", URL))
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    jsonschema.validate(json.loads(lines[0]), RECORD_SCHEMA)


def test_the_network_transport_sends_only_over_https():
    answer = Network().send(Request("GET", "http://api.example/x"), 1)
    assert answer.error == "not https" and answer.status is None


def test_a_recording_can_be_replayed_and_keeps_no_token(tmp_path):
    recording = Recording(Replay({f"GET {URL}": {"status": 200, "json": {"a": 1}}}))
    recording.send(Request("GET", URL, {"Authorization": "secret"}), 1)
    recording.send(Request("GET", "https://nothing.example/"), 1)
    path = tmp_path / "recorded.json"
    recording.save(path)
    assert "secret" not in path.read_text()
    replay = Replay.from_file(path)
    assert replay.send(Request("GET", URL), 1).json() == {"a": 1}
    assert replay.send(Request("GET", "https://nothing.example/"), 1).error == "not recorded"


def test_an_empty_body_reads_as_nothing():
    assert Response(204).json() is None


def test_mode_comes_from_the_repositorys_file_and_reaches_the_record(tmp_path):
    (tmp_path / "integrations.json").write_text(json.dumps({
        "uses": [{"plugin": PLUGIN, "paths": ["a"], "mode": "advisory"},
                 {"plugin": "slack.post", "paths": ["a"]},
                 {"plugin": "linear.sync", "paths": ["a"], "mode": "advisory"},
                 {"plugin": "linear.sync", "paths": ["b"]}],
        "terraform": [{"dir": "infra", "plugins": ["terraform.hetzner"], "runs": [], "mode": "advisory"}]}))
    modes = read_modes(tmp_path)
    assert modes == {PLUGIN: "advisory", "slack.post": "required", "linear.sync": "required",
                     "terraform.hetzner": "advisory"}
    made, sink, _, _ = caller({f"GET {URL}": {"status": 200, "json": {}}}, modes=modes)
    made.call(PLUGIN, "open-pr", Request("GET", URL))
    assert sink.records[0].mode == "advisory"


def test_required_down_is_unhealthy_and_advisory_down_only_alerts():
    modes = {"a.required": "required", "b.advisory": "advisory"}
    up = [Check("a.required", True), Check("b.advisory", True)]
    assert health(up, modes).healthy
    advisory_down = health([Check("a.required", True), Check("b.advisory", False, "down")], modes)
    assert advisory_down.healthy and [c.plugin for c in advisory_down.alerts] == ["b.advisory"]
    required_down = health([Check("a.required", False), Check("not.listed", False)], modes)
    assert not required_down.healthy and len(required_down.unhealthy) == 2


def test_the_version_is_the_same_in_both_places():
    text = (Path(__file__).parents[2] / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in text


@pytest.mark.parametrize("plugin", ["github.edit-pr", "github.app-token"])
def test_a_plugin_with_a_client_has_a_settings_schema(plugin):
    jsonschema.Draft202012Validator.check_schema(registry.load(registry.PLUGINS / f"{plugin}.json")["settings"])


API = "https://api.github.com/repos/o/r"
SHA = "base-sha"


def github(**extra):
    """What GitHub answers while an edit to a.md becomes a pull request."""
    content = base64.b64encode(b"old text").decode()
    return {
        f"GET {API}/git/ref/heads/main": {"status": 200, "json": {"object": {"sha": SHA}}},
        f"GET {API}/contents/a.md?ref={SHA}": {"status": 200, "json": {"content": content, "sha": "blob-a"}},
        f"GET {API}/contents/new.md?ref={SHA}": {"status": 404, "json": {}},
        f"GET {API}/git/matching-refs/heads/edit/card-": {
            "status": 200, "json": [{"ref": "refs/heads/edit/card-1"}, {"ref": "refs/heads/edit/card-7"}]},
        f"POST {API}/git/refs": {"status": 201, "json": {}},
        f"PUT {API}/contents/a.md": {"status": 200, "json": {}},
        f"PUT {API}/contents/new.md": {"status": 201, "json": {}},
        f"POST {API}/pulls": {"status": 201, "json": {"html_url": "https://github.com/o/r/pull/9"}},
        **extra,
    }


def editor(responses):
    made, sink, transport, _ = caller(responses)
    return EditPullRequests(made, lambda: "a-token", "o/r"), sink, transport


def sent_json(transport, method, url):
    return next(json.loads(r.body) for r in transport.sent if (r.method, r.url) == (method, url))


def test_an_edit_becomes_a_pull_request_on_the_next_free_branch():
    pulls, sink, transport = editor(github())
    url = pulls.propose(stem="edit/card-", changes=[Change("a.md", "old text", "new text"), Change("new.md", None, "n")],
                        title="T", body="B", request_id="from-the-app")
    assert url == "https://github.com/o/r/pull/9"
    assert sent_json(transport, "POST", f"{API}/git/refs") == {"ref": "refs/heads/edit/card-8", "sha": SHA}
    put = sent_json(transport, "PUT", f"{API}/contents/a.md")
    assert (put["branch"], put["sha"]) == ("edit/card-8", "blob-a")
    assert base64.b64decode(put["content"]) == b"new text"
    assert "sha" not in sent_json(transport, "PUT", f"{API}/contents/new.md")
    assert sent_json(transport, "POST", f"{API}/pulls") == {"title": "T", "head": "edit/card-8", "base": "main", "body": "B"}
    assert {r.plugin for r in sink.records} == {"github.edit-pr"}
    assert {r.request_id for r in sink.records} == {"from-the-app"}
    assert all(r.headers["Authorization"] == "Bearer a-token" for r in transport.sent)


def test_an_edit_of_a_file_that_moved_is_a_conflict_and_changes_nothing():
    pulls, _, transport = editor(github())
    with pytest.raises(Conflict, match="a.md"):
        pulls.propose(stem="edit/card-", changes=[Change("a.md", "what I saw", "new")], title="T", body="B")
    assert all(r.method == "GET" for r in transport.sent)


def test_a_new_file_that_now_exists_is_a_conflict():
    pulls, _, _ = editor(github())
    with pytest.raises(Conflict):
        pulls.propose(stem="edit/card-", changes=[Change("a.md", None, "new")], title="T", body="B")


def test_a_refusal_from_github_is_refused_and_names_no_query():
    pulls, _, _ = editor(github(**{f"POST {API}/pulls": {"status": 422, "json": {}}}))
    with pytest.raises(Refused, match="POST /pulls: HTTP 422"):
        pulls.propose(stem="edit/card-", changes=[Change("a.md", "old text", "new")], title="T", body="B")


def test_check_says_whether_the_repository_can_be_read():
    pulls, sink, _ = editor({f"GET {API}": {"status": 200, "json": {}}})
    assert pulls.check() == Check("github.edit-pr", True)
    assert sink.records[0].operation == "check"
    pulls, _, _ = editor({f"GET {API}": {"status": 404, "json": {}}})
    assert not pulls.check().ok
    pulls, _, _ = editor({f"GET {API}": {"error": "no answer"}})
    assert not pulls.check().ok


def test_the_branch_number_ignores_other_names():
    assert next_branch("s-", []) == "s-1"
    assert next_branch("s-", ["s-2", "s-x", "s-2-b", "other-9"]) == "s-3"


def test_the_fake_answers_as_the_client_does():
    fake = FakeEditPullRequests(files={"a.md": "old"}, repo="o/r")
    first = fake.propose(stem="s-", changes=[Change("a.md", "old", "new")], title="T", body="B")
    second = fake.propose(stem="s-", changes=[Change("b.md", None, "new")], title="T", body="B")
    assert first == "https://github.com/o/r/pull/1" and second.endswith("/2")
    assert [p["branch"] for p in fake.proposals] == ["s-1", "s-2"]
    with pytest.raises(Conflict):
        fake.propose(stem="s-", changes=[Change("a.md", "moved", "new")], title="T", body="B")
    assert fake.check().ok and not FakeEditPullRequests(working=False).check().ok


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


MINT = "POST https://api.github.com/app/installations/9/access_tokens"


def test_a_token_is_minted_once_and_reused_until_well_before_it_expires(pem):
    now = [1000.0]
    answers = [{"status": 201, "json": {"token": "first"}}, {"status": 201, "json": {"token": "second"}}]
    made, sink, transport, _ = caller({MINT: answers})
    token = AppToken(made, "5", pem, "9", clock=lambda: now[0])
    assert token() == "first" and token() == "first"
    assert len(transport.sent) == 1
    head, claims, _signature = transport.sent[0].headers["Authorization"].removeprefix("Bearer ").split(".")
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    assert decoded["iss"] == "5" and decoded["exp"] == 1540
    assert sink.records[0].plugin == "github.app-token" and sink.records[0].route == "routed"
    now[0] += 46 * 60
    assert token() == "second"


def test_a_refused_token_is_refused_and_check_says_so(pem):
    made, _, _, _ = caller({MINT: {"status": 401, "json": {}}})
    with pytest.raises(Refused):
        AppToken(made, "5", pem, "9")()
    assert not AppToken(made, "5", pem, "9").check().ok
    made, _, _, _ = caller({MINT: {"status": 201, "json": {"token": "t"}}})
    assert AppToken(made, "5", pem, "9").check().ok


def test_the_fake_token_signs_nothing():
    assert FakeAppToken()() == "fake-token" and FakeAppToken().check().ok
    assert not FakeAppToken(working=False).check().ok


class Answer:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"body"


def test_the_network_transport_reads_an_answer_a_refusal_and_silence(monkeypatch):
    def answers(outgoing, timeout):
        assert (outgoing.method, outgoing.data, timeout) == ("POST", b"out", 7)
        return Answer()

    request = Request("POST", "https://api.example/x", {}, b"out")
    monkeypatch.setattr("urllib.request.urlopen", answers)
    assert Network().send(request, 7) == Response(200, b"body")

    def refuses(outgoing, timeout):
        raise urllib.error.HTTPError(outgoing.full_url, 403, "no", {}, io.BytesIO(b"why"))

    monkeypatch.setattr("urllib.request.urlopen", refuses)
    assert Network().send(request, 7) == Response(403, b"why")

    def silent(outgoing, timeout):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", silent)
    assert Network().send(request, 7) == Response(None, error="no answer: down")
