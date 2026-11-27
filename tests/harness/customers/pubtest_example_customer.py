"""A test customer for pubtest's own suite, found as a package's would be: by
its `pubtest.customers` entry point (the dist-info beside it, on the suite's
pythonpath). It names one repository, made up while recording."""

from pytest_bdd import given

from pubtest.steps import phrase

name = "pubtest.example"
made: list[str] = []


def setup(tape):
    """Record mode only: the real names this scenario's logical ones stand for."""
    made.append("pubtest-example-1")
    return {"the repository": "pubtest-example-1"}


def cleanup(tape, names):
    """Record mode only, after the scenario, passed or failed."""
    made.remove(names["the repository"])


@given(phrase('the example customer\'s repository "{logical}"'))
def example_repository(pubtest_ctx, logical):
    names = pubtest_ctx.tape.names_of(name)
    assert logical in names, f"the example customer has no {logical}: {names}"
