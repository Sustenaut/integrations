"""cloudflare.dns's check, against a fake Cloudflare."""

from __future__ import annotations

from .providers import caller
from integrations.clients.cloudflare_dns import CloudflareDns, FakeCloudflareDns
from pytest_bdd import given, parsers, scenarios, when

scenarios("cloudflare-dns.feature")


@given(parsers.parse('the Cloudflare token "{token}"'))
def cloudflare_token(ctx, token):
    ctx.token = token


@when("I check Cloudflare")
def check(ctx):
    ctx.attempt(lambda: CloudflareDns(caller(ctx), lambda: ctx.token).check())


@when(parsers.parse("I check a fake Cloudflare that is {state}"))
def check_fake(ctx, state):
    ctx.attempt(lambda: FakeCloudflareDns(working=state == "working").check())
