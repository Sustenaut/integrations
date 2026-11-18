"""The suite's shared steps: a provider that does not answer, what every
request to a fake provider carried, and the call records a caller kept
(providers.py)."""

from __future__ import annotations

from dataclasses import asdict

import jsonschema
from integrations import registry
from pubtest.core import dig, same, show
from pytest_bdd import given, parsers, then

from .providers import providers, records

RECORD_SCHEMA = registry.load(registry.SCHEMAS / "call-record.schema.json")


@given(parsers.parse('"{host}" does not answer'))
def does_not_answer(ctx, host):
    providers(ctx).silent.add(host)


@then("the calls recorded are:")
def calls_recorded(ctx, datatable):
    """A row per record, in order, the header row naming the fields; each cell
    is compared as pubtest compares a step's value."""
    kept = records(ctx).records
    keys, rows = datatable[0], datatable[1:]
    assert len(kept) == len(rows), f"{len(kept)} calls were recorded: {show([r.__dict__ for r in kept])}"
    for record, row in zip(kept, rows, strict=True):
        for key, cell in zip(keys, row, strict=True):
            assert same(dig(record, key), cell), f"a record's {key} is {dig(record, key)!r}, not {cell}"


@then("no call was recorded")
def no_call_recorded(ctx):
    kept = records(ctx).records
    assert not kept, f"recorded: {show([r.__dict__ for r in kept])}"


@then(parsers.parse('every request to the "{name}" service carried the header "{header}" set to "{value}"'))
def every_request_carried(ctx, name, header, value):
    """pubtest's `was asked … with the header` holds when some request carried
    it; this, when every one did."""
    requests = getattr(ctx, "fakes", {})[name].requests
    assert requests, f"{name} was asked nothing"
    seen = [{k.lower(): v for k, v in r["headers"].items()}.get(header.lower()) for r in requests]
    assert all(v == value for v in seen), f"{name} was asked with {header} {seen}"


@then("each call record meets its schema")
def records_meet_schema(ctx):
    kept = records(ctx).records
    assert kept, "no call was recorded"
    for record in kept:
        jsonschema.validate(asdict(record), RECORD_SCHEMA)
