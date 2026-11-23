// workers/call-log's Durable Object, on fakes of its storage and R2, and caller.js's
// CallLogSink that sends it records.
import { Given, Then, When } from "@cucumber/cucumber";
import { fromFetch } from "@sustenaut/pubtest/http";
import { CallLog, report } from "../../../workers/call-log/src/call-log.js";
import worker from "../../../workers/call-log/src/call-log.js";
import { CallLogSink, noticeSignature } from "../caller.js";
import { bucket, records } from "./helpers.mjs";

const START = 1_800_000_000_000; // 2027-01-15T08:00:00Z

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

Given("a call-log object whose senders are:", function (doc) {
  this.storage = durable();
  this.calls = bucket(this);
  this.clock = { now: START };
  this.object = new CallLog({ storage: this.storage }, { SENDERS: doc, CALLS: this.calls }, { now: () => this.clock.now });
});

// The object again, with these vars beside SENDERS and CALLS, and a fetch that keeps each
// notice it is sent to the ingest pod and answers 202, or fails while ingest is down.
Given("the object's vars are:", function (doc) {
  const env = { SENDERS: this.object.env.SENDERS, CALLS: this.calls, ...JSON.parse(doc) };
  this.notices = [];
  const fetch = async (url, init) => {
    this.notices.push({ url, ...init });
    if (this.ingestDown) throw new TypeError("fetch failed");
    return new Response("{}", { status: 202 });
  };
  this.object = new CallLog({ storage: this.storage }, env, { now: () => this.clock.now, fetch });
});

Given("ingest is down", function () {
  this.ingestDown = true;
});

When("{int} seconds pass and the alarm rings", async function (seconds) {
  this.clock.now += seconds * 1000;
  await this.attempt(() => this.object.alarm());
});

When("{int} seconds pass, the alarm silent", function (seconds) {
  this.clock.now += seconds * 1000;
});

// Each notice: where it went, its body but the name's hash, and whether its signature is
// the key's over its timestamp and body.
Then("ingest was told, with the key {string}, of:", async function (key, doc) {
  const told = [];
  for (const n of this.notices ?? []) {
    const body = JSON.parse(n.body);
    const seconds = n.headers["X-Ingest-Timestamp"];
    told.push({ url: n.url, tenant: body.tenant, name: body.name.replace(/-[0-9a-f]{16}\.avro$/, ".avro"),
      sized: body.size > 0, at: seconds, signed: n.headers["X-Ingest-Signature"] === await noticeSignature(key, seconds, n.body) });
  }
  const wanted = JSON.parse(doc);
  if (JSON.stringify(told) !== JSON.stringify(wanted)) throw new Error(`ingest was told of ${JSON.stringify(told)}`);
});

Then("ingest was told of nothing", function () {
  if ((this.notices ?? []).length) throw new Error(`ingest was told of ${JSON.stringify(this.notices)}`);
});

Given("R2 is down", function () {
  this.calls.down = true;
});

Given("the service is the call-log object", function () {
  this.serve = fromFetch((request) => this.object.fetch(request), "https://call-log");
});

Given("the service is the call-log Worker", function () {
  this.serve = fromFetch((request) => worker.fetch(request), "https://call-log");
});

async function send(world, sender, sent) {
  const answer = await world.object.fetch(new Request("https://call-log/records", { method: "POST", body: JSON.stringify({ sender, records: sent }) }));
  return { status: answer.status, body: await answer.json() };
}

When("{string} sends the records {string}", async function (sender, ids) {
  await this.attempt(() => send(this, sender, records(ids)));
});

When("{string} sends {int} records", async function (sender, n) {
  await this.attempt(() => send(this, sender, Array.from({ length: n }, (_, i) => records(`r${i}`)[0])));
});

When("a minute passes", function () {
  this.clock.now += 60_000;
});

When("two minutes pass and the alarm rings", async function () {
  this.clock.now += 120_000;
  await this.attempt(() => this.object.alarm());
});

When("the alarm rings", async function () {
  await this.attempt(() => this.object.alarm());
});

When("R2 comes back", function () {
  this.calls.down = false;
});

When("I make {string}'s report of the records {string}, two minutes after the start, with the counters:", async function (sender, ids, doc) {
  await this.attempt(() => report(sender, JSON.parse(doc), records(ids), START + 120_000));
});

// The binding a Worker gets for the object's namespace, here backed by the scenario's
// CallLog; it says on stdout each object it gives.
Given("a CallLogSink for {string}, bound to the object", function (sender) {
  const namespace = {
    idFromName: (name) => (process.stdout.write(`the binding gave the object of ${name}\n`), { name }),
    get: () => ({ fetch: (url, init) => this.object.fetch(new Request(url, init)) }),
  };
  this.callLogSink = new CallLogSink({ sender, namespace: () => namespace });
});

Given("a CallLogSink for {string}, bound to an object that answers {int} with:", function (sender, status, doc) {
  const namespace = { idFromName: () => ({}), get: () => ({ fetch: async () => new Response(doc, { status, headers: { "Content-Type": "application/json" } }) }) };
  this.callLogSink = new CallLogSink({ sender, namespace });
});

Given("a CallLogSink for {string}, bound to nothing, saying {string}", function (sender, why) {
  this.callLogSink = new CallLogSink({ sender, namespace: { idFromName: () => { throw new Error(why); } } });
});

// What is left in the sink once it has sent.
When("it writes the records {string} and sends", async function (ids) {
  await this.attempt(async () => {
    for (const r of records(ids)) this.callLogSink.write(r);
    await this.callLogSink.send();
    return this.callLogSink.pending;
  });
});

When("it writes the records {string} and sends twice", async function (ids) {
  await this.attempt(async () => {
    for (const r of records(ids)) this.callLogSink.write(r);
    await this.callLogSink.send();
    await this.callLogSink.send(); // nothing left: no call
    return this.callLogSink.pending;
  });
});

Then("the alarm is set for {int} s after the start", function (seconds) {
  if (this.storage.alarm !== START + seconds * 1000) throw new Error(`the alarm is set for ${(this.storage.alarm - START) / 1000} s after the start`);
});

Then("the object keeps the records {string}", async function (ids) {
  const kept = [...(await this.storage.list({ prefix: "r:" })).values()].map((r) => r.request_id).join(",");
  if (kept !== ids) throw new Error(`the object keeps the records ${JSON.stringify(kept)}`);
});
