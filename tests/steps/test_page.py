"""The call log's page, with BigQuery as a fake."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from integrations.calls import Caller, Response
from integrations.page import main  # noqa: F401 - what uvicorn runs must import
from integrations.page.app import create_app
from integrations.page.queries import CallLog, Step, split


def bq_rows(*rows):
    return json.dumps({"rows": [{"f": [{"v": v} for v in row]} for row in rows]}).encode()


class FakeBigQuery:
    def __init__(self, *rows, status=200):
        self.rows, self.status = rows, status
        self.asked = []

    def send(self, request, timeout):
        self.asked.append(json.loads(request.body))
        if self.status is None:
            return Response(None, error="no answer")
        return Response(self.status, bq_rows(*self.rows))


def page(*rows, status=200, base=""):
    fake = FakeBigQuery(*rows, status=status)
    log = CallLog(Caller("integrations", "page", transport=fake, sleep=lambda _: None), lambda: "t", "proj",
                  ["calls_one", "calls_two"])
    return TestClient(create_app(log, base), raise_server_exceptions=False, follow_redirects=False), fake


def step(operation, ms, status=200):
    return ["2026-11-02T10:00:00Z", "calls_one", "github.edit-pr", operation, str(ms), None if status is None else str(status), "ok", "0"]


def test_it_shows_each_plugins_latency_and_errors_beside_its_target():
    client, fake = page(["calls_one", "github.edit-pr", "open-pr", "10", "120", "900", "1"])
    body = client.get("/").text
    for text in ("calls_one", "github.edit-pr", "open-pr", "2 s to acknowledge", "120", "900", "10.0%"):
        assert text in body
    sql = fake.asked[0]["query"]
    assert "`proj.calls_one.calls`" in sql and "`proj.calls_two.calls`" in sql and "INTERVAL 7 DAY" in sql


def test_with_no_calls_it_says_so():
    assert "No calls yet" in page()[0].get("/").text


def test_a_request_is_split_into_provider_service_and_app_time():
    client, fake = page(step("open-pr", 300), step("open-pr", 200), step("service-total", 600), step("client-total", 700, None))
    body = client.get("/trace", params={"id": "abc-1"}).text
    assert "Provider: 500 ms" in body and "Service: 100 ms" in body and "App: 100 ms" in body
    assert fake.asked[0]["queryParameters"][0]["parameterValue"]["value"] == "abc-1"
    assert "abc-1" not in fake.asked[0]["query"]


def test_an_app_time_that_hasnt_arrived_says_not_yet():
    parts = split([Step("t", "d", "p", "open-pr", 5, 200, "ok", 0), Step("t", "d", "p", "service-total", 9, 200, "ok", 0)])
    assert (parts.provider_ms, parts.service_ms, parts.app_ms) == (5, 4, None)
    client, _ = page(step("open-pr", 5), step("service-total", 9))
    assert "App: not yet" in client.get("/trace", params={"id": "a"}).text


def test_calls_that_ran_at_once_say_so_rather_than_a_negative_time():
    client, _ = page(step("read", 300), step("read", 300), step("service-total", 350))
    body = client.get("/trace", params={"id": "a"}).text
    assert "Service: 0 ms" in body and "ran at once" in body


def test_an_unknown_request_says_records_take_time():
    assert "take a few minutes" in page()[0].get("/trace", params={"id": "a"}).text


@pytest.mark.parametrize("bad", ["<script>", "a b", "x" * 65, "a'; DROP"])
def test_a_request_id_is_checked_before_any_query(bad):
    client, fake = page()
    assert client.get("/trace", params={"id": bad}).status_code == 422
    assert not fake.asked


def test_what_comes_from_the_log_is_escaped():
    client, _ = page(["calls_one", "<b>plugin", "<i>op", "1", "1", "1", "0"])
    body = client.get("/").text
    assert "<b>plugin" not in body and "&lt;b&gt;plugin" in body


@pytest.mark.parametrize("status", [None, 500, 403])
def test_bigquery_not_answering_is_a_503(status):
    client, _ = page(status=status)
    assert client.get("/").status_code == 503
    assert client.get("/trace", params={"id": "a"}).status_code == 503


def test_a_dataset_name_is_checked_as_it_goes_into_the_query():
    with pytest.raises(ValueError, match="not dataset names"):
        CallLog(Caller("r"), lambda: "t", "p", ["calls`; DROP"])


def test_the_page_loads_nothing_and_isnt_cached():
    client, _ = page()
    response = client.get("/")
    assert response.headers["cache-control"] == "private, no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "<script" not in response.text


def test_health_needs_no_query():
    client, fake = page()
    assert client.get("/healthz").json() == {"status": "ok"} and not fake.asked
    assert client.get("/docs").status_code == 404


def test_under_a_base_path_its_links_and_routes_are_there():
    client, _ = page(base="/logging")
    assert client.get("/logging").headers["location"] == "/logging/"
    assert 'action="/logging/trace"' in client.get("/logging/").text
    assert "take a few minutes" in client.get("/logging/trace", params={"id": "a"}).text
    assert client.get("/").status_code == 404 and client.get("/trace", params={"id": "a"}).status_code == 404
    assert client.get("/healthz").status_code == 200
