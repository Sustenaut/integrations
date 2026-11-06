// workers/call-log's Durable Object, on fakes of its storage and R2, and caller.js's
// CallLogSink that sends it records.
import { test } from "node:test";
import assert from "node:assert/strict";
import { CallLog, report } from "../../../workers/call-log/src/call-log.js";
import worker from "../../../workers/call-log/src/call-log.js";
import { CallLogSink } from "../caller.js";

const START = 1_800_000_000_000; // 2027-01-15T08:00:00Z
const SENDERS = { storage: "calls_storage", other: "calls_other" };

// Durable Object storage, as its async API answers.
function durable() {
  const kept = new Map();
  let alarm = null;
  const copy = (v) => structuredClone(v);
  return {
    kept,
    get alarm() { return alarm; },
    async get(k) {
      if (Array.isArray(k)) return new Map(k.filter((x) => kept.has(x)).map((x) => [x, copy(kept.get(x))]));
      return kept.has(k) ? copy(kept.get(k)) : undefined;
    },
    async put(k, v) {
      if (typeof k === "string") kept.set(k, copy(v));
      else for (const [x, y] of Object.entries(k)) kept.set(x, copy(y));
    },
    async delete(k) {
      for (const x of Array.isArray(k) ? k : [k]) kept.delete(x);
    },
    async list({ prefix }) {
      return new Map([...kept].filter(([x]) => x.startsWith(prefix)).sort(([a], [b]) => (a < b ? -1 : 1)).map(([x, y]) => [x, copy(y)]));
    },
    async getAlarm() { return alarm; },
    async setAlarm(at) { alarm = at; },
  };
}

function bucket() {
  const files = new Map();
  return { files, down: false, async put(key, data) { if (this.down) throw new Error("R2 is down"); files.set(key, data); } };
}

function made() {
  const storage = durable();
  const calls = bucket();
  const clock = { now: START };
  const object = new CallLog({ storage }, { SENDERS: JSON.stringify(SENDERS), CALLS: calls }, { now: () => clock.now });
  const post = (body, method = "POST", path = "/records") => object.fetch(new Request(`https://call-log${path}`, { method, body: method === "POST" ? JSON.stringify(body) : undefined }));
  return { object, storage, calls, clock, post };
}

const record = (n, fields = {}) => ({
  at: "2026-11-06T09:00:00Z", repo: "storage", app: "worker", plugin: "github.repo-ops", operation: "check-push",
  request_id: `r${n}`, latency_ms: 40, status: 200, outcome: "ok", retries: 0, bytes_in: 10, bytes_out: 0,
  mode: "required", route: "routed", ...fields,
});

// The sender's report, from an Avro file's metadata.
function metadata(bytes) {
  let at = 4;
  const long = () => {
    let z = 0n;
    for (let shift = 0n; ; shift += 7n) {
      const b = bytes[at++];
      z |= BigInt(b & 0x7f) << shift;
      if (!(b & 0x80)) break;
    }
    return Number((z >> 1n) ^ -(z & 1n));
  };
  const string = () => {
    const n = long();
    return new TextDecoder().decode(bytes.slice(at, (at += n)));
  };
  const meta = {};
  for (let n = long(); n; n = long()) for (let i = 0; i < n; i++) meta[string()] = string();
  return JSON.parse(meta["integrations.status"]);
}

test("records are kept the moment they come, and the first sets the two-minute alarm", async () => {
  const { storage, post } = made();
  const answer = await post({ sender: "storage", records: [record(1), record(2)] });
  assert.deepEqual([answer.status, await answer.json()], [200, { kept: 2 }]);
  assert.equal(storage.alarm, START + 120_000);
  assert.deepEqual([...(await storage.list({ prefix: "r:" })).values()].map((r) => r.request_id), ["r1", "r2"]);
  await post({ sender: "storage", records: [record(3)] });
  assert.equal(storage.alarm, START + 120_000, "a set alarm isn't moved");
});

test("the alarm writes one file of the cycle's records into the sender's buffer, and re-arms", async () => {
  const { object, storage, calls, clock, post } = made();
  await post({ sender: "storage", records: [record(1), record(2)] });
  clock.now += 120_000;
  await object.alarm();
  const [[key, data]] = calls.files;
  assert.match(key, /^calls-storage\/inbox\/calls\/2027-01-15\/080200-storage-[0-9a-f]{16}\.avro$/);
  assert.deepEqual(metadata(data), {
    kind: "sender", sender: "storage", version: "call-log", at: "2027-01-15T08:02:00Z", cycle_start: "2027-01-15T08:00:00Z",
    cycle_end: "2027-01-15T08:02:00Z", calls: 2, drops: 0, buffer_size: 2, storage_failures: 0,
  });
  assert.equal((await storage.list({ prefix: "r:" })).size, 0, "sent records are gone");
  assert.equal(storage.alarm, START + 240_000);
});

