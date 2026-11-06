// The call log's buffer for code that runs on Cloudflare, where no process lives long
// enough to hold records for two minutes: one Durable Object per sender, which keeps each
// record in its own durable storage the moment it arrives, and on its alarm, every
// EVERY_SECONDS whether or not anything came, writes the cycle's Avro file into that
// sender's buffer in R2, as clients/js/caller.js's StorageSink and storage_sink.py do: the
// records, and the sender's report in the file's metadata. So nothing waits in a Worker's
// memory, and a quiet sender can be told from a stopped one.
//
// A Worker binds it (a durable_objects binding with script_name "call-log") and sends
// each request's records with caller.js's CallLogSink, one POST /records per request:
//   { "sender": "storage", "records": [CallRecord, ...] }
// SENDERS (wrangler.toml) is the fixed map from a sender to its dataset; its tenant is the
// dataset's name with "-" for "_", and an instance writes nothing outside that tenant's
// inbox/. An instance is one sender's: the first sender it is sent pins it.
//
// The cycle's payload is report() below, so health checks and other statistics can join
// the sender's report there.

import { encode, fileName, timestamp } from "../../../clients/js/caller.js";

const EVERY_SECONDS = 120;
const LIMIT = 10_000; // records an instance keeps; past it the oldest go, and are counted
const BATCH_DELETE = 128; // keys Durable Object storage deletes at once
const RECORD = "r:";

export class CallLog {
  constructor(state, env, { now = () => Date.now() } = {}) {
    Object.assign(this, { storage: state.storage, env, now });
    this.senders = typeof env.SENDERS === "string" ? JSON.parse(env.SENDERS) : env.SENDERS ?? {};
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/records") return Response.json({ error: "no such route" }, { status: 404 });
    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ error: "the body is JSON" }, { status: 400 });
    }
    const { sender, records } = body ?? {};
    if (!Object.hasOwn(this.senders, sender)) return Response.json({ error: `${sender} is not a sender here` }, { status: 403 });
    if (!Array.isArray(records)) return Response.json({ error: "records is a list" }, { status: 400 });
    const pinned = await this.storage.get("sender");
    if (pinned && pinned !== sender) return Response.json({ error: `this instance is ${pinned}'s` }, { status: 409 });
    if (!pinned) await this.storage.put({ sender, started: this.now() });
    await this.#keep(records);
    if ((await this.storage.getAlarm()) === null) await this.storage.setAlarm(this.now() + EVERY_SECONDS * 1000);
    return Response.json({ kept: records.length });
  }

  // The instance's counters: the next record's number, the oldest kept, and the cycle's
  // drops, peak and failures.
  async #counters() {
    const found = Object.fromEntries(await this.storage.get(["next", "first", "drops", "peak", "failures", "started"]));
    return { next: 0, first: 0, drops: 0, peak: 0, failures: 0, ...found };
  }

  async #keep(records) {
    const c = await this.#counters();
    const entries = {};
    for (const record of records) entries[key(c.next++)] = record;
    if (records.length) await this.storage.put(entries);
    const over = c.next - c.first - LIMIT;
    if (over > 0) {
      await this.#delete(range(c.first, c.first + over));
      c.first += over;
      c.drops += over;
    }
    await this.storage.put({ next: c.next, first: c.first, drops: c.drops, peak: Math.max(c.peak, c.next - c.first) });
  }

  // The cycle: a held file first, then one file of everything kept, even nothing. The sent
  // records are deleted, and the file held, before R2 is asked, so a request that arrives
  // meanwhile keeps its records for the next cycle and none is sent twice.
  async alarm() {
    try {
      const sender = await this.storage.get("sender");
      if (!sender) return;
      const tenant = this.senders[sender].replaceAll("_", "-");
      const held = await this.storage.get("held");
      if (held) {
        if (!(await this.#put(tenant, held.name, held.data))) return;
        await this.storage.delete("held");
      }
      const ended = this.now();
      const kept = await this.storage.list({ prefix: RECORD });
      const records = [...kept.values()];
      const c = await this.#counters();
      const data = await encode(records, report(sender, c, records, ended));
      const name = await fileName(ended, sender, data);
      await this.#delete([...kept.keys()]);
      const first = kept.size ? Number([...kept.keys()].at(-1).slice(RECORD.length)) + 1 : c.first;
      await this.storage.put({ first, started: ended, drops: 0, peak: c.next - first, failures: 0, held: { name, data } });
      if (await this.#put(tenant, name, data)) await this.storage.delete("held");
    } finally {
      await this.storage.setAlarm(this.now() + EVERY_SECONDS * 1000);
    }
  }

  // One file into the sender's buffer. A failure is counted in the next report, and the
  // file is sent again as it was, under the same name, so a lost answer can't double it.
  async #put(tenant, name, data) {
    try {
      await this.env.CALLS.put(`${tenant}/${name}`, data);
      return true;
    } catch (err) {
      await this.storage.put("failures", ((await this.storage.get("failures")) ?? 0) + 1);
      console.error(`call-log: R2 didn't take ${name} for ${tenant}: ${err?.message ?? err}`);
      return false;
    }
  }

  async #delete(keys) {
    for (let i = 0; i < keys.length; i += BATCH_DELETE) await this.storage.delete(keys.slice(i, i + BATCH_DELETE));
  }
}

// The sender's report for the cycle, as StorageSink's: the place for health checks and
// other statistics to join it.
export function report(sender, { started, drops = 0, peak = 0, failures = 0 }, records, ended) {
  return {
    kind: "sender", sender, version: "call-log", at: timestamp(ended), cycle_start: timestamp(started ?? ended),
    cycle_end: timestamp(ended), calls: records.length, drops, buffer_size: peak, storage_failures: failures,
  };
}

const key = (n) => `${RECORD}${String(n).padStart(16, "0")}`;
const range = (from, to) => Array.from({ length: to - from }, (_, i) => key(from + i));

// The Worker itself answers nothing: only bindings reach its objects.
export default {
  fetch: () => new Response("call-log: bind to it as a Durable Object\n", { status: 404 }),
};
