// clients/js/storage.js, on the vectors in tests/vectors/storage/ that
// tests/steps/test_storage_client.py runs too, and what they don't cover.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { Caller, MemorySink, Replay, Refused, Unavailable } from "../caller.js";
import { NotFound, ROUTES, SPEC, Storage, StorageError, StorageUnavailable } from "../storage.js";

const VECTORS = new URL("../../../tests/vectors/storage/", import.meta.url);
const BASE = "https://storage.example";
const camel = (name) => name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
const value = (arg) => (arg && typeof arg === "object" && "bytes" in arg ? new TextEncoder().encode(arg.bytes) : arg);

// A result as the vectors write it: bytes as {text}.
function plain(result) {
  if (result instanceof Uint8Array) return { text: new TextDecoder().decode(result) };
  if (result && typeof result === "object" && "hash" in result && result.body instanceof Uint8Array) return { body: plain(result.body), hash: result.hash };
  return result ?? null;
}

function made(vector, options = {}) {
  const sink = new MemorySink();
  const naps = [];
  const replay = new Replay(vector.responses);
  const caller = new Caller("a-repo", "web", { sink, fetch: replay.fetch, sleep: async (s) => naps.push(s), newId: () => "id-1" });
  const { tenant, token, request_id: requestId, user_agent: userAgent } = vector.client;
  const storage = new Storage({ tenant, token, requestId, userAgent, baseUrl: BASE, caller, ...options });
  return { storage, sink, replay, naps };
}

const names = (await readdir(VECTORS)).filter((n) => n.endsWith(".json")).sort();
for (const name of names) {
  const vector = JSON.parse(await readFile(new URL(name, VECTORS), "utf8"));
  test(`${name}: ${vector.description}`, async () => {
    const { storage, sink, replay, naps } = made(vector);
    const kwargs = Object.fromEntries(Object.entries(vector.kwargs).map(([k, v]) => [camel(k), v]));
    const args = vector.args.map(value);
    const { expect } = vector;
    let raised = null;
    let result = null;
    try {
      result = plain(await storage[camel(vector.call)](...args, ...(Object.keys(kwargs).length ? [kwargs] : [])));
    } catch (err) {
      if (!(err instanceof StorageError || err instanceof StorageUnavailable)) throw err;
      raised = { type: err.constructor.name, status: err.status, error: err.error };
    }
    assert.deepEqual([raised, result, naps], [expect.raises, expect.result, expect.sleeps]);
    assert.equal(replay.sent.length, expect.sent.length);
    expect.sent.forEach((want, i) => {
      const sent = replay.sent[i];
      assert.deepEqual([sent.method, sent.url], [want.method, want.url]);
      const headers = Object.fromEntries(Object.keys(want.headers ?? {}).map((k) => [k, sent.headers[k]]));
      assert.deepEqual(headers, want.headers ?? {});
      if (want.json) assert.deepEqual(JSON.parse(sent.body), want.json);
    });
    assert.equal(sink.records.length, expect.records.length);
    expect.records.forEach((want, i) => assert.deepEqual(Object.fromEntries(Object.keys(want).map((k) => [k, sink.records[i][k]])), want));
  });
}

test("there are vectors to run", () => assert.ok(names.length >= 10));

test("the routes are the pinned spec's", async () => {
  const lock = JSON.parse(await readFile(new URL("../../../openapi/storage.lock.json", import.meta.url), "utf8"));
  assert.deepEqual(SPEC, { repository: lock.repository, commit: lock.commit, sha256: lock.sha256 });
  assert.deepEqual(ROUTES.list.query, ["prefix", "limit", "cursor"]);
  assert.ok(Object.isFrozen(ROUTES));
});

test("an upload is one form with the fields given", async () => {
  const { storage, replay } = made({ client: { tenant: "deals", token: "t" }, responses: { [`POST ${BASE}/deals/files`]: { status: 200, json: {} } } });
  await storage.uploadFile(new Blob(["%PDF-1.7"]), { name: "a.pdf", kept: false, for: "someone" });
  const form = replay.sent[0].body;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("file").name, "a.pdf");
  assert.equal(await form.get("file").text(), "%PDF-1.7");
  assert.deepEqual([form.get("name"), form.get("kept"), form.get("for"), form.get("label"), form.get("subject")], ["a.pdf", "false", "someone", null, null]);
  await assert.rejects(storage.uploadFile(new Uint8Array([1])), /needs the file's name/);
});

test("the token file is read for each request, and another tenant shares it", async () => {
  let token = "first";
  const reads = [];
  const vector = { client: { tenant: "deals" }, responses: {
    [`GET ${BASE}/deals/head/board/log`]: { status: 200, json: { log: [] } }, [`GET ${BASE}/other/head/board/log`]: { status: 200, json: { log: [] } } } };
  const { storage, replay } = made(vector, { tokenFile: "/var/run/token", readFile: async (path, enc) => (reads.push([path, enc]), `${token}\n`) });
  await storage.headLog("board");
  token = "second";
  await storage.forTenant("other").headLog("board");
  assert.deepEqual(replay.sent.map((s) => s.headers.Authorization), ["Bearer first", "Bearer second"]);
  assert.equal(replay.sent[1].url, `${BASE}/other/head/board/log`);
  assert.deepEqual(reads[0], ["/var/run/token", "utf8"]);
});

test("a token can be a function, and one is needed, as is a tenant", async () => {
  const { storage, replay } = made({ client: { tenant: "deals" }, responses: { [`GET ${BASE}/deals/head/board/log`]: { status: 200, json: { log: [] } } } }, { token: async () => "minted" });
  await storage.headLog("board");
  assert.equal(replay.sent[0].headers.Authorization, "Bearer minted");
  assert.throws(() => new Storage({ tenant: "deals" }), /a token or a tokenFile/);
  assert.throws(() => new Storage({ token: "t" }), /needs a tenant/);
  assert.equal(new Storage({ tenant: "d", token: "t" }).baseUrl, "https://storage.sustenaut.eu");
});

test("a place outside the buffers' folders is refused here", async () => {
  const { storage, replay } = made({ client: { tenant: "deals", token: "t" }, responses: {} });
  for (const place of ["f/x", "inbox", "other/a.avro"]) await assert.rejects(storage.getBuffer(place), /is not in inbox\/, batches\/ or rejected\//);
  assert.equal(replay.sent.length, 0);
});

test("a batch's JSON goes as it is, and the errors are the caller's kinds", async () => {
  const { storage, replay } = made({ client: { tenant: "deals", token: "t" }, responses: {
    [`PUT ${BASE}/deals/batches/job.json`]: { status: 200, json: { name: "batches/job.json", size: 2 } },
    [`GET ${BASE}/deals/batches/job.json`]: { status: 200, json: { files: [] } } } });
  await storage.putBuffer("batches/job.json", '{"files":[]}');
  assert.equal(replay.sent[0].body, '{"files":[]}');
  assert.deepEqual(await storage.getBuffer("batches/job.json"), { files: [] });
  const error = new NotFound("read", 404, "not found");
  assert.ok(error instanceof StorageError && error instanceof Refused);
  assert.equal(error.message, "storage.api read: HTTP 404 not found");
  assert.equal(new StorageError("write", 500, "").message, "storage.api write: HTTP 500");
  assert.ok(new StorageUnavailable(new Unavailable("storage.api", "read", "no answer")) instanceof Unavailable);
});