test("an idle cycle still writes a report, so a quiet sender can be told from a stopped one", async () => {
  const { object, calls, clock, post } = made();
  await post({ sender: "storage", records: [] });
  for (let i = 0; i < 3; i++) {
    clock.now += 120_000;
    await object.alarm();
  }
  assert.equal(calls.files.size, 3);
  assert.deepEqual([...calls.files.values()].map((d) => metadata(d).calls), [0, 0, 0]);
});

test("a file R2 didn't take goes again as it was, under its name, and nothing is sent twice", async (t) => {
  t.mock.method(console, "error", () => {});
  const { object, calls, clock, post } = made();
  await post({ sender: "storage", records: [record(1)] });
  calls.down = true;
  clock.now += 120_000;
  await object.alarm();
  await post({ sender: "storage", records: [record(2)] });
  clock.now += 120_000;
  await object.alarm(); // the held file again, still refused, and no new file
  assert.equal(calls.files.size, 0);
  calls.down = false;
  clock.now += 120_000;
  await object.alarm();
  const files = [...calls.files.values()].map(metadata);
  assert.deepEqual(files.map((m) => [m.calls, m.storage_failures]), [[1, 0], [1, 2]]);
  assert.match([...calls.files.keys()][0], /080200-storage-/, "the held file keeps its first name");
});

test("past the limit the oldest records go and are counted", async () => {
  const { object, calls, clock, post } = made();
  const many = Array.from({ length: 10_003 }, (_, n) => record(n));
  await post({ sender: "storage", records: many });
  clock.now += 120_000;
  await object.alarm();
  const status = metadata([...calls.files.values()][0]);
  assert.deepEqual([status.calls, status.drops, status.buffer_size], [10_000, 3, 10_000]);
});

test("an instance is one sender's, and only a sender SENDERS names may use it", async () => {
  const { post } = made();
  assert.equal((await post({ sender: "stranger", records: [] })).status, 403);
  assert.equal((await post({ sender: "toString", records: [] })).status, 403);
  assert.equal((await post({ sender: "storage", records: [] })).status, 200);
  const other = await post({ sender: "other", records: [] });
  assert.deepEqual([other.status, (await other.json()).error], [409, "this instance is storage's"]);
  assert.equal((await post({ sender: "storage", records: "x" })).status, 400);
  assert.equal((await post({}, "GET")).status, 404);
  const bad = await made().object.fetch(new Request("https://call-log/records", { method: "POST", body: "{" }));
  assert.equal(bad.status, 400);
});

test("an instance nobody has sent to does nothing on an alarm, and the Worker answers nothing itself", async () => {
  const { object, calls, storage } = made();
  await object.alarm();
  assert.equal(calls.files.size, 0);
  assert.equal(storage.alarm, START + 120_000, "it re-arms, harmlessly");
  assert.equal((await worker.fetch(new Request("https://call-log/"))).status, 404);
});

test("the report is where health checks and statistics can join, as StorageSink's is", () => {
  assert.deepEqual(report("s", { started: START, drops: 1, peak: 4, failures: 2 }, [record(1)], START + 120_000), {
    kind: "sender", sender: "s", version: "call-log", at: "2027-01-15T08:02:00Z", cycle_start: "2027-01-15T08:00:00Z",
    cycle_end: "2027-01-15T08:02:00Z", calls: 1, drops: 1, buffer_size: 4, storage_failures: 2,
  });
});

// The binding a Worker gets for the object's namespace, here backed by a real CallLog.
function binding() {
  const { object, storage, calls, clock } = made();
  const names = [];
  return {
    object, storage, calls, clock, names,
    namespace: {
      idFromName: (name) => (names.push(name), { name }),
      get: () => ({ fetch: (url, init) => object.fetch(new Request(url, init)) }),
    },
  };
}

test("a sink keeps a request's records and sends them to its sender's object in one call", async () => {
  const b = binding();
  const sink = new CallLogSink({ sender: "storage", namespace: () => b.namespace });
  sink.write(record(1));
  sink.write(record(2));
  await sink.send();
  await sink.send(); // nothing left: no call
  assert.deepEqual(b.names, ["storage"]);
  assert.deepEqual([...(await b.storage.list({ prefix: "r:" })).values()].map((r) => r.request_id), ["r1", "r2"]);
  assert.deepEqual(sink.pending, []);
});

test("a sink that can't send says so and lets the records go, never failing the request", async (t) => {
  const said = [];
  t.mock.method(console, "error", (line) => said.push(line));
  const refusing = { idFromName: () => ({}), get: () => ({ fetch: async () => Response.json({ error: "stranger is not a sender here" }, { status: 403 }) }) };
  const sink = new CallLogSink({ sender: "stranger", namespace: refusing });
  sink.write(record(1));
  await sink.send();
  const broken = new CallLogSink({ sender: "storage", namespace: { idFromName: () => { throw new Error("no binding"); } } });
  broken.write(record(1));
  await broken.send();
  assert.deepEqual(said, [
    "integrations: the call log didn't take 1 records from stranger: HTTP 403 stranger is not a sender here",
    "integrations: the call log didn't take 1 records from storage: no binding",
  ]);
  assert.throws(() => new CallLogSink({ sender: "storage" }), /needs a sender and a namespace/);
});
