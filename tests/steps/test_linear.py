"""The Linear client, against a fake Linear (providers.py); its fake, in memory."""

from __future__ import annotations

import json
from dataclasses import asdict

from pytest_bdd import given, parsers, scenarios, when

from integrations.clients.linear import FakeLinear, LinearClient

from .providers import caller

scenarios("linear.feature")


@given(parsers.parse('the Linear key "{key}"'))
def linear_key(ctx, key):
    ctx.key, ctx.operation = key, "sync"


@given("the Linear client reads for a person")
def reads_for_a_person(ctx):
    ctx.operation = "read"


def client(ctx) -> LinearClient:
    return LinearClient(caller(ctx), lambda: ctx.key, ctx.operation)


@when(parsers.parse('the query "{query}" is sent'))
def query_sent(ctx, query):
    ctx.attempt(lambda: client(ctx).query(query))


@when(parsers.parse('the query "{query}" is sent with the variables:'))
def query_with_variables(ctx, query, docstring):
    ctx.attempt(lambda: client(ctx).query(query, json.loads(docstring)))


@when(parsers.parse('the query "{query}" is sent with the request id "{request_id}"'))
def query_with_id(ctx, query, request_id):
    ctx.attempt(lambda: client(ctx).query(query, request_id=request_id))


@when("the Linear client is checked")
def client_checked(ctx):
    ctx.attempt(lambda: asdict(client(ctx).check()))


@given("a Linear fake that answers:")
def fake_answers(ctx, docstring):
    ctx.fake = FakeLinear(answers=json.loads(docstring))


@given(parsers.parse("a Linear fake that is {state}"))
def fake_set(ctx, state):
    ctx.fake = FakeLinear(working=state == "working")


@when(parsers.parse('the fake is asked "{first}" with {variables} and then "{second}"'))
def fake_asked_twice(ctx, first, variables, second):
    def ask():
        answers = [ctx.fake.query(first, json.loads(variables)), ctx.fake.query(second)]
        return {"answers": answers, "asked": [list(a) for a in ctx.fake.asked]}
    ctx.attempt(ask)


@when(parsers.re(r'the fake is asked "(?P<query>[^"]*)"'))
def fake_asked(ctx, query):
    ctx.attempt(lambda: ctx.fake.query(query))


@when("the Linear fake is checked")
def fake_checked(ctx):
    ctx.attempt(lambda: asdict(ctx.fake.check()))
