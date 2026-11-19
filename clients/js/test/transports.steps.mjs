// The cassette vectors in tests/vectors/cassettes/, which tests/steps/test_vectors.py runs on
// transports.py too, and the steps of transports.feature: modes, use(), the Caller's
// default transport and caller.fetch.
import { Given, When } from "@cucumber/cucumber";
import { dig } from "@sustenaut/pubtest";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { Caller, MemorySink, pythonJson } from "../caller.js";
import { Cassette, Faulty, Recorder, checkMode, currentTransport, promote, record, use } from "../transports.js";
import { repositoryJson } from "./helpers.mjs";

const utf8 = (text) => new TextEncoder().encode(text);

function bodyBytes(body) {
  if (body === undefined || body === null) return null;
  if ("repeat" in body) return utf8(body.repeat.repeat(body.times));
  if ("json" in body) return utf8(pythonJson(body.json));
  if ("text" in body) return utf8(body.text);
  return Uint8Array.from(atob(body.base64), (c) => c.charCodeAt(0));
}

// The provider of a recording vector: its answers in turn, each taking its elapsed_ms.
function scripted(answers, clock) {
  const left = [...answers];
  return {
    fetch: async () => {
      const a = left.shift();
      clock.now += a.elapsed_ms ?? 0;
      if (a.status === null || a.status === undefined) throw new TypeError(String(a.error).replace(/^no answer: /, ""));
      return new Response(bodyBytes(a.body), { status: a.status, headers: a.headers ?? {} });
    },
  };
}

// An answer as the vector expects it: status, headers (names in lower case), body.
async function answered(response) {
  const headers = {};
  response.headers.forEach((v, k) => {
    headers[k] = v;
  });
  const bytes = new Uint8Array(await response.arrayBuffer());
  const out = { status: response.status, headers };
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    out.base64 = btoa(String.fromCharCode(...bytes));
    return out;
  }
  try {
    out.json = JSON.parse(text);
  } catch {
    out.text = text;
  }
  return out;
}

// {{N.key}} in a call's text, as the value at the dotted key of the Nth result.
function fill(value, results) {
  if (Array.isArray(value)) return value.map((v) => fill(v, results));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fill(v, results)]));
  if (typeof value !== "string") return value;
  const whole = /^\{\{(\d+)\.([^}]+)\}\}$/.exec(value);
  if (whole) return dig(results[Number(whole[1])], whole[2]);
  return value.replace(/\{\{(\d+)\.([^}]+)\}\}/g, (_, n, key) => String(dig(results[Number(n)], key)));
}

const refusal = (err, wanted) => ({ raises: err.name, mentions: (wanted?.mentions ?? []).filter((m) => String(err.message).includes(m)) });

Given("the cassette vector {string}", async function (name) {
  this.vector = await repositoryJson(this, `tests/vectors/cassettes/${name}`);
});

