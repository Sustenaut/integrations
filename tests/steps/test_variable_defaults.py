"""Steps for the ingest pod's variable defaults: pubrules.json's against the code's constants.

The defaults are the code's, never an org's value (pubrules' README, "variables"), so a
drift between pubrules.json and inbox.py's constants would have the deploy walk call a
variable ready at a value the pod doesn't take. These read both, and what the code makes
of the variable unset or empty, as deploy.yml renders an unset one.
"""

from __future__ import annotations

import json
from pathlib import Path

from integrations.ingest.inbox import GAP_SECONDS, INTERVAL_SECONDS
from integrations.ingest.main import seconds
from pubrules.variables import block_problems
from pytest_bdd import scenarios, when

scenarios("variable-defaults.feature")

ROOT = Path(__file__).resolve().parents[2]

# Each variable and the constant main.py's build() falls back to without it.
CONSTANTS = {
    "INGEST_SWEEP_SECONDS": INTERVAL_SECONDS,
    "INGEST_REPORT_SECONDS": INTERVAL_SECONDS,
    "INGEST_BATCH_GAP_SECONDS": GAP_SECONDS,
}


def block() -> dict:
    return json.loads((ROOT / "pubrules.json").read_text(encoding="utf-8"))["variables"]


def as_given(value: float) -> str:
    """A number of seconds as a GitHub variable holds it."""
    return str(int(value)) if value == int(value) else str(value)


@when("I read the defaults pubrules.json gives and what the code does without the variables")
def read_defaults(ctx):
    def facts():
        defaults = block().get("defaults", {})
        deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        return {name: {"default": defaults.get(name),
                       "is the constant": defaults.get(name) == as_given(constant),
                       "unset gives": seconds(name, None, constant),
                       "empty gives": seconds(name, "", constant),
                       "deploy.yml renders it": f'"{name}=$(var {name})"' in deploy}
                for name, constant in CONSTANTS.items()}
    ctx.attempt(facts)


@when("I read pubrules.json's variables block")
def read_block(ctx):
    def facts():
        found = block()
        defaulted = sorted(found.get("defaults", {}))
        return {"version": found.get("version"), "defaulted": defaulted,
                "every defaulted name is declared": set(defaulted) <= set(found["names"]),
                "problems": block_problems(found)}
    ctx.attempt(facts)
