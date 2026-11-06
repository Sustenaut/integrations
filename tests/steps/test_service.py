"""The running service, with GitHub on recorded answers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from integrations import calls
from integrations.calls import MemorySink, Replay
from integrations.clients.github_edit_pr import OneCommitProposer
from integrations.service import main  # noqa: F401 - what uvicorn runs must import
from integrations.service.app import RateLimit, create_app
from integrations.service.settings import Settings
from integrations.service.spec import spec_text

ROUTE = "/v1/github/edit-pr/proposals"
TOKEN = "person-secret-token"
API = "https://api.github.com"
AT = f"{API}/repos/o/r"
ORIGIN = "https://site.example"
FIELDS = {"repo": "o/r", "branch": "edit/one", "title": "T", "body": "B", "changes": {"a.txt": "new"}}


def github(**extra):
    """What GitHub answers while one commit becomes a pull request."""
    return {
        f"GET {AT}": {"status": 200, "json": {"default_branch": "main"}},
        f"GET {AT}/git/ref/heads/main": {"status": 200, "json": {"object": {"sha": "start"}}},
        f"GET {AT}/git/commits/start": {"status": 200, "json": {"tree": {"sha": "base-tree"}}},
        f"POST {AT}/git/trees": {"status": 201, "json": {"sha": "next-tree"}},
        f"POST {AT}/git/commits": {"status": 201, "json": {"sha": "commit"}},
        f"POST {AT}/git/refs": {"status": 201, "json": {}},
        f"POST {AT}/pulls": {"status": 201, "json": {"html_url": "https://github.com/o/r/pull/7", "number": 7, "node_id": "N"}},
        **extra,
    }


def service(responses=None, **settings):
    transport, sink = Replay(responses if responses is not None else github()), MemorySink()
    made = Settings(edit_pr_repos=frozenset({"o/r"}), allowed_origins=frozenset({ORIGIN}), **settings)
    return TestClient(create_app(made, transport=transport, sink=sink), raise_server_exceptions=False), transport, sink


def post(client, fields=None, token=TOKEN, **headers):
    return client.post(ROUTE, json=fields or FIELDS, headers={"Authorization": f"Bearer {token}", **headers})


def sent(transport, method, url):
    return next(json.loads(r.body) for r in transport.sent if (r.method, r.url) == (method, url))


def test_a_proposal_is_one_commit_and_a_pull_request_as_the_person():
    client, transport, sink = service()
    response = post(client, **{"X-Request-Id": "from-the-app"})
    assert response.status_code == 200
    assert response.json() == {"status": "opened", "url": "https://github.com/o/r/pull/7", "number": 7,
                               "branch": "edit/one", "commit": "commit"}
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in transport.sent)
    assert sent(transport, "POST", f"{AT}/git/trees") == {
        "base_tree": "base-tree", "tree": [{"path": "a.txt", "mode": "100644", "type": "blob", "content": "new"}]}
    assert sent(transport, "POST", f"{AT}/git/commits") == {"message": "T\n\nB", "tree": "next-tree", "parents": ["start"]}
    assert sent(transport, "POST", f"{AT}/git/refs") == {"ref": "refs/heads/edit/one", "sha": "commit"}
    assert sent(transport, "POST", f"{AT}/pulls") == {"title": "T", "head": "edit/one", "base": "main", "body": "B"}
    assert {r.plugin for r in sink.records} == {"github.edit-pr"}
    assert {r.request_id for r in sink.records} == {"from-the-app"}
    assert {r.operation for r in sink.records} == {"open-pr", "service-total"}


def test_parent_base_and_message_are_the_callers():
    client, transport, _ = service()
    transport.responses[f"GET {AT}/git/commits/read-here"] = {"status": 200, "json": {"tree": {"sha": "base-tree"}}}
    assert post(client, {**FIELDS, "base": "dev", "parent": "read-here", "message": "M"}).status_code == 200
    assert not any(r.url.endswith("/git/ref/heads/dev") for r in transport.sent)
    assert sent(transport, "POST", f"{AT}/git/commits")["message"] == "M"
    assert sent(transport, "POST", f"{AT}/pulls")["base"] == "dev"


def test_no_change_and_the_same_tree_are_nothing_to_change():
    client, _, _ = service()
    assert post(client, {**FIELDS, "changes": {}}).json() == {"status": "nothing to change"}
    client, transport, _ = service(github(**{f"POST {AT}/git/trees": {"status": 201, "json": {"sha": "base-tree"}}}))
    assert post(client).json() == {"status": "nothing to change"}
    assert not any(r.url.endswith("/pulls") for r in transport.sent)


def test_a_file_that_moved_is_stale_and_writes_nothing():
    contents = f"GET {AT}/contents/a.txt?ref=start"
    client, transport, _ = service(github(**{contents: {"status": 200, "json": {"sha": "moved"}}}))
    assert post(client, {**FIELDS, "expect": {"a.txt": "read"}}).json() == {"status": "stale", "path": "a.txt"}
    assert all(r.method == "GET" for r in transport.sent)
    client, _, _ = service(github(**{contents: {"status": 404, "json": {}}}))
    assert post(client, {**FIELDS, "expect": {"a.txt": None}}).json()["status"] == "opened"


def test_a_file_that_cant_be_read_for_expect_is_refused():
    contents = f"GET {AT}/contents/a.txt?ref=start"
    client, _, _ = service(github(**{contents: {"status": 403, "json": {}}}))
    assert post(client, {**FIELDS, "expect": {"a.txt": "read"}}).status_code == 403


def test_deleting_skips_a_file_that_isnt_there():
    gone = f"GET {AT}/contents/gone.txt?ref=start"
    there = f"GET {AT}/contents/there.txt?ref=start"
    client, transport, _ = service(github(**{gone: {"status": 404, "json": {}}, there: {"status": 200, "json": {"sha": "s"}}}))
    assert post(client, {**FIELDS, "changes": {"gone.txt": None, "there.txt": None}}).json()["status"] == "opened"
    assert sent(transport, "POST", f"{AT}/git/trees")["tree"] == [
        {"path": "there.txt", "mode": "100644", "type": "blob", "sha": None}]
    client, transport, _ = service(github(**{gone: {"status": 404, "json": {}}}))
    assert post(client, {**FIELDS, "changes": {"gone.txt": None}}).json() == {"status": "nothing to change"}


@pytest.mark.parametrize(("answer", "says"), [
    ({"status": 200, "json": {"data": {}}}, "on"),
    ({"status": 200, "json": {"errors": [{"message": "not allowed"}]}}, "not allowed"),
    ({"status": 200, "json": {"errors": [{"message": "Pull request is in clean status"}]}}, "merged"),
    ({"status": 403, "json": {"message": "nope"}}, "HTTP 403: nope"),
])
def test_auto_merge_says_what_happened_and_the_pull_request_stands(answer, says):
    client, _, _ = service(github(**{f"POST {API}/graphql": answer, f"PUT {AT}/pulls/7/merge": {"status": 200, "json": {}}}))
    result = post(client, {**FIELDS, "autoMerge": True}).json()
    assert result["status"] == "opened" and result["autoMerge"] == says


def test_a_repository_not_named_is_refused_before_any_call():
    client, transport, _ = service()
    response = post(client, {**FIELDS, "repo": "other/repo"})
    assert response.status_code == 403 and "not served" in response.json()["detail"]
    assert not transport.sent


def test_with_nothing_named_nothing_is_served(monkeypatch):
    monkeypatch.delenv("INTEGRATIONS_EDIT_PR_REPOS", raising=False)
    client = TestClient(create_app(transport=Replay(github()), sink=MemorySink()))
    assert post(client).status_code == 403


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_EDIT_PR_REPOS", "o/r, a/b")
    monkeypatch.setenv("INTEGRATIONS_ORIGINS", ORIGIN)
    monkeypatch.setenv("INTEGRATIONS_EDIT_PR_PER_MINUTE", "5")
    monkeypatch.setenv("INTEGRATIONS_EDIT_PR_TIMINGS_PER_MINUTE", "7")
    made = Settings.from_env()
    assert made.edit_pr_repos == {"o/r", "a/b"} and made.allowed_origins == {ORIGIN} and made.edit_pr_per_minute == 5
    assert made.edit_pr_timings_per_minute == 7


def test_no_token_is_refused():
    client, transport, _ = service()
    assert client.post(ROUTE, json=FIELDS).status_code in (401, 403)
    assert not transport.sent


@pytest.mark.parametrize(("answer", "code"), [
    ({"status": 401, "json": {"message": "Bad credentials"}}, 401),
    ({"status": 404, "json": {"message": "Not Found"}}, 404),
    ({"status": 422, "json": {"message": "Reference already exists"}}, 422),
])
def test_githubs_refusal_comes_back_with_its_words(answer, code):
    client, _, _ = service(github(**{f"POST {AT}/git/refs": answer}))
    response = post(client)
    assert response.status_code == code and answer["json"]["message"] in response.json()["detail"]


def test_a_refusal_with_no_json_still_says_its_status():
    client, _, _ = service(github(**{f"GET {AT}": {"status": 403, "json": None}}))
    response = post(client)
    assert response.status_code == 403


def test_github_not_answering_is_a_503_and_a_bad_answer_a_502(monkeypatch):
    client, _, _ = service(github(**{f"GET {AT}": {"error": "no answer"}}))
    assert post(client).status_code == 503
    client, _, _ = service()

    def fails(self, **kwargs):
        raise calls.IntegrationError("github.edit-pr", "open-pr", "odd")

    monkeypatch.setattr(OneCommitProposer, "propose", fails)
    assert post(client).status_code == 502


@pytest.mark.parametrize("answers", [
    github(),
    github(**{f"POST {AT}/git/refs": {"status": 401, "json": {"message": "Bad credentials"}}}),
    github(**{f"GET {AT}": {"error": "no answer"}}),
    {},
])
def test_the_token_is_never_in_a_record_a_log_or_an_answer(answers, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    client, _, sink = service(answers)
    response = post(client)
    invalid = client.post(ROUTE, json={"repo": "o/r"}, headers={"Authorization": f"Bearer {TOKEN}"})
    out = capsys.readouterr()
    everything = "".join([response.text, invalid.text, json.dumps(dict(response.headers)), out.out, out.err,
                          caplog.text, json.dumps([r.__dict__ for r in sink.records])])
    assert TOKEN not in everything


def test_the_service_keeps_no_token():
    client, _, _ = service()
    post(client)
    assert not hasattr(client.app.state, "token")
    assert TOKEN not in repr(vars(client.app.state))


def test_a_stdout_record_holds_no_token(capsys):
    transport = Replay(github())
    made = Settings(edit_pr_repos=frozenset({"o/r"}))
    post(TestClient(create_app(made, transport=transport)))
    printed = capsys.readouterr().out
    assert '"plugin": "github.edit-pr"' in printed and TOKEN not in printed


def test_cors_lets_in_only_the_named_sites():
    client, _, _ = service()
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    allowed = client.options(ROUTE, headers={"Origin": ORIGIN, **preflight})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    refused = client.options(ROUTE, headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in refused.headers


def test_the_rate_limit_is_per_minute_and_refuses_before_any_call():
    client, transport, _ = service(edit_pr_per_minute=1)
    assert post(client).status_code == 200
    before = len(transport.sent)
    assert post(client).status_code == 429
    assert len(transport.sent) == before


def test_a_rate_limit_lets_calls_in_again_after_its_window():
    now = [0.0]
    limit = RateLimit(1, 60, lambda: now[0])
    assert limit.allow() and not limit.allow()
    now[0] = 61
    assert limit.allow()


def test_a_body_too_large_is_refused_before_it_is_read():
    client, transport, _ = service(max_body_bytes=10)
    assert post(client).status_code == 413
    assert not transport.sent


def test_a_bad_proposal_is_a_validation_error():
    client, transport, _ = service()
    assert post(client, {**FIELDS, "repo": "../../etc"}).status_code == 422
    assert not transport.sent


def test_the_address_alone_leads_to_the_docs():
    response = service()[0].get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/docs/"


def test_a_projects_calls_are_recorded_as_that_projects():
    client, _, sink = service()
    post(client)
    assert {r.repo for r in sink.records} == {"o/r"}
    client, _, sink = service(repository())
    client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH)
    client.post(FILES, json={"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}, headers=AUTH)
    assert {r.repo for r in sink.records} == {"o/r"}


def test_healthz_needs_nothing():
    client, _, _ = service()
    assert client.get("/healthz").json() == {"status": "ok", "unhealthy": [], "alerts": []}
    assert client.get("/livez").json() == {"status": "ok"}


def test_a_buffer_it_cannot_reach_only_alerts():
    from integrations.calls import Check
    failing = lambda: [Check("storage.buffer", False, "no token")]  # noqa: E731
    client = TestClient(create_app(Settings(), sink=MemorySink(), checks=failing))
    answer = client.get("/healthz")
    assert answer.status_code == 200
    assert answer.json() == {"status": "ok", "unhealthy": [], "alerts": ["storage.buffer"]}


def test_the_service_checks_its_storage_token(tmp_path, monkeypatch):
    from integrations.service import main
    token = tmp_path / "token"
    monkeypatch.setenv("INTEGRATIONS_STORAGE_URL", "https://storage.example")
    monkeypatch.setenv("INTEGRATIONS_STORAGE_TOKEN_FILE", str(token))
    assert [c.ok for c in main.checks()] == [False]
    token.write_text("t\n")
    assert [c.ok for c in main.checks()] == [True]
    monkeypatch.delenv("INTEGRATIONS_STORAGE_URL")
    assert main.checks() == []


def test_the_built_docs_are_served_and_the_interactive_ones_are_off(tmp_path):
    (tmp_path / "index.html").write_text("<title>the docs</title>")
    client = TestClient(create_app(Settings(docs_dir=str(tmp_path)), sink=MemorySink()))
    assert "the docs" in client.get("/docs/").text
    assert client.get("/redoc").status_code == 404
    assert service()[0].get("/docs").status_code == 404


SNAPSHOT = "/v1/github/edit-pr/snapshot"
FILES = "/v1/github/edit-pr/files"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def repository(**extra):
    """What GitHub answers about a repository, and its files at a commit."""
    return {
        f"GET {AT}": {"status": 200, "json": {"default_branch": "main", "permissions": {"push": True}}},
        f"GET {AT}/git/ref/heads/main": {"status": 200, "json": {"object": {"sha": "tip"}}},
        f"GET {AT}/git/trees/tip?recursive=1": {"status": 200, "json": {"tree": [
            {"path": "a.txt", "type": "blob"}, {"path": "dir", "type": "tree"}, {"path": "dir/b.txt", "type": "blob"}]}},
        f"GET {AT}/contents/a.txt?ref=tip": {"status": 200, "text": "text of a"},
        f"GET {AT}/contents/dir/b%C3%A4.txt?ref=tip": {"status": 200, "text": "text of b\u00e4"},
        f"GET {AT}/contents/gone.txt?ref=tip": {"status": 404, "json": {}},
        **extra,
    }


def test_a_snapshot_is_the_newest_commit_its_paths_and_whether_the_token_may_write():
    client, transport, sink = service(repository())
    response = client.get(SNAPSHOT, params={"repo": "o/r"}, headers={**AUTH, "X-Request-Id": "from-the-app"})
    assert response.json() == {"branch": "main", "commit": "tip", "can_push": True,
                               "paths": ["a.txt", "dir/b.txt"], "truncated": False}
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in transport.sent)
    assert {(r.plugin, r.operation, r.request_id) for r in sink.records} == {
        ("github.edit-pr", "read", "from-the-app"), ("github.edit-pr", "service-total", "from-the-app")}


def test_a_snapshot_says_when_the_token_may_not_write_and_when_github_left_paths_out():
    client, _, _ = service(repository(**{
        f"GET {AT}": {"status": 200, "json": {"default_branch": "main", "permissions": {"push": False}}},
        f"GET {AT}/git/trees/tip?recursive=1": {"status": 200, "json": {"tree": [], "truncated": True}}}))
    answer = client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH).json()
    assert answer["can_push"] is False and answer["truncated"] is True


def test_a_snapshot_can_be_of_another_branch():
    client, transport, _ = service(repository(**{
        f"GET {AT}/git/ref/heads/dev": {"status": 200, "json": {"object": {"sha": "tip"}}}}))
    assert client.get(SNAPSHOT, params={"repo": "o/r", "branch": "dev"}, headers=AUTH).json()["branch"] == "dev"
    assert not any(r.url.endswith("/heads/main") for r in transport.sent)


def test_files_come_in_one_call_with_null_for_one_that_isnt_there():
    client, transport, sink = service(repository())
    response = client.post(FILES, json={"repo": "o/r", "commit": "tip", "paths": ["a.txt", "dir/b\u00e4.txt", "gone.txt"]},
                           headers=AUTH)
    assert response.json() == {"files": {"a.txt": "text of a", "dir/b\u00e4.txt": "text of b\u00e4", "gone.txt": None}}
    assert all(r.headers["Accept"] == "application/vnd.github.raw+json" for r in transport.sent)
    assert len([r for r in sink.records if r.operation == "read"]) == 3


def test_a_file_github_refuses_refuses_the_call():
    client, _, _ = service(repository(**{f"GET {AT}/contents/a.txt?ref=tip": {"status": 403, "json": {"message": "no"}}}))
    response = client.post(FILES, json={"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}, headers=AUTH)
    assert response.status_code == 403 and "no" in response.json()["detail"]


def test_too_many_files_is_a_validation_error():
    client, transport, _ = service(repository())
    response = client.post(FILES, json={"repo": "o/r", "commit": "tip", "paths": ["a"] * 2001}, headers=AUTH)
    assert response.status_code == 422 and not transport.sent


@pytest.mark.parametrize(("call", "kwargs"), [
    ("get", {"url": SNAPSHOT, "params": {"repo": "other/repo"}}),
    ("post", {"url": FILES, "json": {"repo": "other/repo", "commit": "tip", "paths": ["a"]}}),
])
def test_reads_serve_only_the_repositories_named_and_need_a_token(call, kwargs):
    client, transport, _ = service(repository())
    assert getattr(client, call)(**kwargs, headers=AUTH).status_code == 403
    assert getattr(client, call)(**kwargs).status_code in (401, 403)
    assert not transport.sent


def test_reads_have_their_own_rate_limit():
    client, _, _ = service(repository(), edit_pr_reads_per_minute=1)
    assert client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH).status_code == 200
    assert client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH).status_code == 429
    assert post(client).status_code != 429


@pytest.mark.parametrize("answers", [
    repository(),
    repository(**{f"GET {AT}": {"status": 401, "json": {"message": "Bad credentials"}}}),
    repository(**{f"GET {AT}/contents/a.txt?ref=tip": {"error": "no answer"}}),
])
def test_reads_keep_the_token_out_of_records_logs_and_answers(answers, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    client, _, sink = service(answers)
    snapshot = client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH)
    files = client.post(FILES, json={"repo": "o/r", "commit": "tip", "paths": ["a.txt"]}, headers=AUTH)
    out = capsys.readouterr()
    everything = "".join([snapshot.text, files.text, out.out, out.err, caplog.text,
                          json.dumps([r.__dict__ for r in sink.records])])
    assert TOKEN not in everything


def test_cors_lets_the_named_sites_read():
    client, _, _ = service()
    allowed = client.options(SNAPSHOT, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                                "Access-Control-Request-Headers": "authorization"})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN


def test_every_call_of_one_request_shares_its_id_and_the_answer_says_it():
    client, _, sink = service()
    response = post(client)
    made = response.headers["x-request-id"]
    assert {r.request_id for r in sink.records} == {made}
    client, _, sink = service(repository())
    response = client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH)
    assert {r.request_id for r in sink.records} == {response.headers["x-request-id"]}


def test_an_apps_own_id_is_kept_unless_it_isnt_one():
    client, _, sink = service()
    assert post(client, **{"X-Request-Id": "from-the-app"}).headers["x-request-id"] == "from-the-app"
    assert {r.request_id for r in sink.records} == {"from-the-app"}
    client, _, sink = service()
    made = post(client, **{"X-Request-Id": "not one\tat all"}).headers["x-request-id"]
    assert made != "not one\tat all" and {r.request_id for r in sink.records} == {made}


def test_a_request_records_its_whole_time_as_well_as_its_calls():
    client, _, sink = service()
    post(client)
    totals = [r for r in sink.records if r.operation == "service-total"]
    assert len(totals) == 1 and totals[0].route == "none" and totals[0].status == 200 and totals[0].outcome == "ok"
    assert totals[0].repo == "o/r" and totals[0].latency_ms >= 0


@pytest.mark.parametrize(("answers", "status", "outcome"), [
    (github(**{f"POST {AT}/git/refs": {"status": 422, "json": {"message": "exists"}}}), 422, "refused"),
    (github(**{f"GET {AT}": {"error": "no answer"}}), 503, "unavailable"),
])
def test_a_failed_request_records_how_it_ended(answers, status, outcome):
    client, _, sink = service(answers)
    post(client)
    total = next(r for r in sink.records if r.operation == "service-total")
    assert (total.status, total.outcome) == (status, outcome)


TIMINGS = "/v1/github/edit-pr/timings"


def test_an_apps_timings_become_records_with_no_token_needed():
    client, _, sink = service()
    response = client.post(TIMINGS, json={"repo": "o/r", "timings": [{"request_id": "a-1", "total_ms": 120}, {"request_id": "b-2", "total_ms": 80}]})
    assert response.status_code == 202 and response.json() == {"accepted": 2}
    assert [(r.operation, r.request_id, r.latency_ms, r.repo) for r in sink.records] == [
        ("client-total", "a-1", 120, "o/r"), ("client-total", "b-2", 80, "o/r")]


@pytest.mark.parametrize("body", [
    {"repo": "o/r", "timings": [{"request_id": "bad id!", "total_ms": 1}]},
    {"repo": "o/r", "timings": [{"request_id": "a", "total_ms": -1}]},
    {"repo": "o/r", "timings": [{"request_id": "a", "total_ms": 3_600_001}]},
    {"repo": "o/r", "timings": [{"request_id": "a", "total_ms": 1}] * 201},
])
def test_timings_are_only_ids_and_sensible_numbers(body):
    client, _, sink = service()
    assert client.post(TIMINGS, json=body).status_code == 422
    assert not sink.records


def test_timings_serve_only_the_repositories_named_and_are_rate_limited():
    client, _, sink = service()
    assert client.post(TIMINGS, json={"repo": "other/repo", "timings": []}).status_code == 403
    client, _, _ = service(edit_pr_timings_per_minute=1)
    assert client.post(TIMINGS, json={"repo": "o/r", "timings": []}).status_code == 202
    assert client.post(TIMINGS, json={"repo": "o/r", "timings": []}).status_code == 429


def test_timings_with_no_token_cant_use_up_what_reads_and_proposals_need():
    client, _, _ = service(repository(), edit_pr_timings_per_minute=1, edit_pr_reads_per_minute=1, edit_pr_per_minute=1)
    assert client.post(TIMINGS, json={"repo": "o/r", "timings": []}).status_code == 202
    assert client.post(TIMINGS, json={"repo": "o/r", "timings": []}).status_code == 429
    assert client.get(SNAPSHOT, params={"repo": "o/r"}, headers=AUTH).status_code == 200
    assert post(client).status_code != 429


def test_the_committed_spec_is_what_the_service_gives():
    committed = (Path(__file__).parents[2] / "openapi" / "integrations.json").read_text()
    assert committed == spec_text(), "run: PYTHONPATH=src python -m integrations.service.spec > openapi/integrations.json"


def test_records_go_to_storage_only_when_a_deployment_names_it(monkeypatch, tmp_path):
    monkeypatch.delenv("INTEGRATIONS_STORAGE_URL", raising=False)
    assert isinstance(main.sink(), calls.StdoutSink)
    token = tmp_path / "token"
    token.write_text("pod-token\n")
    monkeypatch.setenv("INTEGRATIONS_STORAGE_URL", "https://storage.example/")
    monkeypatch.setenv("INTEGRATIONS_STORAGE_TOKEN_FILE", str(token))
    monkeypatch.setenv("INGEST_DATASETS", '{"o/r": "calls_r"}')
    monkeypatch.setenv("HOSTNAME", "app-1")
    import integrations.storage_sink as storage_sink
    monkeypatch.setattr(storage_sink.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(storage_sink.atexit, "register", lambda f: None)
    made = main.sink()
    assert isinstance(made, storage_sink.StorageSink)
    assert made.base == "https://storage.example" and made.sender == "app-1" and made.token() == "pod-token"
    assert made.datasets == {"o/r": "calls_r"}
