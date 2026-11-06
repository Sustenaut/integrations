// clients/js/caller.js: what tests/vectors/ doesn't cover.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Answer, Caller, IntegrationError, MemorySink, NullSink, OPERATIONS, POLICIES, Refused, Replay, Unavailable, pythonJson, readModes } from "../caller.js";

const REGISTRY = new URL("../../../src/integrations/", import.meta.url);
const URL_ = "https://api.example/x";
const PLUGIN = "github.edit-pr";

function caller(responses = {}, options = {}) {
  const sink = new MemorySink();
  const replay = new Replay(responses);
  const made = new Caller("a-repo", "web", { sink, fetch: replay.fetch, sleep: async () => {}, newId: () => "id-1", ...options });
  return { made, sink, replay };
}

test("the tables are classes.json's and the plugins'", async () => {
  const classes = JSON.parse(await readFile(new URL("classes.json", REGISTRY), "utf8"));
  for (const [id, c] of Object.entries(classes)) assert.deepEqual(POLICIES[id], { timeout: c.timeout, tries: c.tries });
  const ops = JSON.parse(await readFile(new URL("plugins/github.repo-ops.json", REGISTRY), "utf8")).operations;
  for (const o of ops) assert.deepEqual(OPERATIONS["github.repo-ops"][o.name], { class: o.class, route: o.route });
  assert.ok(Object.isFrozen(POLICIES) && Object.isFrozen(OPERATIONS));
});

