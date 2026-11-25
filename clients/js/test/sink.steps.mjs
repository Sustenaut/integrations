// caller.js's StorageSink: as tests/steps/test_storage_sink.py holds storage_sink.py.
// Its storage is a stand-in fetch that keeps each file it is sent in the scratch
// directory, under the URL's path, and says on stdout, when a Given asks, what it was
// sent (as a line of JSON, its body left out: that is the file).
import { Given, Then, When } from "@cucumber/cucumber";
import { mock } from "node:test";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { StorageSink, encode, fileName } from "../caller.js";
import { avroMetadata, bucket, given } from "./helpers.mjs";

const START = 1_800_000_000_000; // 2027-01-15T08:00:00Z

const record = (n = 1, fields = {}) => ({
  at: "2026-11-02T10:00:00Z", repo: "a-repo", app: "web", plugin: "github.edit-pr", operation: "open-pr",
  request_id: `r${n}`, latency_ms: 12, status: 201, outcome: "ok", retries: 0, bytes_in: 3, bytes_out: 4,
  mode: "required", route: "routed", ...fields,
});
const records = (ids, fields) => ids.split(",").filter(Boolean).map((id) => record(Number(id.slice(1)), fields));

function storage(world) {
  const self = {
    down: false, status: 200, body: "{}", says: false, sent: 0, written: [],
    fetch: async (url, init) => {
      self.sent += 1;
      if (self.says) process.stdout.write(`${await given(url, { ...init, body: undefined })}\n`);
      if (self.down) throw new TypeError("fetch failed");
      if (self.status !== 200) return new Response(self.body, { status: self.status });
      const file = path.join(world.pubtest.directory, new URL(url).pathname);
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(file, init.body);
      self.written.push(new URL(url).pathname.split("/")[1]);
      return new Response("{}");
    },
  };
  return self;
}

function sink(world) {
  world.made ??= new StorageSink(world.datasets, world.sinkOptions);
  return world.made;
}

Given("a StorageSink {string} for:", function (sender, doc) {
  this.store = storage(this);
  this.clock = { now: START };
  this.datasets = JSON.parse(doc);
  this.sinkOptions = {
    sender, url: "https://storage.example/", token: "pod-token", clock: () => this.clock.now, fetch: this.store.fetch,
    flushOnWrite: false, limit: 3,
  };
});

Given("a StorageSink {string} into an R2 bucket with the prefix {string}, for:", function (sender, prefix, doc) {
  this.calls = bucket(this);
  this.clock = { now: START };
  this.datasets = JSON.parse(doc);
  this.sinkOptions = { sender, bucket: this.calls, prefix, clock: () => this.clock.now, flushOnWrite: false };
});

Given("its deadline is {int} seconds and no heartbeat", function (deadline) {
  Object.assign(this.sinkOptions, { deadlineSeconds: deadline, heartbeatSeconds: undefined, random: () => 1 });
});

Given("its deadline is {int} seconds and its heartbeat {int}", function (deadline, heartbeat) {
  Object.assign(this.sinkOptions, { deadlineSeconds: deadline, heartbeatSeconds: heartbeat, random: () => 1 });
});

Given("its heartbeat is {int} seconds, the first {float} of the way in", function (heartbeat, share) {
  Object.assign(this.sinkOptions, { heartbeatSeconds: heartbeat, random: () => share });
});

Given("its deadline and heartbeat are the environment's", function () {
  delete this.sinkOptions.deadlineSeconds;
  delete this.sinkOptions.heartbeatSeconds;
  this.sinkOptions.random = () => 1;
});

When("{int} seconds pass", function (seconds) {
  sink(this);
  this.clock.now += seconds * 1000;
});

// What it wrote, as the tenant of each file storage took, from the start.
When("it sends what is due", async function () {
  await this.attempt(async () => {
    await sink(this).flushDue();
    return this.store.written;
  });
});

Given("its limit is the default", function () {
  delete this.sinkOptions.limit;
});

Given("its token is a function giving {string}", function (token) {
  this.sinkOptions.token = () => token;
});

// The token file says each read on stdout, and holds each token in turn, its last for good.
Given("its token is read from {string}, which holds {string}, then {string}", function (tokenFile, first, second) {
  const tokens = [first, second];
  delete this.sinkOptions.token;
  Object.assign(this.sinkOptions, {
    tokenFile,
    readFile: async (p, encoding) => {
      process.stdout.write(`read ${p} ${encoding}\n`);
      return `${tokens.length > 1 ? tokens.shift() : tokens[0]}\n`;
    },
  });
});

Given("its token is read from {string}, which isn't there", function (tokenFile) {
  delete this.sinkOptions.token;
  this.sinkOptions.tokenFile = tokenFile;
});

Given("it flushes from a write that finds the file overdue, handing that to waitUntil", function () {
  this.handed = [];
  Object.assign(this.sinkOptions, {
    flushOnWrite: true,
    waitUntil: (p) => {
      process.stdout.write("handed a flush to waitUntil\n");
      this.handed.push(p);
    },
  });
});

Given("it flushes as it does by default", function () {
  delete this.sinkOptions.flushOnWrite;
});

Given("storage says on stdout what it is sent", function () {
  this.store.says = true;
});

