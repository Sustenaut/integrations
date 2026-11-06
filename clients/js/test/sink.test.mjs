// caller.js's StorageSink: as tests/steps/test_storage_sink.py holds storage_sink.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { StorageSink, encode, fileName } from "../caller.js";

const START = 1_800_000_000_000; // 2027-01-15T08:00:00Z
const VECTORS = new URL("../../../tests/vectors/", import.meta.url);

const record = (n = 1, fields = {}) => ({
  at: "2026-11-02T10:00:00Z", repo: "a-repo", app: "web", plugin: "github.edit-pr", operation: "open-pr",
  request_id: `r${n}`, latency_ms: 12, status: 201, outcome: "ok", retries: 0, bytes_in: 3, bytes_out: 4,
  mode: "required", route: "routed", ...fields,
});

// The storage service's buffer, by PUT; down while down is true.
function storage() {
  const files = new Map();
  const sent = [];
  const self = {
    files, sent, down: false, status: 200, body: "{}",
    fetch: async (url, init) => {
      const { pathname } = new URL(url);
      sent.push({ path: pathname, init });
      if (self.down) throw new TypeError("fetch failed");
      if (self.status !== 200) return new Response(self.body, { status: self.status });
      files.set(pathname.slice(1), init.body);
      return new Response("{}");
    },
  };
  return self;
}

function world(options = {}) {
  const buffer = storage();
  const clock = { now: START };
  const sink = new StorageSink({ "a-repo": "ds", "b-repo": "ds" }, {
    sender: "app-1_x", url: "https://storage.example/", token: "pod-token", clock: () => clock.now, fetch: buffer.fetch,
    flushOnWrite: false, limit: 3, ...options,
  });
  return { sink, buffer, clock };
}

// An Avro file's metadata, read back: just enough of the container format to check it.
function metadata(bytes) {
  let at = 4; // after "Obj\x01"
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
  return meta;
}

const report = (bytes) => JSON.parse(metadata(bytes)["integrations.status"]);

test("an idle cycle still writes a report", async () => {
  const { sink, buffer, clock } = world();
  clock.now += 120_000;
  await sink.flush();
  const [[name, data]] = buffer.files;
  assert.match(name, /^ds\/inbox\/calls\/2027-01-15\/080200-app-1-x-[0-9a-f]{16}\.avro$/);
  assert.deepEqual(report(data), {
    kind: "sender", sender: "app-1_x", version: "js", at: "2027-01-15T08:02:00Z", cycle_start: "2027-01-15T08:00:00Z",
    cycle_end: "2027-01-15T08:02:00Z", calls: 0, drops: 0, buffer_size: 0, storage_failures: 0, page_count: 0,
    page_ms_low: null, page_ms_median: null, page_ms_high: null, cold_start: true,
  });
  assert.equal(metadata(data)["avro.codec"], "null");
  assert.equal(buffer.sent[0].init.headers.Authorization, "Bearer pod-token");
  assert.equal(buffer.sent[0].init.method, "PUT");
  assert.ok(buffer.sent[0].init.signal instanceof AbortSignal);
});

test("each dataset is its own tenant, and a cycle's calls are one file", async () => {
  const buffer = storage();
  const sink = new StorageSink({ "a-repo": "proj_a", "b-repo": "proj_b" }, { sender: "s", url: "https://storage.example", token: () => "pod-token", fetch: buffer.fetch, flushOnWrite: false });
  sink.write(record(1));
  sink.write(record(2));
  sink.write(record(3, { repo: "b-repo" }));
  await sink.flush();
  const names = [...buffer.files.keys()];
  assert.deepEqual(names.map((n) => n.split("/")[0]), ["proj-a", "proj-b"]);
  assert.deepEqual([report(buffer.files.get(names[0])).calls, report(buffer.files.get(names[1])).calls], [2, 1]);
});

test("past the limit the oldest go and are counted, as are a project's with no buffer", async () => {
  const { sink, buffer } = world();
  for (let n = 1; n <= 5; n++) sink.write(record(n));
  sink.write(record(6, { repo: "elsewhere" }));
  assert.deepEqual(sink.waiting.ds.map((r) => r.request_id), ["r3", "r4", "r5"]);
  await sink.flush();
  const status = report([...buffer.files.values()][0]);
  assert.deepEqual([status.calls, status.drops, status.buffer_size], [3, 3, 3]);
  const none = new StorageSink({}, { sender: "s", bucket: { put: async () => {} } });
  none.write(record());
  await none.flush();
});

