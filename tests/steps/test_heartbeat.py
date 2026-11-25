"""Steps for heartbeat.py: a sender's heartbeat in Cloud Monitoring, against a stand-in
for Google that keeps every request it is sent and answers as a Given says."""

from __future__ import annotations

import json
import os
from datetime import datetime

from pytest_bdd import given, parsers, scenarios, then, when

from integrations import heartbeat
from integrations.calls import Response
from integrations.storage_sink import StorageSink

from .stores import BASE

scenarios("heartbeat.feature")

KEY = {"type": "service_account", "project_id": "a-project", "client_email": "beat@a-project.iam.gserviceaccount.com",
       "token_uri": "https://oauth.example/token", "private_key": "unused"}


def reader(files):
    """A file system holding `files`, and nothing else."""
    def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]
    return read


class Google:
    """Monitoring's timeSeries.create, answering `status` (or nothing), and keeping each request."""

    def __init__(self):
        self.requests, self.status = [], 200

    def send(self, request, timeout):
        self.requests.append(request)
        if self.status is None:
            return Response(None, error="no answer")
        return Response(self.status, b"{}")


@given(parsers.parse("the clock reads {moment}"))
def clock_reads(ctx, moment):
    ctx.now = datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp()


@given(parsers.parse('a heartbeat of the instance "{instance}" for the datasets {datasets}'))
def a_heartbeat(ctx, instance, datasets):
    ctx.google = Google()
    ctx.beat = heartbeat.Heartbeat([heartbeat.project_of_dataset(d) for d in json.loads(datasets)], instance, 60,
                                   transport=ctx.google, token=lambda: "a-token", google_project="a-project",
                                   clock=lambda: ctx.now, start=False)


@given(parsers.parse("Monitoring answers {status:d}"))
def answers(ctx, status):
    ctx.google.status = status


@given("Monitoring doesn't answer")
def silent(ctx):
    ctx.google.status = None


@when("it beats")
def beats(ctx):
    ctx.attempt(lambda: ctx.beat.beat())


@when(parsers.parse('the sender prefix of "{instance}" is read'))
def prefix(ctx, instance):
    ctx.attempt(lambda: heartbeat.sender_of(instance))


@when(parsers.parse('the heartbeat is made from the environment, for the datasets {datasets}'))
def from_environment(ctx, datasets):
    files = {"/var/run/google/credential.json": json.dumps(KEY)}
    ctx.attempt(lambda: heartbeat.from_environment(json.loads(datasets), "app-7d9f8c6b5d-x2k4p", Google(), start=False,
                                                   read=reader(files)))


@given("the credential file holds a key")
def credential_file(monkeypatch):
    files = {"/var/run/google/credential.json": json.dumps(KEY)}
    real = heartbeat.credential_from
    monkeypatch.setattr(heartbeat, "credential_from",
                        lambda environ=None, read=None: real(environ or os.environ, reader(files)))


@when("a storage sink is made")
def sink_made(ctx):
    ctx.attempt(lambda: StorageSink({"a-repo": "calls_a_repo"}, transport=Google(), token=lambda: "t", base_url=BASE,
                                    sender="app-7d9f8c6b5d-x2k4p", start=False))


@then("the request is:")
def request_is(ctx, docstring):
    assert ctx.refused is None, ctx.refused
    (request,) = ctx.google.requests
    want = json.loads(docstring)
    got = {"method": request.method, "url": request.url, "authorization": request.headers.get("Authorization"),
           "body": json.loads(request.body)}
    assert got == want, got


@then(parsers.parse("it sent {count:d} requests"))
def sent(ctx, count):
    assert len(ctx.google.requests) == count, ctx.google.requests


@then(parsers.parse("the heartbeat has sent {sent:d} and failed {failed:d}"))
def counted(ctx, sent, failed):
    assert (ctx.beat.sent, ctx.beat.failures) == (sent, failed), (ctx.beat.sent, ctx.beat.failures)


@then(parsers.parse('the sink\'s heartbeat writes for "{projects}" as "{sender}"'))
def sink_heartbeat(ctx, projects, sender):
    assert ctx.refused is None, ctx.refused
    metric = ctx.result.metric
    assert metric is not None and metric.projects == projects.split(",") and metric.sender == sender, metric


@then("the sink has no heartbeat")
def no_heartbeat(ctx):
    assert ctx.refused is None, ctx.refused
    assert ctx.result.metric is None