// Each step of the vector on a Cassette, a Recorder or Faulty, as the vector says; what
// came of each, in the shape of the vector's "expect".
When("the transports run it", async function () {
  const vector = this.vector;
  const directory = this.pubtest.directory;
  await this.attempt(async () => {
    const clock = { now: 0 };
    let slept = 0;
    const recorder = vector.record ? new Recorder(scripted(vector.record, clock), null, { scenario: vector.scenario ?? null, clock: () => clock.now }) : null;
    const chain = (base) => (vector.faults ? new Faulty(base, vector.faults, { advance: (s) => { slept += s; } }) : base);
    let cassette = vector.cassette ? new Cassette(vector.cassette, { mode: "replay" }) : null;
    let transport = chain(recorder ?? cassette);
    let saved = null;
    const results = [];
    for (const [i, step] of vector.steps.entries()) {
      const wanted = vector.expect.results[i];
      try {
        if (step.call) {
          const asked = fill(step.call, results);
          try {
            const response = await transport.fetch(asked.url, {
              method: asked.method, headers: asked.headers ?? {}, body: bodyBytes(asked.body),
              integrations: { plugin: asked.plugin, operation: asked.operation, timeout: asked.timeout ?? 10 },
            });
            results.push(await answered(response));
          } catch (err) {
            if (!(err instanceof TypeError)) throw err;
            results.push({ error: `no answer: ${err.message}` });
          }
        } else if (step.save || step.promote) {
          const file = path.join(directory, "cassette.json");
          if (saved !== null) await writeFile(file, JSON.stringify(saved));
          if (step.save) recorder.save(file);
          else promote(file);
          const text = await readFile(file, "utf8");
          saved = JSON.parse(text);
          const shown = structuredClone(saved);
          delete shown.current.recorded;
          if (shown.baseline) delete shown.baseline.recorded;
          results.push({ saved: shown, leaked: (vector.absent ?? []).filter((t) => text.includes(t)) });
        } else if (step.replay) {
          cassette = new Cassette(saved, { mode: "replay" });
          transport = chain(cassette);
          results.push({ replayed: true });
        } else if (step.unused) {
          results.push({ unused: cassette.unused() });
        } else if (step.close) {
          cassette.close();
          results.push({ closed: true });
        } else if ("rules" in step) {
          results.push({ rules: record(step.rules || null) });
        }
      } catch (err) {
        results.push(refusal(err, wanted));
      }
    }
    return vector.faults ? { results, slept } : { results };
  });
});

When("I ask for the current transport", async function () {
  await this.attempt(() => {
    const transport = currentTransport();
    return transport.constructor === Object ? "live" : transport.constructor.name;
  });
});

When("I check the mode", async function () {
  await this.attempt(() => checkMode());
});

// A cassette from a vector's "cassette".
Given("a cassette from {string}", async function (file) {
  this.cassette = new Cassette((await repositoryJson(this, file)).cassette, { mode: "replay" });
});

When("a caller given no fetch calls {string} {string} with {string} while the cassette is in use", async function (plugin, operation, asked) {
  const [method, url] = asked.split(" ");
  this.sink = new MemorySink();
  const caller = new Caller("a-repo", "web", { sink: this.sink, newId: () => "id-1", sleep: async () => {} });
  const restore = use(this.cassette);
  try {
    await this.attempt(async () => (await caller.call(plugin, operation, { method, url })).json());
  } finally {
    restore();
  }
});

When("I ask for the current transport after the cassette is used and put back", async function () {
  use(this.cassette)();
  await this.attempt(() => (currentTransport().constructor === Object ? "live" : currentTransport().constructor.name));
});

// What pubpr's propose() does with the fetch it is given: send, read ok, status and JSON.
async function proposeLike(fetch, url) {
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer t" }, body: JSON.stringify({ title: "A", head: "b", base: "main" }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw Object.assign(new Error(`GitHub said ${response.status}: ${detail.message}`), { status: response.status });
  }
  return { status: response.status, json: await response.json(), type: response.headers.get("content-type") };
}

When("code that takes a fetch opens a pull request at {string} through caller.fetch {string} {string}", async function (url, plugin, operation) {
  this.sink = new MemorySink();
  const caller = new Caller("a-repo", "web", { sink: this.sink, fetch: this.cassette.fetch, newId: () => "id-1" });
  await this.attempt(() => proposeLike(caller.fetch(plugin, operation), url));
});

When("code that takes a fetch opens a pull request at {string} through caller.fetch {string} {string}, with no answer", async function (url, plugin, operation) {
  this.sink = new MemorySink();
  const caller = new Caller("a-repo", "web", { sink: this.sink, fetch: async () => { throw new TypeError("down"); }, newId: () => "id-1" });
  await this.attempt(() => proposeLike(caller.fetch(plugin, operation), url));
});

When("I make a fetch of {string} {string} from a caller", async function (plugin, operation) {
  await this.attempt(() => new Caller("a-repo").fetch(plugin, operation));
});
