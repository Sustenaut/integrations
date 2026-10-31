# integrations

The registry of every outside service our code calls, and the check that keeps
it true. This is phase 1 of the integrations service: there is no runtime yet.

An integration is a use, not a provider. GitHub has several plugins, one for
each way we use it, because each has its own token scope, limits and class.

## The registry

`src/integrations/plugins/<provider>.<use>.json` holds one plugin:

| Field | Meaning |
|---|---|
| `id` | `provider.use`, and the file's name |
| `provider`, `use` | Who, and what for |
| `operations` | Each with its `class` and `route` |
| `setup` | The manual steps, in order |
| `detect` | The signatures that show the plugin is used |

`class` is `H` (human-driven), `E` (event-driven), `S` (scheduled) or `M`
(manual setup). `src/integrations/classes.json` gives each class's response and
uptime targets, once, so no plugin can disagree with them. `route` says whether
the call goes through the service (`routed`), is made by a vendor's own action
and only reported (`reported`), or has nothing to log (`none`).

`detect` can hold `hosts`, `env`, `commands`, `actions`, `terraform_providers`,
`files` and `patterns`. A plugin with none is set up by hand only, and every
operation it has must be class `M`.

The registry names no consumer. Each repository says what it uses, in its own
`integrations.json`, so a private repository's details stay in that repository.

## A repository's integrations.json

```json
{
  "uses": [
    { "plugin": "linear.sync", "paths": ["tools/linear_*.py", "planning/"] }
  ],
  "terraform": [
    { "dir": "infra", "plugins": ["terraform.hetzner"], "runs": ["laptop", "actions"] }
  ],
  "own_hosts": ["example.eu"],
  "other_hosts": [
    { "host": "packages.example.org", "reason": "A download at build time; no account or key" }
  ],
  "other_secrets": [
    { "name": "DEPLOY_KEY", "reason": "An SSH key to our own server" }
  ],
  "exclude": [
    { "path": "tests/fixtures/**", "reason": "Recorded responses" }
  ]
}
```

In `paths`, `**` crosses directories, `*` and `?` do not, and a trailing `/`
means everything under that directory.

## Required or advisory

Each repository chooses, per integration, by giving a use or a Terraform stack a
`mode`. It is `required` unless it says otherwise.

```json
{ "plugin": "linear.sync", "paths": ["tools/linear_api.py"], "mode": "advisory" }
```

An advisory use is a backup, or an integration being built for a version not
released yet. If it is stale or names no paths, the scan warns, as an annotation
in CI, and passes. A required use in that state fails.

A signature hit that no use covers always fails, whatever the modes: code reaching
a provider must be declared, even if only as advisory for those paths.

`mode` will also drive runtime health once the service runs (phase 5): a required
integration being down makes the service unhealthy, and an advisory one being down
only alerts. Nothing reads it for that yet.

## The scan

```sh
bin/integrations scan PATH             # compare PATH with its integrations.json
bin/integrations scan --suggest PATH   # print a first integrations.json
```

It fails, exit code 1, listing `file:line` for each:

- **undeclared:** a signature is found, and no plugin holding it is declared for
  that file. Where several plugins share a signature, any of them will do.
- **stale:** a required use's path holds no signature of its plugin, or a plugin
  that has signatures names no paths.
- **unregistered:** something that may be a provider the registry does not know:
  a URL in code on a host no plugin names, a workflow secret no plugin names, or
  a Terraform provider no plugin names. Register it, or list it in
  `integrations.json` with a reason.

Exit code 2 means it could not compare: no `integrations.json`, or a bad one.

It reads code and configuration, not prose: Markdown, lock files and comment
lines are skipped.

## Shared packages

An integration is counted in the repository whose code makes the call. A package
or reusable workflow declares its own uses, and the repositories that consume it
do not declare them again. This library's clients are the exception: importing one
is how a repository makes that call, so it declares the plugin. This is also what the scan can see, since it reads
only the repository it is given.

## In CI

```yaml
name: integrations

on:
  pull_request:
  merge_group:
  push: { branches: [main] }

permissions:
  contents: read

jobs:
  integrations:
    uses: Sustenaut/integrations/.github/workflows/scan.yml@<commit> # main
```

## The library

A program calls an outside service through a client here. Every client makes
its calls through one `Caller`, which writes one record per call.

```python
from integrations.calls import Caller, read_modes
from integrations.clients.github_app_token import AppToken
from integrations.clients.github_edit_pr import Change, EditPullRequests

caller = Caller("my-repo", "web", modes=read_modes(Path(".")))
token = AppToken(caller, app_id, private_key, installation_id)
pulls = EditPullRequests(caller, token, "owner/name")
url = pulls.propose(stem="edit/card-", changes=[Change("a.md", old, new)], title=title, body=body)
```

A repository that imports a client declares that plugin in its `integrations.json`,
so its mode and paths are still its own: an import of a client is the plugin's
signature. Each client module has a fake with the same methods, for a program's tests, and a
`check()` that says whether the plugin works now. `health()` turns checks into
what mode means: a required plugin that fails makes a service unhealthy, and an
advisory one only alerts.

A record, described by `schemas/call-record.schema.json`, says who called what,
how long it took and how it ended. It holds no body, full URL, token or personal
data. It goes to the `Caller`'s sink, which keeps nothing by default;
`JsonlSink` writes a file.

An operation's class sets its timeout and tries. A call is tried again only when
nothing answered or the provider asked for it (429, 502, 503, 504), and only if it
is safe to repeat. A provider's refusal comes back to the client. No answer, or a
failure, raises `Unavailable`, whatever the mode: a failure is never swallowed.

Install it from a release's wheel, pinned by URL and hash:

```
integrations[github-app] @ https://github.com/Sustenaut/integrations/releases/download/v0.2.0/integrations-0.2.0-py3-none-any.whl
```

### Releasing

Change `version` in `pyproject.toml` and `__version__` together. When that reaches
`main`, `wheel.yml` tags it, makes the release and attaches the wheel.

JavaScript clients keep their own package and write the same record: `pubpr` is
`github.edit-pr` in the browser.

## Working here

```sh
make venv
make check
```

`scan` needs only Python, and the package has no dependencies. The JSON schemas
in `src/integrations/schemas/` are for editors and the tests; the tests hold
the loader in `consumer.py` to the same answers as the schema.
