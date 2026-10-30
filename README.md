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

## The scan

```sh
bin/integrations scan PATH             # compare PATH with its integrations.json
bin/integrations scan --suggest PATH   # print a first integrations.json
```

It fails, exit code 1, listing `file:line` for each:

- **undeclared:** a signature is found, and no plugin holding it is declared for
  that file. Where several plugins share a signature, any of them will do.
- **stale:** a declared path holds no signature of its plugin.
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
do not declare them again. This is also what the scan can see, since it reads
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

## Working here

```sh
make venv
make check
```

`scan` needs only Python, and the package has no dependencies. The JSON schemas
in `src/integrations/schemas/` are for editors and the tests; the tests hold
the loader in `consumer.py` to the same answers as the schema.