test("a refused file goes again as it was, under its name, and the failures are reported", async (t) => {
  const said = [];
  t.mock.method(console, "error", (line) => said.push(line));
  const { sink, buffer, clock } = world();
  sink.write(record(1));
  buffer.status = 403;
  buffer.body = JSON.stringify({ error: "github:x may not write here" });
  clock.now += 120_000;
  await sink.flush();
  assert.equal(buffer.files.size, 0);
  const first = buffer.sent[0].path;
  sink.write(record(2));
  clock.now += 120_000;
  await sink.flush(); // still refused: the held file again, and nothing new
  assert.deepEqual(buffer.sent.map((s) => s.path), [first, first]);
  buffer.status = 200;
  clock.now += 120_000;
  await sink.flush();
  assert.equal(buffer.sent[2].path, first);
  const [held, next] = [...buffer.files.values()];
  assert.equal(report(held).calls, 1);
  assert.deepEqual([report(next).calls, report(next).storage_failures], [1, 2]);
  assert.match(said[0], /^integrations: storage didn't take inbox\/calls\/.+\.avro for ds: HTTP 403 github:x may not write here$/);
});

test("no answer, a status without a reason, and a token that can't be read are failed writes too", async (t) => {
  const said = [];
  t.mock.method(console, "error", (line) => said.push(line));
  const { sink, buffer } = world();
  buffer.down = true;
  await sink.flush();
  assert.match(said[0], /: no answer: fetch failed$/);
  buffer.down = false;
  buffer.status = 500;
  buffer.body = "<html>";
  await sink.flush();
  assert.match(said[1], /: HTTP 500$/);
  const files = storage();
  const reading = new StorageSink({ "a-repo": "ds" }, { sender: "s", url: "https://s.example", tokenFile: "/no/such/token", fetch: files.fetch, flushOnWrite: false });
  files.status = 401;
  await reading.flush();
  assert.equal(files.sent[0].init.headers.Authorization, "Bearer ");
  assert.match(said[2], /HTTP 401; no token to send: ENOENT/);
});

test("the token file is read for each request", async () => {
  let token = "first";
  const reads = [];
  const buffer = storage();
  const sink = new StorageSink({ "a-repo": "ds" }, {
    sender: "s", url: "https://s.example", tokenFile: "/var/run/token", fetch: buffer.fetch, flushOnWrite: false,
    readFile: async (path, encoding) => (reads.push([path, encoding]), `${token}\n`),
  });
  await sink.flush();
  token = "second";
  await sink.flush();
  assert.deepEqual(buffer.sent.map((s) => s.init.headers.Authorization), ["Bearer first", "Bearer second"]);
  assert.deepEqual(reads[0], ["/var/run/token", "utf8"]);
});

test("into an R2 bucket binding, under its prefix, for the storage Worker", async () => {
  const put = [];
  let failing = false;
  const bucket = { put: async (key, data) => { if (failing) throw new Error("R2 is down"); put.push([key, data]); } };
  const sink = new StorageSink({ "a-repo": "proj_a" }, { sender: "storage", bucket, prefix: "", flushOnWrite: false });
  sink.write(record());
  failing = true;
  await sink.flush();
  failing = false;
  await sink.flush();
  assert.equal(put.length, 2);
  assert.match(put[0][0], /^proj-a\/inbox\/calls\/.+-storage-[0-9a-f]{16}\.avro$/);
  assert.equal(report(put[1][1]).storage_failures, 1);
  const prefixed = new StorageSink({ "a-repo": "ds" }, { sender: "s", bucket, prefix: "x/", flushOnWrite: false });
  await prefixed.flush();
  assert.match(put[2][0], /^x\/ds\/inbox\//);
});

test("a write that finds the file overdue sends it, and hands that to waitUntil", async () => {
  const handed = [];
  const { sink, buffer, clock } = world({ flushOnWrite: true, waitUntil: (p) => handed.push(p) });
  sink.write(record(1));
  assert.equal(handed.length, 0, "not yet due");
  clock.now += 120_000;
  sink.write(record(2));
  sink.write(record(3)); // a flush is under way: not another
  assert.equal(handed.length, 1);
  await handed[0];
  assert.equal(buffer.files.size, 1);
  assert.equal(report([...buffer.files.values()][0]).calls, 2);
  assert.deepEqual(sink.waiting.ds.map((r) => r.request_id), ["r3"]);
});

test("by default, a Worker's way: flush from write, with no timer", async () => {
  const buffer = storage();
  const clock = { now: START };
  const sink = new StorageSink({ "a-repo": "ds" }, { sender: "s", url: "https://s.example", token: "t", fetch: buffer.fetch, clock: () => clock.now });
  assert.equal(sink.timer, undefined);
  clock.now += 120_000;
  sink.write(record());
  await sink.flushing;
  assert.equal(buffer.files.size, 1);
});

test("in Node, start: true sends on a timer, first a random part of a cycle in, and close() stops it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const buffer = storage();
  const sink = new StorageSink({ "a-repo": "ds" }, { sender: "s", url: "https://s.example", token: "t", fetch: buffer.fetch, start: true, everySeconds: 120, random: () => 0.25 });
  t.mock.timers.tick(29_999);
  assert.equal(buffer.sent.length, 0);
  t.mock.timers.tick(1);
  await sink.flushing;
  assert.equal(buffer.sent.length, 1, "30 s in: a quarter of the cycle");
  t.mock.timers.tick(120_000);
  await sink.flushing;
  assert.equal(buffer.sent.length, 2, "then every cycle");
  await sink.close();
  t.mock.timers.tick(240_000);
  assert.equal(buffer.sent.length, 3);
});

test("a sink needs a sender and somewhere to send", () => {
  assert.throws(() => new StorageSink({}, { url: "https://s", token: "t" }), /needs a sender/);
  assert.throws(() => new StorageSink({}, { sender: "s", url: "https://s" }), /needs a url and a token/);
});

test("page times become the window's low, median and high, rounded as Python rounds", async () => {
  const { sink, buffer } = world();
  for (const ms of [3, 2, 9, 1]) sink.page(ms); // median 2.5: Python's round() makes it 2
  await sink.flush();
  sink.page(0.5);
  sink.page(1.5);
  sink.page(2.5);
  await sink.flush();
  await sink.flush();
  const [first, second, third] = [...buffer.files.values()].map(report);
  assert.deepEqual([first.page_count, first.page_ms_low, first.page_ms_median, first.page_ms_high, first.cold_start], [4, 1, 2, 9, true]);
  assert.deepEqual([second.page_ms_low, second.page_ms_median, second.page_ms_high, second.cold_start], [0, 2, 2, false]);
  assert.deepEqual([third.page_count, third.page_ms_median], [0, null]);
});

test("past the limit page times are still counted", async () => {
  const { sink, buffer } = world();
  for (let i = 0; i < 10_005; i++) sink.page(1);
  assert.equal(sink.pages.length, 10_000);
  await sink.flush();
  assert.equal(report([...buffer.files.values()][0]).page_count, 10_005);
});

test("the same records give the same bytes, and the name holds their hash", async () => {
  const a = await encode([record(1), record(2)], { kind: "sender" });
  assert.deepEqual(a, await encode([record(1), record(2)], { kind: "sender" }));
  assert.notDeepEqual(a, await encode([record(1)], { kind: "sender" }));
  assert.match(await fileName(START, "a b/c", a), /^inbox\/calls\/2027-01-15\/080000-a-b-c-[0-9a-f]{16}\.avro$/);
  assert.equal(metadata(await encode([]))["integrations.status"], undefined);
});

// tests/vectors/sink.avro, which test_vectors.py reads with avrofile.decode and file_status.
test("the file in tests/vectors/ is what the sink writes", async () => {
  const put = [];
  const clock = { now: START };
  const sink = new StorageSink({ "a-repo": "ds" }, { sender: "app-1_x", version: "0.0.0-test", bucket: { put: async (key, data) => put.push([key, data]) }, clock: () => clock.now, flushOnWrite: false });
  const records = [
    record(1),
    record(2, { status: null, outcome: "unavailable", retries: 2, latency_ms: 30_000, bytes_in: 0 }),
    record(3, { app: "", operation: "check-push", plugin: "github.repo-ops", mode: "advisory", request_id: "ünïcode-ünd-𝄞" }),
    record(4, { at: "1999-12-31T23:59:59Z", latency_ms: 2 ** 40, status: 429, bytes_out: 0 }),
  ];
  for (const r of records) sink.write(r);
  for (const ms of [5, 7, 40]) sink.page(ms);
  clock.now += 120_000;
  await sink.flush();
  const [[key, data]] = put;
  const name = key.slice("ds/".length);
  const expected = { sender: "app-1_x", at: clock.now / 1000, name, records, report: report(data) };
  if (process.env.WRITE_VECTORS) {
    await writeFile(new URL("sink.avro", VECTORS), data);
    await writeFile(new URL("sink.json", VECTORS), `${JSON.stringify(expected, null, 2)}\n`);
  }
  assert.deepEqual(new Uint8Array(await readFile(new URL("sink.avro", VECTORS))), data);
  assert.deepEqual(JSON.parse(await readFile(new URL("sink.json", VECTORS), "utf8")), expected);
});
