// Every call to an outside service goes through a Caller, which makes it and writes one
// record of it: src/integrations/calls.py, for Node 22 and Cloudflare Workers, with no
// dependencies. A record says who called what, how long it took and how it ended, in the
// same shape as Python's CallRecord. It never holds a body, a full URL or a token.
//
// The operation's class sets its timeout and how often it is tried, so a call a person
// is waiting on is not retried behind their back. A retry is only for a call that is
// safe to repeat, or that says it is. POLICIES and OPERATIONS above come from
// classes.json and plugins/*.json, the tables calls.py reads.
//
//   import { Caller, StorageSink } from "@sustenaut/integrations/caller.js";
//   const caller = new Caller("my-repo", "web", { sink: new StorageSink(...) });
//   const answer = await caller.call("github.repo-ops", "check-push",
//     { method: "GET", url: `https://api.github.com/repos/${repo}`, headers });

const IDEMPOTENT = new Set(["GET", "HEAD", "PUT", "DELETE"]);
const RETRY_STATUSES = new Set([429, 502, 503, 504]);
// Cloudflare refuses some default User-Agents before the request reaches whoever it was for.
const AGENT = "integrations-caller.js";

export class IntegrationError extends Error {
  constructor(plugin, operation, detail) {
    super(`${plugin} ${operation}: ${detail}`);
    this.name = new.target.name;
    Object.assign(this, { plugin, operation, detail });
  }
}

// The provider did not answer, or failed: another try may work.
export class Unavailable extends IntegrationError {}

// The provider answered no: trying again will not change that.
export class Refused extends IntegrationError {}

// What came back: a status and a body, or the error that stopped it. json() of an empty
// body is null, as in Python.
export class Answer {
  constructor(status, body = new Uint8Array(), { error = null, headers = new Headers() } = {}) {
    Object.assign(this, { status, body, error, headers, etag: headers.get("ETag") });
  }

  get ok() {
    return this.status !== null && this.status >= 200 && this.status < 300;
  }

  text() {
    return new TextDecoder().decode(this.body);
  }

  json() {
    const text = this.text();
    return text.trim() ? JSON.parse(text) : null;
  }
}

const utf8 = (text) => new TextEncoder().encode(text);

function byteLength(body) {
  if (body === null || body === undefined) return 0;
  if (typeof body === "string") return utf8(body).length;
  return body.byteLength ?? 0;
}

const now = () => new Date().toISOString().slice(0, 19) + "Z";
const nap = (seconds) => new Promise((resolve) => setTimeout(resolve, seconds * 1000));
const monotonic = globalThis.performance?.now ? () => performance.now() : () => Date.now();

// Each plugin's mode in a repository's integrations.json (already read: a Worker has no
// files). A plugin declared both ways counts as required.
export function readModes(data) {
  const pairs = (data.uses ?? []).map((u) => [u.plugin, u.mode ?? "required"]);
  for (const stack of data.terraform ?? []) for (const p of stack.plugins) pairs.push([p, stack.mode ?? "required"]);
  const modes = {};
  for (const [plugin, mode] of pairs) modes[plugin] = mode === "required" || modes[plugin] === "required" ? "required" : mode;
  return modes;
}

export class Caller {
  // fetch is the only thing that reaches the network; clock() is in milliseconds, sleep()
  // takes seconds, as calls.py's do.
  constructor(repo, app = "", { sink = new NullSink(), fetch = (...a) => globalThis.fetch(...a), modes = {}, clock = monotonic, sleep = nap, newId = () => crypto.randomUUID().replaceAll("-", "") } = {}) {
    Object.assign(this, { repo, app, sink, fetch, modes, clock, sleep, newId });
  }

  // The same caller, on behalf of a project, so its records are that project's.
  forRepo(repo) {
    const { app, sink, fetch, modes, clock, sleep, newId } = this;
    return new this.constructor(repo, app, { sink, fetch, modes, clock, sleep, newId });
  }

  mode(plugin) {
    return this.modes[plugin] ?? "required";
  }

