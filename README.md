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

### From JavaScript

`clients/js/caller.js` is the same `Caller` for Node 22 and Cloudflare Workers, with no
dependencies: the same classes, timeouts, retries and records, with `Unavailable`,
`Refused`, `note()` and `forRepo()`. It is generated (`npm run generate` in `clients/js`)
from `clients/js/src/caller.js` and the tables `calls.py` reads, `classes.json` and
`plugins/*.json`, and CI fails when the committed file differs. The JSON vectors in
`tests/vectors/` run on both callers.

```js
import { Caller, StorageSink } from "@sustenaut/integrations/caller.js";

const sink = new StorageSink({ "my-repo": "my_project" }, {
  sender: "my-app", url: "https://storage.sustenaut.eu", tokenFile: "/var/run/secrets/storage/token", start: true,
});
const caller = new Caller("my-repo", "web", { sink });
const answer = await caller.call("github.repo-ops", "check-push",
  { method: "GET", url: `https://api.github.com/repos/${repo}`, headers: { Authorization: `Bearer ${token}` } });
if (!answer.ok || !answer.json().permissions?.push) throw new Error("no push");
```

`StorageSink` writes the same Avro files as `storage_sink.py`, to the storage service
(`url` with `token` or `tokenFile`, read for each request) or, in the storage Worker, to
its R2 binding (`bucket` and `prefix`). A Worker has no background timer, so the write
that finds a file overdue sends it; give it the request's `waitUntil`
(`sink.waitUntil = (p) => ctx.waitUntil(p)`) so the answer doesn't wait on storage. In
Node, `start: true` also sends every two minutes. `MemorySink`, `NullSink` and `Replay`
(a recorded fetch, for tests) are there too.

`clients/js/storage.js` is the storage service's client on that `Caller`, and
`integrations.clients.storage` the same in Python: one tenant's files (upload, get, and a
list across every page, filtered by `subject`), named heads (get, move with `If-Match`,
log) and buffers (put, get, delete, list). Each call is plugin `storage.api`: `read` and
`write` for an app (class H), `buffer` for scheduled buffer work (S). It takes `baseUrl`,
`tenant`, `token` or `tokenFile` (read for each request), `caller`, `requestId` (sent as
`X-Request-Id`) and `userAgent`. Storage saying no is a `StorageError` (`NotFound` for a
404) with its `status` and `error`; failing or not answering is `StorageUnavailable`.

```js
import { Storage } from "@sustenaut/integrations/storage.js";

const storage = new Storage({ tenant: "my-tenant", tokenFile: "/var/run/secrets/storage/token", caller });
const { id } = await storage.uploadFile(bytes, { name: "offer.pdf", subject: "card-42" });
const files = await storage.listFiles({ subject: "card-42" });
```

Both are generated from Sustenaut/storage's `openapi.json`, vendored in
`openapi/storage.json` at the commit `openapi/storage.lock.json` pins with its SHA-256.
`npm run generate` stops when the file isn't that one, and the `clients` check fails when
`storage.js` or `src/integrations/clients/storage_routes.py` differ from what it gives.
`storage-spec.yml` checks, weekly or by hand, that the vendored file is the pinned commit's
and says when storage's main has a newer one. To move to it: vendor the file, update the
lock's `commit` and `sha256`, and run `npm run generate`. The JSON vectors in
`tests/vectors/storage/` run on both clients.

There is no npm release: depend on a release's tag, as with pubpr.

```json
"dependencies": { "@sustenaut/integrations": "github:Sustenaut/integrations#vX.Y.Z" }
```

A shell script posts to Slack with a command, which takes the token from
`SLACK_TOKEN`, never from the command line:

```sh
integrations slack post --channel "$USER_ID" --text "the nightly finished"
```

Install it from a release's wheel, pinned by URL and hash:

```
integrations[github-app] @ https://github.com/Sustenaut/integrations/releases/download/v0.12.2/integrations-0.12.2-py3-none-any.whl
```

### Releasing

With [pubrel](https://github.com/Sustenaut/pubrel), like the other Sustenaut packages.
A pull request that changes `src/` or `pyproject.toml` records what it changes
(`pubrel add fixed "…"`, or `internal` for no release), and CI checks it. To release,
run `pubrel prepare` on an up-to-date main in a checkout holding the release key
(`corpus/.publet`, gitignored): it sets `version` in `pyproject.toml` (`__version__` reads
it), signs the release and opens its pull request. Merging that tags `vX.Y.Z` and makes
the release, and `wheel.yml` then attaches `integrations-X.Y.Z-py3-none-any.whl`.

## The service

`integrations.service` is one API per use case, in FastAPI. `openapi/integrations.json`
is generated from it and committed, and a test fails when they differ. From that
spec come the JavaScript clients, the docs and a mock.

| From the spec | How | Where |
|---|---|---|
| A client per use case | `npm run generate` in `clients/js` | `clients/js/github-edit-pr.js`, a dependency-free module |
| Docs | `npm run docs` | `/docs/` on the service |
| A mock | `npm run mock` | for a program's tests |

A browser loads a client from jsDelivr at a release's tag, with its hash:

```js
import { propose, readFiles, snapshot } from "https://cdn.jsdelivr.net/gh/Sustenaut/integrations@v0.12.2/clients/js/github-edit-pr.js";

