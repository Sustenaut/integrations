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
| `record` | Optional: how its calls are recorded and replayed ([the record block](#the-record-block)) |

`class` is `H` (human-driven), `E` (event-driven), `S` (scheduled) or `M`
(manual setup). `src/integrations/classes.json` gives each class's response and
uptime targets, once, so no plugin can disagree with them. `route` says whether
the call goes through the service (`routed`), is made by a vendor's own action
and only reported (`reported`), or has nothing to log (`none`).

`detect` can hold `hosts`, `env`, `commands`, `actions`, `terraform_providers`,
`files` and `patterns`. A plugin with none is set up by hand only, and every
operation it has must be class `M`.

A file whose name starts with `_` (`plugins/_github.json`) is not a plugin: it holds
record rules that plugins share. The registry, the scan and the JS generator skip it.

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

The scan runs on GitHub's `ubuntu-latest` unless the caller names other
runners, such as a self-hosted runner scale set, with
`with: { runs-on: my-runners }`; it needs git and Python 3 there.

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

A `Caller` given no `transport` calls through `transports.current_transport()`, looked up
at each call: the network, unless a test harness installed a cassette
([record and replay](#record-and-replay)). A transport given to it still wins.

An operation's class sets its timeout and tries. A call is tried again only when
nothing answered or the provider asked for it (429, 502, 503, 504), and only if it
is safe to repeat. Between tries it waits a random time up to 2**n seconds (full
jitter, so callers that failed together don't retry together), or what a 429's or
503's `Retry-After` says (seconds or a date), up to the class's timeout. A call never
runs past its class's timeout times its tries, waits included: a try that wouldn't
fit isn't started, and the last one gets only the time left. A provider's refusal comes back to the client. No answer, or a
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
Node, `start: true` also sends every two minutes. `MemorySink` and `NullSink` are there
too; a test's recorded answers come from `Cassette` in `transports.js` (below).

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

Code on Cloudflare (a Worker, a Pages Function) has no process that lives two
minutes, so it sends its records to `workers/call-log`, a Durable Object per sender:
`CallLogSink` keeps a request's records and `send()`s them in one call, which the request
hands to `waitUntil`. The object keeps each record in its own durable storage at once, and
on its alarm, every two minutes whether or not anything came, writes the sender's Avro file
(records and report, as `StorageSink`'s) into its buffer tenant in storage's R2 bucket. So
nothing waits in a Worker's memory, and a quiet sender still reports. `SENDERS` in its
`wrangler.toml` is the fixed map from each sender to its dataset; an instance writes only
its own tenant's `inbox/`. A Worker or Pages project binds it with a `durable_objects`
binding naming `script_name = "call-log"`; `.github/workflows/call-log.yml` deploys it.

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

### Record and replay

`integrations.transports` (and `clients/js/transports.js`, exported as
`@sustenaut/integrations/transports.js`) holds the transports a test runs its calls on:

- `Cassette(path, plugins=registry)` answers from a recording. A request finds its recorded
  call by plugin, operation, method, URL (scheme and host included, the query sorted) and
  body (by the plugin's `match`), all normalised by the plugin's record rules, and is
  answered by the first such call not answered yet. A call it has no recording of raises
  `NoSnapshot`, whose message names the scenario, the call, the nearest recorded call and
  how they differ, and ends `record it: pubtest record "<scenario>"`. `unused()` lists the
  recorded calls never made, and `close()` raises `UnusedCalls` for them (only a warning in
  record mode). It reads the old `{"responses": {"METHOD URL": answer}}` files and the shared
  vectors too.
- `Recorder(inner, path, plugins=registry, scenario=...)` sends through `inner`, keeps every
  call in order with its time, and `save()` writes the cassette.
- `Faulty(inner, rules, advance=...)` makes calls fail on purpose. A rule is
  `{"match": "PLUGIN[ OPERATION][ METHOD PATH]", "nth": "*"|"first"|N|"after N", "effect": ...}`,
  PATH a pattern in which `*` is anything, and the effect `no-answer`, `status N` (with the
  rule's `body` and `headers`), `slow S` (advances the test's clock, `advance(S)`, and never
  sleeps; past the call's timeout it is no answer), `malformed` (half the body, then text
  no parser reads) or `then-recorded` (passes through). The first rule that matches a call
  and whose `nth` it is decides.
- `use(transport)` installs a transport for the code it wraps: a context manager in Python
  (per context, a `ContextVar`), a function returning `restore()` in JS. Only test harnesses
  call it.
- `promote(path)` accepts a cassette's recording: it becomes the baseline.

There is no `Replay` or `Recording` any more: `Cassette` and `Recorder` from
`integrations.transports` (or `transports.js`) take their place, and `Cassette` still reads
the old `{"responses": ...}` files.

**Where cassettes go.** A cassette is written (`Recorder.save()`, `promote()`,
`write_cassette()`), and a cassette the environment names is read, only at a path that
resolves, symlinks followed, inside the cassettes directory; anything else raises
`CassettePathError` (a `ValueError`; in JS an `Error` of that name) before a byte is read
or written. The cassettes directory is `INTEGRATIONS_CASSETTES` when it is set, else the
directory holding `INTEGRATIONS_CASSETTE` when that is set, else the directory of the path
given: to the `Recorder` when it is made, or to `promote()` and `write_cassette()`. So
`../outside.json` climbing out of `INTEGRATIONS_CASSETTES`, or a cassette that is a symlink
out of its directory, is refused. `cassettes_dir()` (`cassettesDir()`) says which
directory that is, and `cassette_path(path)` (`cassettePath()`) checks a path against it.

#### Modes and the guard

`current_transport()` (`currentTransport()` in JS) is the transport `use()` installed, else
the one `INTEGRATIONS_MODE` picks:

| `INTEGRATIONS_MODE` | Transport |
|---|---|
| `live`, or unset | `Network()`; `globalThis.fetch` in JS |
| `replay` | `Cassette(INTEGRATIONS_CASSETTE)` |
| `record` | `Recorder(Network(), INTEGRATIONS_CASSETTE)`, saved when the process exits |

`INTEGRATIONS_CASSETTE` is a file path: it is how a child process, a command line a scenario
runs, learns its cassette. `INTEGRATIONS_CASSETTES`, when set, is the directory every
cassette must resolve inside, `INTEGRATIONS_CASSETTE` included. A mode other than `live` needs `PUBTEST_RUN` set, which only a
test harness sets: without it `current_transport()`, and every `Caller.call`, whatever
transport it was given, raise `ModeError`, so a pod misconfigured into replay can't answer
from cassettes. Another mode, or one with no `INTEGRATIONS_CASSETTE`, is refused the same way.

#### The cassette format

One file per scenario, `integrations.cassette/1`:

```json
{
  "format": "integrations.cassette/1",
  "scenario": "<feature file>: <scenario title>[ #<row>]",
  "current": {
    "recorded": {"at": "2026-11-19T09:00:00Z", "integrations": "0.16.0", "consumer": "<repo>@<sha>"},
    "volatile": {"⟦integrations:sha:1⟧": {"kind": "sha", "first": "calls.0.response.body.object.sha"}},
    "calls": [{"kind": "http", "plugin": "github.edit-pr", "operation": "read",
               "request": {"method": "GET", "url": "https://api.github.com/repos/o/r/git/ref/heads/main",
                           "headers": {}, "body": null},
               "response": {"status": 200, "headers": {"ETag": "⟦integrations:header:1⟧"},
                            "body": {"json": {"object": {"sha": "⟦integrations:sha:1⟧"}}}},
               "elapsed_ms": 40}]
  },
  "baseline": null
}
```

A body is `{"json": ...}`, `{"text": ...}`, `{"base64": ...}` or null; a request body over
64 KiB is `{"sha256": ..., "length": N}` and matched by that, while an answer's body is
always kept. An answer that never came is `{"status": null, "error": "no answer: ..."}`.

**Placeholders.** Each cassette has one map from real values to placeholders, applied to
requests and answers alike: a volatile value (by JSON path, header, or a kind's regex) is
written `⟦integrations:KIND:N⟧`, the same placeholder wherever the same value is, and a
different one for every other value. `volatile` lists them, with where each was first seen;
it never holds a real value. On replay each placeholder is answered as a stand-in made from
its name, of its kind's shape: a 40-hex sha, an ISO or HTTP date, a ten-digit id, a
`ghs_` token. A stand-in the code sends back is read as its placeholder again, so a sha one
call answers and the next sends still matches. Replayed stand-in dates are in the year
2000, so one is never mistaken for a real date. A volatile value first seen in a request is
one the code made itself, and matches any value of its kind; where an answer echoes it, it
is replayed as a stand-in. Real text holding `⟦integrations:` is written
`⟦integrations:escape⟧`, and replayed as it was.

**Secrets.** A recording never holds a secret's value: secret headers (and
`Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie` for every plugin) are
dropped, secret query parameters and JSON paths replaced (`⟦integrations:secret⟧` in a
request, `⟦integrations:secret:N⟧` in an answer, replayed as a token). `save()` then looks
for every secret value it saw, of four characters or more, anywhere in what it would write,
and raises `SecretInRecording` without writing anything when one is there.

**The baseline.** `current` is what replay uses. `baseline` is the last accepted
recording: a re-record never overwrites it, and accepting a recording moves it.
`Recorder.save()` into an existing cassette writes the new recording as the candidate,
`current`, and keeps the file's previous `current` as `baseline` unless it already has
one; `promote(path)` (a later `pubtest record --accept`) makes `current` the baseline.
So when the data repository's pull request with a re-record merges, `baseline` still holds
the recording accepted before it, which is what a drift check compares a live answer with.

#### The record block

A plugin's optional `record` says how its calls are recorded:

| Field | Meaning |
|---|---|
| `extends` | A shared file, `"_github"` for `plugins/_github.json`, whose rules these are merged over: lists joined, objects merged, the rest replaced. Only the record block extends |
| `keep_headers` | The headers kept, of requests and answers; every other is dropped |
| `volatile` | `headers`, `json` (JSONPath-lite: `$.a.b`, `$..key`, `$.a[*].b`, `$.a.*`) and `values` (a regex per kind, for a value anywhere) |
| `secret` | `headers` dropped, `json` paths and `query` parameters replaced |
| `match` | `url` (`scheme+host+path+sorted-query`), `body` (`json`, compared as parsed JSON after normalising, or `text`, exact) and `graphql` (`query-name+variables`: the operation's name and its variables) |
| `customers` | The set of test customers recordings are made with, such as `github.test` |

`registry.record(plugin_id)` gives a plugin's rules merged, every field there; a plugin
without a block gets the defaults. `_github.json` holds GitHub's, which `github.app-token`,
`github.edit-pr`, `github.board-read` and `github.repo-ops` extend. `clients/js/generate.mjs`
writes the record blocks into `clients/js/transports.js`, so both languages record by the
same rules; the vectors in `tests/vectors/cassettes/` run on both.

#### Caller.fetch and Caller.opener

Code that takes a `fetch` in place of making its own requests, such as pubpr's
`propose({ fetch })`, works unchanged through a `Caller`:

```js
const result = await propose({ repo, branch, title, changes, token, fetch: caller.fetch("github.edit-pr", "open-pr") });
```

Each request is `caller.call` with that plugin and operation: its policy, retries, record
and the current transport. It answers a `Response` (`status`, `ok`, `headers.get`, `text()`,
`json()`, `arrayBuffer()`); a refusal is its answer, and no answer throws a `TypeError`, as
`fetch` does, with the `Unavailable` as its `cause`.

`Caller.opener(plugin, operation, *, refused=None, repeatable=False)` is Python's:
`open(method, url, headers=None, body=None) -> Response`, `body` bytes or text. No answer
raises `Unavailable`.

#### GitHub's test steps

`integrations.testing` is an opt-in pytest plugin (the `pytest11` entry point
`integrations.testing`), inert unless a suite sets `integrations_testing = true` in its
pytest.ini or lists `pytest_plugins = ["integrations.testing.github"]`. In this release it
is a stub: `Given the GitHub repository "o/r" holds:` (a JSON object of each file's path to
its text) registers the repository under its own name in replay, and in record mode raises
`NotImplementedError`, as creating it needs the GitHub test org.

#### Later steps

pubtest 0.3 brings the harness: it installs a cassette per scenario with `use()`, sets
`PUBTEST_RUN`, `INTEGRATIONS_MODE` and `INTEGRATIONS_CASSETTE` for the programs a scenario
runs, puts its failure steps on `Faulty`, and adds `pubtest record "<scenario>"` (and
`--accept`, which calls `promote`). Then come the GitHub test org and its recorder app,
which record mode creates repositories in and `integrations.testing.github` fills out; then
the consumers, pubpr and release-controlplane first, moving their tests onto cassettes; and
last the production drift check, which reuses each plugin's `record.volatile` rules to
compare what a provider answers now with the cassette's baseline.

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

It runs on the shared k3s server, namespace `integrations`, and "Deploying" below says
how it gets there. The ingest pod needs the variable `INGEST_DATASETS` on this
repository: JSON from each repository to its dataset.
`INGEST_SIGNALS`, when set, also has it load observability's signals:
`{"tenant": "signals", "dataset": "observability"}`, the tenant the evaluator writes
them to and the dataset whose `signals` table they load into. That buffer holds Avro
signals under `inbox/signals/`, loads like a call log's but into its own table, and
gets no reports: the pod's report on it goes to its log. `INGEST_NEED_STATES`, in the
same shape (`{"tenant": "need-states", "dataset": "convergence"}`), loads the
convergence engine's need states the same way, from `inbox/need-states/` into that
dataset's `need_states` table, for observability's `need_drift` rule. A file there that
isn't need states is set aside alone, in `rejected/`. `INGEST_PUBRULES_CHECKS`
(`{"tenant": "pubrules-checks", "dataset": "platform"}`) loads the platform's nightly
checks of each repository against pubrules, passes and failures, from
`inbox/pubrules-checks/` into that dataset's `pubrules_checks` table, for observability's
`pubrules_drift` rule and for audits; it too sets aside what isn't pubrules checks.
`INGEST_BUFFERS` names any number more, as a list of `{"tenant": ..., "dataset": ...,
"table": ...}`, each the buffer whose table it names, so one dataset may hold several:
`[{"tenant": "ci-usage", "dataset": "platform", "table": "ci_jobs"}, {"tenant":
"ci-usage", "dataset": "platform", "table": "cluster_usage"}]` loads CI's wait times and
compute load (below) beside `pubrules_checks`. Buffers that share a tenant keep their
batch records apart, under `batches/<table>-`. The three variables before it work as
they did.

`INGEST_NOTIFY_KEY` (the Secret `ingest-notify`, from Bitwarden's `INGEST_NOTIFY_KEY`)
turns on storage's notices, below; without it they are refused. `INGEST_SWEEP_SECONDS`,
`INGEST_REPORT_SECONDS` and `INGEST_BATCH_GAP_SECONDS`, numbers of seconds, set how often
the pod lists every buffer, how often its own reports load when no batch takes them, and
how far apart a notified buffer's batches are; unset, each is 120, as the pod has always run.

### CI's wait times and compute load

`integrations ci-usage` collects them into storage's `ci-usage` buffer, for BigQuery's
`platform.ci_jobs` and `platform.cluster_usage` (shared-infra's `infra/platform.tf`).
Nothing reads them as a rule yet: they are the history a rule will be designed from.

- `integrations ci-usage jobs`, the CronJob `ci-jobs` (`k8s/ci-jobs.yaml`), every ten
  minutes: each finished GitHub Actions job of every repository the release reader app's
  installation sees, archived ones aside (the board's and the others alike), GitHub's
  runners and `sustenaut-ci` alike, written once, with `wait_seconds` (started minus queued)
  and `run_seconds`. It reads as the release reader app, from the Secret `ci-jobs-app`, and
  keeps which jobs it has written in a head of the `ci-jobs` tenant. `--since-days 90`
  backfills, once: the deploy block's `k8s.job-once` need `ci-jobs-backfill` (pubdeploy
  0.4.4) makes that Job from the CronJob's template, and the walk then shows it running,
  completed or failed. A failed one is not run again by itself (`rerun: never`): delete the
  Job and walk with `--fulfil`. A backfill stopped partway goes on where it was. The first
  backfills ran by hand in November 2026, the second with `--except-board` for the
  repositories the first, which read the board alone, missed.
- `integrations ci-usage cluster`, the CronJob beside the runners in shared-infra's
  `infra/ci-runners/`, every two minutes: node, namespace, `ci` (quota and runners) and
  runner rows, each runner pod with its runner's and Docker's cgroup `memory.peak`, its
  `runner_name` the one `ci_jobs` names.

To add a use case: add its route, regenerate the spec, and add it to `USE_CASES` in
`clients/js/generate.mjs`.

### Deploying

`.github/workflows/deploy.yml` builds the image on each merge to main, once CI has
passed, and applies `k8s/` with pubrules's `k8s-deploy.yml`, once `DEPLOY_INTEGRATIONS` is
`on`. The variables become the ConfigMaps `settings` and `ingest-settings`.

What it needs elsewhere: shared-infra's `integrations-ingest` account and the call log
datasets (`infra/call_log.tf`), storage's `calls-integrations` tenant and the buffers of
the projects it loads, and its variables on this repository.
pubrules.json's `deploy` block lists them with everything else it needs, and pubrules's
deploy walk checks each and makes what it can. To take it live, or bring it back in
step, from a checkout of this repository with Sustenaut/shared-infra checked out beside
it, on a machine on the admin tailnet, signed in to `gh` and `gcloud`:

```sh
./with-secrets ./pubdeploy walk --fulfil --server sustenaut --zones ../shared-infra/pubrules.json
```

It checks every need, prints the plan, and waits for a typed `yes`; then it makes each
missing need it can, over the admin channel (`ssh root@sustenaut`), and checks it again.
Every run can run again: what is ready is left alone. In order:

| Need | What the walk does |
|---|---|
| the namespace `integrations` | the server's `sustenaut-app`, which makes it and its deploy identity |
| `gcp.workload-identity` `ingest` | that the ingest's ServiceAccount may act as `integrations-ingest` (Terraform's, in shared-infra) through the `services` Workload Identity pool: the ingest reads BigQuery with no key, its `INGEST_KEY_FILE` being `k8s/google-credential.yaml`'s external_account credential. The grant is Terraform's (`infra/services-identity.tf`), so a missing one is proposed |
| `k8s.manifests` | `k8s/admin/network-policy.yaml` applied: only the service's pods, and the cluster's Traefik with storage's notices, may reach the ingest pod |
| `bitwarden.secret` `ingest-notify-key` | the Secret `ingest-notify` (key `key`), from Bitwarden's `INGEST_NOTIFY_KEY`: the key storage's notices are signed with. A person makes it in Bitwarden, and sets the same value on storage's Pages project and the call-log Worker |
| the repository's variables | `INGEST_DATASETS`, `INGEST_SIGNALS`, `INGEST_NEED_STATES`, `INGEST_PUBRULES_CHECKS`, `INGEST_BUFFERS`, `INTEGRATIONS_ORIGINS` and `INTEGRATIONS_EDIT_PR_REPOS` must be set (`INGEST_SWEEP_SECONDS`, `INGEST_REPORT_SECONDS` and `INGEST_BATCH_GAP_SECONDS` may be): their values are the owner's state file's, so a missing one is set with `pubfleet variables --apply` |
| `DEPLOY_INTEGRATIONS` and the rollout | the gate on, once everything before it is ready, and `deploy.yml` run and waited for |
| `service.health` | `https://integrations.sustenaut.eu/healthz` answers |

A new key wants a Bitwarden token that may write: the walk asks for it once, at a hidden
prompt, and keeps it only in its process.

Some needs it only checks, and says what a person does:

- **storage's tenant** `calls-integrations` in Sustenaut/storage's `tenants.json`
  (private, an inbox), the service's own call log buffer, which `k8s:integrations/app`
  writes and `k8s:integrations/ingest` reads, writes and deletes. When it is missing,
  the fix is the entry to add there. The other projects' buffers are their own needs.
- **The entries other services need in its variables**: their own deploy blocks check
  them, such as the notifier's `INGEST_DATASETS` entry and observability's
  `INGEST_SIGNALS`.
- **The DNS record** for `integrations.sustenaut.eu`, in shared-infra's hosting block.
- **storage itself** (`upstream`).

`./pubdeploy walk` without `--fulfil` (or `pubdeploy check`) changes nothing, and
`./pubdeploy walk --offline` checks only the files. `.github/workflows/deploy-check.yml`
walks it every 30 minutes, read-only, with the deploy key, and writes the walk to
storage's `plane` tenant; it fails when a need is missing, drift or blocked.

## The call log

`integrations.ingest` is the pod that gets call records into BigQuery. Services don't
send it records: each writes its records, through `integrations.storage_sink.StorageSink`
(the `storage-sink` extra), as an Avro file in its project's buffer in the storage service
(`inbox/calls/`), so nothing is lost while this pod or BigQuery is down. Each project has
its own private tenant, named like its dataset.

Storage tells the pod of each file it takes into a buffer's inbox: `POST /ingest/notify`
on `integrations.sustenaut.eu`, `{"tenant", "name", "size"}`, with `X-Ingest-Timestamp`
(Unix seconds) and `X-Ingest-Signature: sha256=<hex>`, the HMAC-SHA256 under
`INGEST_NOTIFY_KEY` of the timestamp, a dot and the body. One signed more than 300 seconds
from the pod's clock is refused, and so is a body over 4,096 bytes (413), unread. A notice is only a hint: the pod reads the file it names
from a buffer it scans, under that buffer's folder, with its own token. The pod merges the
files it was told of into one load job once the first has waited 5 seconds and the
buffer's last batch is `INGEST_BATCH_GAP_SECONDS` old (120, so a table uses at most 720 of
its 1,500 jobs a day; 62 is the least that stays within the pod's 1,400), and deletes them
once they have loaded, with nothing listed. A sweep lists every buffer at start and every
`INGEST_SWEEP_SECONDS`, for what no notice named, and a buffer whose batch didn't finish is
swept, not taken from its notices. Without notices the sweep alone does it all. A record in
`batches/` names the files first, so a pod that stops partway sends the same job again,
which BigQuery won't load twice. A rejected batch is split until only the bad files are
left, and those move to `rejected/`.

Every sender writes a file every two minutes, even with no calls in it, and the file's
metadata is its report: who it is, its version, the cycle, its calls, drops, buffer size and
storage failures. Those load into a `status` table next to `calls`, so a quiet service can
be told from one that has stopped. The pod reports too, for each project each pass: what is
waiting and how old it is, what was rejected, and the load jobs used today. Its report goes
with that project's next reports job, or as a job of its own once none has gone for
`INGEST_REPORT_SECONDS`, and is never a file in the buffer. All of this is facts, and it
holds no thresholds or alerts.

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