  // Makes the call and writes its record. A provider's refusal comes back as its Answer,
  // for the client to read. Throws Unavailable when there is no answer, or only a failure,
  // after every try. refused(answer) is for a provider that says no in a successful
  // answer, so the record says so.
  async call(plugin, operation, { method, url, headers = {}, body = null, repeatable = false }, { requestId, refused } = {}) {
    const registered = registeredOperation(plugin, operation);
    const policy = POLICIES[registered.class];
    const request = { method, url, headers, body };
    const tries = repeatable || IDEMPOTENT.has(method) ? policy.tries : 1;
    const started = this.clock();
    let answer = await this.#send(request, policy.timeout);
    let retries = 0;
    while (retries + 1 < tries && worthRetrying(answer)) {
      await this.sleep(2 ** retries);
      retries += 1;
      answer = await this.#send(request, policy.timeout);
    }
    const latency = Math.trunc(this.clock() - started);
    const failed = answer.status === null || answer.status >= 500 || answer.status === 429;
    let outcome = "refused";
    if (failed) outcome = "unavailable";
    else if (answer.ok && !saysNo(answer, refused)) outcome = "ok";
    this.#keep(this.#record(plugin, operation, registered, {
      request_id: requestId ?? this.newId(), latency_ms: latency, status: answer.status, outcome, retries,
      bytes_in: answer.body.length, bytes_out: byteLength(body),
    }));
    if (failed) throw new Unavailable(plugin, operation, answer.error ?? `HTTP ${answer.status}`);
    return answer;
  }

  // A record of time that wasn't a call to a provider: how long a request took the
  // service, or the app that made it.
  note(plugin, operation, { requestId, latencyMs, status, outcome }) {
    const registered = registeredOperation(plugin, operation);
    this.#keep(this.#record(plugin, operation, registered, {
      request_id: requestId, latency_ms: latencyMs, status, outcome, retries: 0, bytes_in: 0, bytes_out: 0,
    }));
  }

  #record(plugin, operation, registered, fields) {
    const { request_id, latency_ms, status, outcome, retries, bytes_in, bytes_out } = fields;
    return {
      at: now(), repo: this.repo, app: this.app, plugin, operation, request_id, latency_ms, status, outcome,
      retries, bytes_in, bytes_out, mode: this.mode(plugin), route: registered.route,
    };
  }

  async #send({ method, url, headers, body }, timeout) {
    if (!url.startsWith("https://")) return new Answer(null, undefined, { error: "not https" });
    try {
      const response = await this.fetch(url, {
        method, headers: { "User-Agent": AGENT, ...headers }, body: body ?? undefined, signal: AbortSignal.timeout(timeout * 1000),
      });
      const bytes = new Uint8Array(await response.arrayBuffer());
      return new Answer(response.status, bytes, { headers: response.headers });
    } catch (err) {
      const why = err?.name === "TimeoutError" ? `timed out after ${timeout} s` : err?.message ?? String(err);
      return new Answer(null, undefined, { error: `no answer: ${why}` });
    }
  }

  // A sink that fails loses the record, not the call.
  #keep(record) {
    const lost = (err) => console.error(`integrations: could not keep a call record: ${err?.message ?? err}`);
    try {
      Promise.resolve(this.sink.write(record)).catch(lost);
    } catch (err) {
      lost(err);
    }
  }
}

function registeredOperation(plugin, operation) {
  const registered = OPERATIONS[plugin]?.[operation];
  if (!registered) throw new RangeError(`${plugin} ${operation} is not a registered operation`);
  return registered;
}

// A body that can't be read isn't a refusal here: the client deals with it.
function saysNo(answer, refused) {
  try {
    return Boolean(refused?.(answer));
  } catch {
    return false;
  }
}

const worthRetrying = (answer) => answer.status === null || RETRY_STATUSES.has(answer.status);

export class NullSink {
  // Keep nothing: the default until the call log exists.
  write() {}
}

export class MemorySink {
  records = [];

  write(record) {
    this.records.push(record);
  }
}

const NULL_BODY = new Set([101, 204, 205, 304]); // a Response with one of these can't have a body

// Recorded answers by "METHOD url", in calls.py's Replay format, as a fetch: for tests,
// and the shared vectors in tests/vectors/. A list answers in turn, its last one for
// good; one not recorded has no answer. A "json" answer's body is written as Python's
// json.dumps writes it, so both languages count the same bytes_in.
export class Replay {
  sent = [];