const { commit, paths, can_push } = await snapshot({ baseUrl, token, repo });
const { files } = await readFiles({ baseUrl, token, repo, commit, paths });
const result = await propose({ baseUrl, token, repo, branch, title, changes });
```

`github.edit-pr` acts as the person whose token comes with the request, so the pull
request is theirs. The token is used for that request and forgotten: never stored
or logged. A deployment names the repositories it serves and the sites that may
call it, in `INTEGRATIONS_EDIT_PR_REPOS` and `INTEGRATIONS_ORIGINS`, and with none
named it serves nothing. `INTEGRATIONS_EDIT_PR_PER_MINUTE` and `INTEGRATIONS_EDIT_PR_READS_PER_MINUTE` limit
its proposals and reads, and `INTEGRATIONS_EDIT_PR_TIMINGS_PER_MINUTE` the timings, which need no
token, so that they can't use up the others.

It runs on the shared k3s server, namespace `integrations`. `.github/workflows/deploy.yml`
builds the image and applies `k8s/` on each merge to main. A new server needs
`sustenaut-app integrations` once, the deploy secrets and variables on this repository,
and the DNS record for `integrations.sustenaut.eu`.

Before the first deploy with the ingest pod, a person puts the writer's key in the
namespace, from a `shared-infra` checkout beside this one:
`./with-secrets ../integrations/k8s/admin/push-ingest-key.sh`. It also needs the variable
`INGEST_DATASETS` on this repository: JSON from each repository to its dataset.

To add a use case: add its route, regenerate the spec, and add it to `USE_CASES` in
`clients/js/generate.mjs`.

## The call log

`integrations.ingest` is the pod that gets call records into BigQuery. Services don't
send it anything: each writes its records, through `integrations.storage_sink.StorageSink`
(the `storage-sink` extra), as an Avro file in its project's buffer in the storage service
(`inbox/calls/`), so nothing is lost while this pod or BigQuery is down. Each project has
its own private tenant, named like its dataset.

Every two minutes the pod merges what is waiting into one load job and deletes the files
once it has loaded, so a table uses at most 720 of its 1,500 jobs a day. A record in
`batches/` names the files first, so a pod that stops partway sends the same job again,
which BigQuery won't load twice. A rejected batch is split until only the bad files are
left, and those move to `rejected/`.

Every sender writes a file every two minutes, even with no calls in it, and the file's
metadata is its report: who it is, its version, the cycle, its calls, drops, buffer size and
storage failures. Those load into a `status` table next to `calls`, so a quiet service can
be told from one that has stopped. The pod reports too, with a file of its own for each
project each cycle: what is waiting and how old it is, what was rejected, and the load jobs
used today. All of this is facts, and it holds no thresholds or alerts.

A request's calls share one id, given by the app or made by the service, and returned
as `X-Request-Id`. That splits a slow request three ways: each call's `latency_ms` is
the provider's time, `service-total` is the whole request at the service, and
`client-total`, which the generated client sends in batches, is the whole request as the
app saw it.

## Working here

```sh
make venv
make check
```

`scan` needs only Python, and the package has no dependencies. The JSON schemas
in `src/integrations/schemas/` are for editors and the tests; the tests hold
the loader in `consumer.py` to the same answers as the schema.