Given("storage is down", function () {
  this.store.down = true;
});

Given("storage refuses with {int} and:", function (status, doc) {
  Object.assign(this.store, { status, body: doc });
});

When("storage takes files again", function () {
  Object.assign(this.store, { status: 200, down: false });
});

// The sink is made by the scenario's first When, at the start, if it isn't yet.
When("two minutes pass", function () {
  sink(this);
  this.clock.now += 120_000;
});

// What waits to be sent, by dataset, as request ids; a flush the write handed to
// waitUntil is waited for, as the Worker's runtime would.
When("it is given the records {string}", async function (ids) {
  await this.attempt(() => given_(this, records(ids)));
});

When("it is given the records {string} for {string}", async function (ids, repo) {
  await this.attempt(() => given_(this, records(ids, { repo })));
});

async function given_(world, sent) {
  const made = sink(world);
  for (const r of sent) made.write(r);
  await Promise.all(world.handed ?? []);
  await made.flushing;
  return Object.fromEntries(Object.entries(made.waiting).map(([d, rs]) => [d, rs.map((r) => r.request_id)]));
}

When("it flushes", async function () {
  await this.attempt(() => sink(this).flush());
});

When("it flushes {int} times", async function (times) {
  await this.attempt(async () => {
    for (let i = 0; i < times; i++) await sink(this).flush();
  });
});

When("it times pages of {string} ms", async function (times) {
  await this.attempt(() => {
    for (const ms of times.split(",")) sink(this).page(Number(ms));
    return sink(this).pages;
  });
});

When("it times {int} pages of {int} ms", async function (n, ms) {
  await this.attempt(() => {
    for (let i = 0; i < n; i++) sink(this).page(ms);
    return sink(this).pages;
  });
});

When("it is made", async function () {
  await this.attempt(() => sink(this));
});

// A Node sink started on a timer: how many files it has sent after each step of the
// timeline, the last two after close().
When("it is started with a random part of {float} of a {int} s cycle, and the timers run {string} ms, then it is closed and they run {int} ms more", async function (part, every, steps, after) {
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  Object.assign(this.sinkOptions, { start: true, everySeconds: every, random: () => part });
  delete this.sinkOptions.flushOnWrite;
  await this.attempt(async () => {
    const made = sink(this);
    const counts = [];
    for (const ms of steps.split(",").map(Number)) {
      mock.timers.tick(ms);
      await made.flushing;
      counts.push(this.store.sent);
    }
    await made.close();
    counts.push(this.store.sent);
    mock.timers.tick(after);
    await made.flushing;
    counts.push(this.store.sent);
    return counts;
  });
});

When("I name a file for {string} at the start, of the records {string} with a sender's report", async function (sender, ids) {
  await this.attempt(async () => fileName(START, sender, await encode(records(ids), { kind: "sender" })));
});

When("I encode no records, with no report, into {string}", async function (file) {
  await this.attempt(async () => writeFile(path.join(this.pubtest.directory, file), await encode([])));
});

// tests/vectors/sink.avro, which test_vectors.py reads with avrofile.decode and file_status;
// WRITE_VECTORS=1 rewrites it, and sink.json, from what the sink writes.
When("the sink of the vector writes its records and page times, two minutes in", async function () {
  this.calls = bucket(this);
  this.clock = { now: START };
  const made = new StorageSink({ "a-repo": "ds" }, { sender: "app-1_x", version: "0.0.0-test", bucket: this.calls, clock: () => this.clock.now, flushOnWrite: false });
  await this.attempt(async () => {
    const written = [
      record(1),
      record(2, { status: null, outcome: "unavailable", retries: 2, latency_ms: 30_000, bytes_in: 0 }),
      record(3, { app: "", operation: "check-push", plugin: "github.repo-ops", mode: "advisory", request_id: "ünïcode-ünd-𝄞" }),
      record(4, { at: "1999-12-31T23:59:59Z", latency_ms: 2 ** 40, status: 429, bytes_out: 0 }),
    ];
    for (const r of written) made.write(r);
    for (const ms of [5, 7, 40]) made.page(ms);
    this.clock.now += 120_000;
    await made.flush();
    const [file] = await readdir(path.join(this.pubtest.directory, "ds/inbox/calls/2027-01-15"));
    const name = `inbox/calls/2027-01-15/${file}`;
    const data = await readFile(path.join(this.pubtest.directory, "ds", name));
    const report = JSON.parse(avroMetadata(new Uint8Array(data))["integrations.status"]);
    const expected = { sender: "app-1_x", at: this.clock.now / 1000, name, records: written, report };
    if (process.env.WRITE_VECTORS) {
      const vectors = path.join(this.pubtest.repository, "tests/vectors");
      await writeFile(path.join(vectors, "sink.avro"), data);
      await writeFile(path.join(vectors, "sink.json"), `${JSON.stringify(expected, null, 2)}\n`);
    }
    return expected;
  });
});

Then("the file {string} is the repository's {string}", async function (file, source) {
  const got = await readFile(path.join(this.pubtest.directory, file));
  const want = await readFile(path.join(this.pubtest.repository, source));
  if (!got.equals(want)) throw new Error(`${file} is not ${source}: ${got.length} bytes, not ${want.length}`);
});
