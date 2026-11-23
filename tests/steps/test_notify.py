"""Storage's notices to the ingest pod: the app's POST /ingest/notify, with a scanner
that only notes what it was handed, and the k8s wiring that brings a notice there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import pubtest.http
from integrations.ingest.app import create_app
from integrations.ingest.inbox import CALLS, KINDS
from integrations.ingest.notify import Notices, signature
from pubtest.steps.http import send
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("notify.feature")

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000.0
BUFFERS = {"calls_visit_ee": ("calls-visit-ee", CALLS), "platform.ci_jobs": ("ci-usage", KINDS["ci_jobs"]),
           "platform.cluster_usage": ("ci-usage", KINDS["cluster_usage"])}


@pytest.fixture
def http_client(ctx):
    """The ingest pod's app, its notices checked with the key a Given set (none: unset)."""
    ctx.handed = getattr(ctx, "handed", [])

    def send_(method, path, headers, body):
        notices = None if getattr(ctx, "no_notices", False) else Notices(
            getattr(ctx, "notify_key", ""), BUFFERS, lambda *taken: ctx.handed.append(list(taken)), lambda: NOW)
        return pubtest.http.asgi(create_app(list, notices))(method, path, headers, body)
    return send_


@given(parsers.parse('the pod\'s notify key is "{key}"'))
def notify_key(ctx, key):
    ctx.notify_key = key


@given("the pod has no notices")
def no_notices(ctx):
    ctx.no_notices = True


def signed(ctx, http_client, key: str, moment: str, body: bytes, sent: bytes | None = None):
    ctx.headers = {"X-Ingest-Timestamp": moment, "X-Ingest-Signature": signature(key, moment, body)}
    send(ctx, http_client, "POST", "/ingest/notify", body if sent is None else sent, "application/json")


@when(parsers.parse('storage sends, signed with "{key}" at the moment {moment:d}:'))
def storage_sends(ctx, http_client, key, moment, docstring):
    signed(ctx, http_client, key, str(moment), docstring.encode())


@when(parsers.parse('storage sends a body other than the one it signed with "{key}"'))
def storage_sends_changed(ctx, http_client, key):
    body = b'{"tenant": "calls-visit-ee", "name": "inbox/calls/a.avro", "size": 1}'
    signed(ctx, http_client, key, str(int(NOW)), body, body.replace(b"1}", b"2}"))


@then("the scanner was handed:")
def handed(ctx, docstring):
    assert ctx.handed == json.loads(docstring), f"it was handed {ctx.handed}"


@then("the scanner was handed nothing")
def handed_nothing(ctx):
    assert getattr(ctx, "handed", []) == [], f"it was handed {ctx.handed}"


@when("I read how a notice reaches the ingest pod in k8s/ and pubrules.json")
def read_wiring(ctx):
    def facts():
        def documents(name):
            return [d for d in yaml.safe_load_all((ROOT / "k8s" / name).read_text(encoding="utf-8")) if d]
        ingest = documents("ingest.yaml")
        service = next(d for d in ingest if d["kind"] == "Service")
        deployment = next(d for d in ingest if d["kind"] == "Deployment")
        (rule,) = documents("ingress.yaml")[0]["spec"]["rules"]
        (path,) = [p for p in rule["http"]["paths"] if p["path"] == "/ingest"]
        (policy,) = documents("admin/network-policy.yaml")
        admitted = policy["spec"]["ingress"][0]["from"]
        (container,) = deployment["spec"]["template"]["spec"]["containers"]
        env = {e["name"]: e for e in container["env"]}
        needs = json.loads((ROOT / "pubrules.json").read_text(encoding="utf-8"))["deploy"]["needs"]
        (need,) = [n for n in needs if n.get("name") == "ingest-notify-key"]
        return {
            "ingress path": path["path"], "ingress service": path["backend"]["service"]["name"],
            "service selects the pod": service["spec"]["selector"] == deployment["spec"]["selector"]["matchLabels"],
            "service port": path["backend"]["service"]["port"]["name"],
            "the policy admits Traefik": {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                                          "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}}} in admitted,
            "the policy admits the service": {"podSelector": {"matchLabels": {"app": "integrations"}}} in admitted,
            "INGEST_NOTIFY_KEY": env["INGEST_NOTIFY_KEY"]["valueFrom"]["secretKeyRef"],
            "declared secret": need["secret"],
        }
    ctx.attempt(facts)