  constructor(responses) {
    this.responses = structuredClone(responses);
  }

  fetch = async (url, init = {}) => {
    const request = { method: init.method ?? "GET", url: String(url), headers: init.headers ?? {}, body: init.body ?? null };
    this.sent.push(request);
    let found = this.responses[`${request.method} ${request.url}`];
    if (Array.isArray(found)) found = found.length > 1 ? found.shift() : found[0];
    if (!found) throw new TypeError("not recorded");
    if ("error" in found) throw new TypeError(found.error);
    const text = "text" in found ? found.text : pythonJson(found.json ?? null);
    return new Response(NULL_BODY.has(found.status) ? null : text, { status: found.status });
  };
}

// json.dumps(value): ", " and ": " between items, and anything outside ASCII escaped.
export function pythonJson(value) {
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).map(([k, v]) => `${pythonJson(k)}: ${pythonJson(v)}`).join(", ")}}`;
  }
  if (typeof value === "string") {
    return JSON.stringify(value).replace(/[\u0080-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  }
  return JSON.stringify(value);
}

// ---------------------------------------------------------------------------------------
// Call records into a project's buffer in the storage service, as
// src/integrations/storage_sink.py writes them: one Avro file per dataset every
// everySeconds, even with no calls in it, whose metadata is the sender's report. So a
// quiet service can be told from one that has stopped.
//
// Writing a record never fails. Records wait in memory up to a limit, and past it the
// oldest are dropped and counted. A file storage didn't take is sent again as it was,
// under the same name, so a lost answer can't double its calls.
//
// Where it goes: { url, token } or { url, tokenFile } PUTs to the storage service, the
// file read for each request (Kubernetes renews it); { bucket, prefix } puts into an R2
// bucket binding, for the storage Worker, which can't call itself.
//
// A Worker has no background timer: the write that finds the file overdue sends it, and
// hands that work to waitUntil (set it per request: sink.waitUntil = (p) => ctx.waitUntil(p)),
// so the answer doesn't wait on storage. In Node, start: true also sends on a timer.

const EVERY_SECONDS = 120;
const PAGE_LIMIT = 10_000;
const PUT_TIMEOUT_SECONDS = 60;

export class StorageSink {
  constructor(datasets, { sender, url, token, tokenFile, bucket, prefix = "", version = "js", everySeconds = EVERY_SECONDS, limit = 10_000, clock = () => Date.now(), fetch = (...a) => globalThis.fetch(...a), readFile, waitUntil, flushOnWrite = true, start = false } = {}) {
    if (!sender) throw new TypeError("a StorageSink needs a sender");
    if (!bucket && !(url && (token || tokenFile))) throw new TypeError("a StorageSink needs a url and a token or tokenFile, or a bucket");
    Object.assign(this, { datasets, sender, version, everySeconds, limit, clock, fetch, waitUntil, flushOnWrite });
    this.target = bucket ? { bucket, prefix } : { url: url.replace(/\/+$/, ""), token, tokenFile, readFile };
    const names = [...new Set(Object.values(datasets))].sort();
    this.waiting = Object.fromEntries(names.map((n) => [n, []]));
    this.held = {};
    this.dropped = Object.fromEntries(names.map((n) => [n, 0]));
    this.failures = { ...this.dropped };
    this.peak = { ...this.dropped };
    this.pages = [];
    this.pageCount = 0;
    this.cold = true; // until the first report goes out
    this.started = clock();
    this.flushing = null; // one flush at a time, whoever starts it
    if (start) {
      this.timer = setInterval(() => void this.flush(), everySeconds * 1000);
      this.timer.unref?.();
    }
  }

  write(record) {
    const dataset = this.datasets[record.repo];
    if (dataset === undefined) {
      // On behalf of a project with no buffer: reported, on the first there is.
      const first = Object.keys(this.dropped).sort()[0];
      if (first !== undefined) this.dropped[first] += 1;
      return;
    }
    const waiting = this.waiting[dataset];
    if (waiting.length >= this.limit) {
      waiting.shift();
      this.dropped[dataset] += 1;
    }
    waiting.push({ ...record });
    this.peak[dataset] = Math.max(this.peak[dataset], waiting.length);
    const due = this.flushOnWrite && this.clock() - this.started >= this.everySeconds * 1000;
    if (due && !this.flushing) {
      const sent = this.flush();
      this.waitUntil?.(sent);
    }
  }

  // How long the app took to answer one request, in milliseconds.
  page(ms) {
    this.pageCount += 1;
    if (this.pages.length < PAGE_LIMIT) this.pages.push(ms);
  }

  // One file per project, whatever has happened since the last. Never rejects.
  flush() {
    if (!this.flushing) this.flushing = this.#flushAll().finally(() => (this.flushing = null));
    return this.flushing;
  }

  // Stops the timer and sends what is waiting.
  async close() {
    clearInterval(this.timer);
    await this.flush();
  }

  #pageReport() {
    const { pages, pageCount: count, cold } = this;
    Object.assign(this, { pages: [], pageCount: 0, cold: false });
    if (!pages.length) return { page_count: count, page_ms_low: null, page_ms_median: null, page_ms_high: null, cold_start: cold };
    return {
      page_count: count, page_ms_low: roundHalfEven(Math.min(...pages)), page_ms_median: roundHalfEven(median(pages)),
      page_ms_high: roundHalfEven(Math.max(...pages)), cold_start: cold,
    };
  }

  async #flushAll() {
    const started = this.started;
    const ended = this.clock();
    this.started = ended;
    const pages = this.#pageReport();
    for (const dataset of Object.keys(this.waiting)) await this.#flush(dataset, started, ended, pages);
  }

  async #flush(dataset, started, ended, pages) {
    if (this.held[dataset]) {
      if (!(await this.#put(dataset, ...this.held[dataset]))) return;
      delete this.held[dataset];
    }
    const batch = this.waiting[dataset];
    this.waiting[dataset] = [];
    const status = {
      kind: "sender", sender: this.sender, version: this.version, at: timestamp(ended), cycle_start: timestamp(started),
      cycle_end: timestamp(ended), calls: batch.length, drops: this.dropped[dataset], buffer_size: this.peak[dataset],
      storage_failures: this.failures[dataset], ...pages,
    };
    this.dropped[dataset] = this.failures[dataset] = this.peak[dataset] = 0;
    const data = await encode(batch, status);
    const name = await fileName(ended, this.sender, data);
    if (!(await this.#put(dataset, name, data))) this.held[dataset] = [name, data];
  }

  async #put(dataset, name, data) {
    const tenant = dataset.replaceAll("_", "-");
    let why;
    let noToken = "";
    try {
      if (this.target.bucket) {
        await this.target.bucket.put(`${this.target.prefix}${tenant}/${name}`, data);
        return true;
      }
      let token = "";
      try {
        token = await this.#token();
      } catch (err) {
        noToken = `; no token to send: ${err?.message ?? err}`;
      }
      const response = await this.fetch(`${this.target.url}/${tenant}/${name}`, {
        method: "PUT", headers: { Authorization: `Bearer ${token}`, "User-Agent": AGENT }, body: data,
        signal: AbortSignal.timeout(PUT_TIMEOUT_SECONDS * 1000),
      });
      if (response.ok) return true;
      why = `HTTP ${response.status} ${reason(await response.text().catch(() => ""))}`.trimEnd();
    } catch (err) {
      why = err?.name === "TimeoutError" ? `no answer: timed out after ${PUT_TIMEOUT_SECONDS} s` : `no answer: ${err?.message ?? err}`;
    }
    this.failures[dataset] += 1;
    // Counted in the next report, and said here too, for the process's own log, with
    // storage's own reason: it says why a token was refused, never what was in it.
    console.error(`integrations: storage didn't take ${name} for ${tenant}: ${why}${noToken}`);
    return false;
  }

  async #token() {
    const { token, tokenFile, readFile } = this.target;
    if (token) return typeof token === "function" ? await token() : token;
    // Named in a variable so a Worker's bundler doesn't look for node:fs, which only Node has.
    const fs = "node:fs/promises";
    const read = readFile ?? (await import(fs)).readFile;
    return (await read(tokenFile, "utf8")).trim();
  }
}