test("a record has CallRecord's fields, in its order, and no body, URL or token", async () => {
  const { made, sink } = caller({ [`POST ${URL_}`]: { status: 201, json: { "secret-answer": 1 } } });
  await made.call(PLUGIN, "open-pr", { method: "POST", url: URL_, headers: { Authorization: "secret-token" }, body: "secret-body" });
  const [record] = sink.records;
  assert.deepEqual(Object.keys(record), ["at", "repo", "app", "plugin", "operation", "request_id", "latency_ms", "status", "outcome", "retries", "bytes_in", "bytes_out", "mode", "route"]);
  assert.match(record.at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.ok(Number.isInteger(record.latency_ms) && record.latency_ms >= 0);
  const text = JSON.stringify(record);
  for (const leak of ["secret", "api.example"]) assert.ok(!text.includes(leak), leak);
});

test("an operation not in the registry is refused, for a call and a note", async () => {
  const { made, sink } = caller();
  await assert.rejects(made.call(PLUGIN, "nope", { method: "GET", url: URL_ }), /github.edit-pr nope is not a registered operation/);
  assert.throws(() => made.note(PLUGIN, "nope", { requestId: "r", latencyMs: 1, status: null, outcome: "ok" }), RangeError);
  assert.equal(sink.records.length, 0);
});

test("the answer can be read as the client needs it", async () => {
  const { made } = caller({ [`GET ${URL_}`]: { status: 200, json: { permissions: { push: true } } } });
  const answer = await made.call("github.repo-ops", "check-push", { method: "GET", url: URL_ });
  assert.ok(answer instanceof Answer && answer.ok);
  assert.deepEqual(await answer.json(), { permissions: { push: true } });
  assert.equal(new Answer(204).json(), null);
  assert.equal(new Answer(null).ok, false);
});

test("the call is sent with its method, headers, body, a User-Agent and a class timeout", async () => {
  const seen = [];
  const fetch = async (url, init) => {
    seen.push({ url, init });
    return new Response("{}", { status: 200, headers: { ETag: '"e1"' } });
  };
  const made = new Caller("a-repo", "", { fetch });
  const answer = await made.call(PLUGIN, "open-pr", { method: "PUT", url: URL_, headers: { Authorization: "t" }, body: new Uint8Array([1, 2]) });
  assert.equal(answer.etag, '"e1"');
  const [{ url, init }] = seen;
  assert.equal(url, URL_);
  assert.equal(init.method, "PUT");
  assert.deepEqual(init.headers, { "User-Agent": "integrations-caller.js", Authorization: "t" });
  assert.deepEqual(init.body, new Uint8Array([1, 2]));
  assert.ok(init.signal instanceof AbortSignal && !init.signal.aborted);
});

test("a provider that doesn't answer within its class's timeout is no answer", async () => {
  const sink = new MemorySink();
  const fetch = async () => {
    throw new DOMException("The operation timed out.", "TimeoutError");
  };
  const made = new Caller("a-repo", "", { sink, fetch });
  await assert.rejects(made.call(PLUGIN, "open-pr", { method: "GET", url: URL_ }), (err) => {
    assert.ok(err instanceof Unavailable && err instanceof IntegrationError);
    assert.equal(err.message, "github.edit-pr open-pr: no answer: timed out after 10 s");
    assert.deepEqual([err.plugin, err.operation, err.name], [PLUGIN, "open-pr", "Unavailable"]);
    return true;
  });
  assert.equal(sink.records[0].status, null);
});

test("only https goes out", async () => {
  let asked = false;
  const made = new Caller("a-repo", "", { fetch: async () => (asked = true) });
  await assert.rejects(made.call(PLUGIN, "open-pr", { method: "GET", url: "http://api.example/x" }), /not https/);
  assert.equal(asked, false);
});

test("the backoff sleeps by default, in seconds", async () => {
  const { made } = caller({ [`GET ${URL_}`]: [{ status: 503, json: {} }, { status: 200, json: {} }] }, { sleep: undefined });
  const started = Date.now();
  assert.ok((await made.call(PLUGIN, "check", { method: "GET", url: URL_ })).ok);
  assert.ok(Date.now() - started >= 990);
});

test("a note is a record of time that wasn't a call", async () => {
  const { made, sink, replay } = caller();
  made.note(PLUGIN, "service-total", { requestId: "r", latencyMs: 9, status: 200, outcome: "ok" });
  const [record] = sink.records;
  assert.deepEqual([record.operation, record.latency_ms, record.route, record.bytes_in, record.request_id], ["service-total", 9, "none", 0, "r"]);
  assert.equal(replay.sent.length, 0);
});

test("forRepo is the same caller for another project", async () => {
  const { made, sink } = caller({ [`GET ${URL_}`]: { status: 200, json: {} } }, { modes: { [PLUGIN]: "advisory" } });
  const other = made.forRepo("b-repo");
  await other.call(PLUGIN, "open-pr", { method: "GET", url: URL_ });
  assert.deepEqual([sink.records[0].repo, sink.records[0].app, sink.records[0].mode], ["b-repo", "web", "advisory"]);
  assert.equal(made.repo, "a-repo");
  assert.ok(other instanceof Caller);
});

test("a sink that fails loses the record, not the call", async (t) => {
  const said = [];
  t.mock.method(console, "error", (line) => said.push(line));
  const fetch = new Replay({ [`GET ${URL_}`]: { status: 200, json: {} } }).fetch;
  const broken = new Caller("a-repo", "", { fetch, sink: { write: () => { throw new Error("disk full"); } } });
  assert.ok((await broken.call(PLUGIN, "open-pr", { method: "GET", url: URL_ })).ok);
  const rejecting = new Caller("a-repo", "", { fetch, sink: { write: async () => { throw new Error("bucket gone"); } } });
  assert.ok((await rejecting.call(PLUGIN, "open-pr", { method: "GET", url: URL_ })).ok);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(said, ["integrations: could not keep a call record: disk full", "integrations: could not keep a call record: bucket gone"]);
});

test("the default sink keeps nothing, and the default fetch is the global one", async (t) => {
  new NullSink().write({});
  t.mock.method(globalThis, "fetch", async () => new Response("{}"));
  assert.ok((await new Caller("a-repo").call(PLUGIN, "open-pr", { method: "GET", url: URL_ })).ok);
});

test("Refused is for clients, as in Python", () => {
  const err = new Refused(PLUGIN, "open-pr", "no");
  assert.ok(err instanceof IntegrationError && !(err instanceof Unavailable));
  assert.equal(err.message, "github.edit-pr open-pr: no");
});

test("modes come from integrations.json, required winning", () => {
  const modes = readModes({
    uses: [{ plugin: PLUGIN, paths: ["a"], mode: "advisory" }, { plugin: "slack.post", paths: ["a"] },
      { plugin: "linear.sync", paths: ["a"], mode: "advisory" }, { plugin: "linear.sync", paths: ["b"] }],
    terraform: [{ dir: "infra", plugins: ["terraform.hetzner"], runs: [], mode: "advisory" }],
  });
  assert.deepEqual(modes, { [PLUGIN]: "advisory", "slack.post": "required", "linear.sync": "required", "terraform.hetzner": "advisory" });
  assert.deepEqual(readModes({}), {});
});

test("a replay answers in turn, keeps its last answer, and a null-body status has none", async () => {
  const replay = new Replay({ [`GET ${URL_}`]: [{ status: 503, json: {} }, { status: 204 }] });
  assert.equal((await replay.fetch(URL_)).status, 503);
  assert.equal((await replay.fetch(URL_)).status, 204);
  assert.equal((await replay.fetch(URL_)).status, 204);
  await assert.rejects(replay.fetch("https://other.example/"), /not recorded/);
});

test("json is written as Python's json.dumps writes it", () => {
  assert.equal(pythonJson({ a: [1, true, null], "é": "x \n" }), '{"a": [1, true, null], "\\u00e9": "x\\u2028\\n"}');
  assert.equal(pythonJson([]), "[]");
  assert.equal(pythonJson({}), "{}");
});
