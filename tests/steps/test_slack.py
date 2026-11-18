"""The Slack clients, against a fake Slack (providers.py); their fakes, in memory."""

from __future__ import annotations

import shlex
from dataclasses import asdict
from urllib.parse import parse_qs

from pytest_bdd import given, parsers, scenarios, then, when

from integrations import cli
from integrations.calls import Caller
from integrations.clients.slack import FakeSlackChannels, FakeSlackPost, SlackChannels, SlackPost

from .providers import caller, providers

scenarios("slack.feature")


@given(parsers.parse('the bot\'s Slack token "{bot}" and the owner\'s "{owner}"'))
def tokens(ctx, bot, owner):
    ctx.bot, ctx.owner = bot, owner


def channels(ctx) -> SlackChannels:
    return SlackChannels(caller(ctx), lambda: ctx.owner, lambda: ctx.bot)


@when(parsers.parse('the bot posts "{text}" to "{channel}"'))
def bot_posts(ctx, text, channel):
    ctx.attempt(lambda: SlackPost(caller(ctx), lambda: ctx.bot).post(channel, text))


@when("the post client is checked")
def post_checked(ctx):
    ctx.attempt(lambda: asdict(SlackPost(caller(ctx), lambda: ctx.bot).check()))


@when("the channels client is checked")
def channels_checked(ctx):
    ctx.attempt(lambda: asdict(channels(ctx).check()))


@when("the channels are listed")
def listed(ctx):
    ctx.attempt(lambda: [asdict(c) for c in channels(ctx).channels()])


@when(parsers.parse('the channel "{name}" is created'))
def created(ctx, name):
    ctx.attempt(lambda: asdict(channels(ctx).create(name)))


@when(parsers.parse('the channels client calls "{method}" with "{args}"'))
def channels_call(ctx, method, args):
    ctx.attempt(lambda: getattr(channels(ctx), method)(*args.split(",")))


@then(parsers.parse('the "{name}" service was asked "{request}" with the form:'))
def asked_with_form(ctx, name, request, datatable):
    """Some such request's form body was exactly these fields."""
    want = {field: value for field, value in datatable[1:]}
    method, path = request.split(" ", 1)
    forms = [{k: v[0] for k, v in parse_qs(r["body"], keep_blank_values=True).items()}
             for r in ctx.fakes[name].requests if (r["method"], r["path"]) == (method, path)]
    assert want in forms, f"{name} was asked {request} with {forms}"


# --- the fakes -------------------------------------------------------------------

@when(parsers.parse('the post fake posts "{text}" to "{channel}"'))
def post_fake_posts(ctx, text, channel):
    fake = FakeSlackPost()

    def post():
        fake.post(channel, text)
        return {"posted": [list(p) for p in fake.posted], "check": asdict(fake.check())}
    ctx.attempt(post)


@when(parsers.parse('a post fake set to fail posts "{text}" to "{channel}"'))
def broken_post_fake_posts(ctx, text, channel):
    ctx.attempt(lambda: FakeSlackPost(working=False).post(channel, text))


@when(parsers.parse("the {fake} fake that is {state} is checked"))
def fake_checked(ctx, fake, state):
    made = (FakeSlackPost if fake == "post" else FakeSlackChannels)(working=state == "working")
    ctx.attempt(lambda: asdict(made.check()))


@when(parsers.parse('the channels fake creates "{name}", renames it "{new}", sets its purpose "{why}" and archives it'))
def fake_changes(ctx, name, new, why):
    fake = FakeSlackChannels()

    def change():
        made = fake.create(name)
        fake.rename(made.id, new)
        fake.set_purpose(made.id, why)
        fake.archive(made.id)
        return [asdict(c) for c in fake.channels()]
    ctx.attempt(change)


@given("the channels fake has an archived channel")
def fake_archived(ctx):
    ctx.fake = FakeSlackChannels()
    ctx.made = ctx.fake.create("one")
    ctx.fake.archive(ctx.made.id)


@when("the channels fake unarchives it")
def fake_unarchives(ctx):
    def unarchive():
        ctx.fake.unarchive(ctx.made.id)
        return [asdict(c) for c in ctx.fake.channels()]
    ctx.attempt(unarchive)


@given(parsers.parse('the channels fake knows the token "{token}" as "{user}"'))
def fake_knows(ctx, token, user):
    ctx.fake = FakeSlackChannels(users={token: user})


@when(parsers.parse('the channels fake is asked whose the token "{token}" is'))
def fake_whose(ctx, token):
    ctx.attempt(lambda: ctx.fake.user_id(lambda: token))


# --- the command -----------------------------------------------------------------

@when(parsers.parse('the command line "{line}" runs'))
def command_runs(ctx, monkeypatch, line):
    """`integrations LINE`, in this process, its calls going to the fake providers."""
    monkeypatch.setattr(cli, "Caller", lambda repo, app: Caller(repo, app, transport=providers(ctx)))

    def run():
        raise SystemExit(cli.main(shlex.split(line)))
    ctx.attempt(run)