// Storage's {"error": ...}, kept short.
function reason(body) {
  try {
    const error = JSON.parse(body).error;
    return error === undefined ? "" : String(error).slice(0, 200);
  } catch {
    return "";
  }
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// Python's round(): a half goes to the even neighbour.
function roundHalfEven(x) {
  const floor = Math.floor(x);
  const rest = x - floor;
  if (rest !== 0.5) return Math.round(x);
  return floor % 2 === 0 ? floor : floor + 1;
}

// ---------------------------------------------------------------------------------------
// Call records as an Avro object container file, as src/integrations/avrofile.py writes
// them, with the null codec. The same records always give the same bytes. The sender's
// report travels in the file's metadata.

const CALL_SCHEMA = {
  type: "record", name: "call", fields: [
    { name: "at", type: { type: "long", logicalType: "timestamp-micros" } },
    ...["repo", "app", "plugin", "operation", "request_id"].map((name) => ({ name, type: "string" })),
    { name: "latency_ms", type: "long" },
    { name: "status", type: ["null", "long"] },
    { name: "outcome", type: "string" },
    ...["retries", "bytes_in", "bytes_out"].map((name) => ({ name, type: "long" })),
    { name: "mode", type: "string" },
    { name: "route", type: "string" },
  ],
};

export const timestamp = (ms) => new Date(Math.floor(ms / 1000) * 1000).toISOString().slice(0, 19) + "Z";

async function sha256(bytes) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

// Where a file goes in a buffer: named for when, who, and what is in it.
export async function fileName(ms, sender, data) {
  const at = timestamp(ms);
  const who = sender.replace(/[^A-Za-z0-9.-]/g, "-");
  return `inbox/calls/${at.slice(0, 10)}/${at.slice(11, 19).replaceAll(":", "")}-${who}-${hex(await sha256(data)).slice(0, 16)}.avro`;
}

// JSON with sorted keys, as json.dumps(..., sort_keys=True) writes it.
const sortedJson = (value) => (value && typeof value === "object" && !Array.isArray(value)
  ? `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}: ${sortedJson(value[k])}`).join(", ")}}`
  : JSON.stringify(value));

class Writer {
  parts = [];

  long(n) {
    let z = BigInt(n);
    z = z >= 0n ? z << 1n : (-z << 1n) - 1n;
    const out = [];
    do {
      let byte = Number(z & 0x7fn);
      z >>= 7n;
      if (z) byte |= 0x80;
      out.push(byte);
    } while (z);
    this.parts.push(Uint8Array.from(out));
  }

  bytes(b) {
    this.long(b.length);
    this.parts.push(b);
  }

  string(s) {
    this.bytes(utf8(s));
  }

  raw(b) {
    this.parts.push(b);
  }

  done() {
    const out = new Uint8Array(this.parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of this.parts) {
      out.set(p, at);
      at += p.length;
    }
    return out;
  }
}

export async function encode(records, status) {
  const marker = (await sha256(utf8(JSON.stringify(records.map((r) => sortedJson(r)).sort())))).slice(0, 16);
  const metadata = [
    ["avro.codec", "null"],
    ["avro.schema", JSON.stringify(CALL_SCHEMA)],
    ["integrations.schema", "call-record"],
    ...(status ? [["integrations.status", sortedJson(status)]] : []),
  ];
  const file = new Writer();
  file.raw(utf8("Obj\x01"));
  file.long(metadata.length);
  for (const [key, value] of metadata) {
    file.string(key);
    file.bytes(utf8(value));
  }
  file.long(0);
  file.raw(marker);
  if (records.length) {
    const block = new Writer();
    for (const r of records) {
      block.long(BigInt(Date.parse(r.at)) * 1000n);
      for (const name of ["repo", "app", "plugin", "operation", "request_id"]) block.string(r[name]);
      block.long(r.latency_ms);
      if (r.status === null) block.long(0);
      else {
        block.long(1);
        block.long(r.status);
      }
      block.string(r.outcome);
      for (const name of ["retries", "bytes_in", "bytes_out"]) block.long(r[name]);
      block.string(r.mode);
      block.string(r.route);
    }
    const data = block.done();
    file.long(records.length);
    file.bytes(data);
    file.raw(marker);
  }
  return file.done();
}
