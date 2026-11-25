// The cassette vectors in tests/vectors/cassettes/, which tests/steps/test_vectors.py runs on
// transports.py too, and the steps of transports.feature: modes, use(), the Caller's
// default transport and caller.fetch.
import { Given, When } from "@cucumber/cucumber";
import { dig } from "@sustenaut/pubtest";
import { mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { Caller, MemorySink, pythonJson } from "../caller.js";
import { Cassette, Faulty, LIVE, Recorder, cassetteReports, checkMode, currentTransport, promote, record, use, useClock } from "../transports.js";
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
    const recorder = vector.record ? new Recorder(scripted(vector.record, clock), null, { scenario: vector.scenario ?? null, clock: () => clock.now, names: vector.names ?? {} }) : null;
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

// A path in the scratch directory as written, its ".." left for the transports to resolve.
const scratch = (world, p) => `${world.pubtest.directory}${path.sep}${p}`;

Given("the environment sets {string} to {string} in the scratch directory", function (name, value) {
  const was = process.env[name];
  process.env[name] = scratch(this, value);
  this.pubtest.cleanup(() => {
    if (was === undefined) delete process.env[name];
    else process.env[name] = was;
  });
});

Given("{string} is a symlink to {string}", async function (link, target) {
  const at = scratch(this, link);
  await mkdir(path.dirname(at), { recursive: true });
  await symlink(target, at);
});

When("a recorder made for {string} is saved as {string}", async function (made, saved) {
  await this.attempt(() => new Recorder(LIVE, scratch(this, made)).save(scratch(this, saved)));
});

When("a recorder made for {string} is saved", async function (made) {
  await this.attempt(() => new Recorder(LIVE, scratch(this, made)).save());
});

When("I promote {string}", async function (file) {
  await this.attempt(() => promote(scratch(this, file)));
});

Given("a file {string} holding the cassette of {string}", async function (file, vector) {
  await writeFile(scratch(this, file), JSON.stringify((await repositoryJson(this, vector)).cassette));
});

async function callCurrent(world, plugin, operation, asked) {
  const [method, url] = asked.split(" ");
  world.sink ??= new MemorySink();
  const caller = new Caller("a-repo", "web", { sink: world.sink, newId: () => "id-1", sleep: async () => {} });
  const answer = await caller.call(plugin, operation, { method, url });
  return { status: answer.status, json: answer.json() };
}

When("a caller given no fetch calls {string} {string} with {string}", async function (plugin, operation, asked) {
  await this.attempt(() => callCurrent(this, plugin, operation, asked));
});

When("a caller given no fetch calls {string} {string} with {string}, and the process reports its cassette", async function (plugin, operation, asked) {
  const file = process.env.INTEGRATIONS_CASSETTE;
  await this.attempt(async () => {
    await callCurrent(this, plugin, operation, asked);
    const { cassette, ...report } = cassetteReports().filter((r) => r.cassette === file).at(-1);
    return { ...report, named: cassette === file };
  });
});

// A 503 then a 200, on a caller made before the harness's clock, which starts at 100 s,
// moves on with each sleep, and jitters a quarter of the way.
async function underHarnessClock(world, plugin, operation, asked, given = {}) {
  const [method, url] = asked.split(" ");
  let now = 100_000;
  const slept = [];
  world.sink = new MemorySink();
  const replay = new Cassette({ responses: { [`${method} ${url}`]: [{ status: 503 }, { status: 200, json: {} }] } });
  const caller = new Caller("a-repo", "web", { sink: world.sink, fetch: replay.fetch, newId: () => "id-1", ...given });
  const restore = useClock({ clock: () => now, sleep: async (s) => { slept.push(s); now += s * 1000; }, random: () => 0.25 });
  try {
    const answer = await caller.call(plugin, operation, { method, url });
    return { status: answer.status, slept, latency_ms: world.sink.records.at(-1).latency_ms };
  } finally {
    restore();
  }
}

When("a caller made before the harness's clock calls {string} {string} with {string}, answered 503 and then 200", async function (plugin, operation, asked) {
  await this.attempt(() => underHarnessClock(this, plugin, operation, asked));
});

When("a caller with its own sleep, made before the harness's clock, calls {string} {string} with {string}, answered 503 and then 200", async function (plugin, operation, asked) {
  const own = [];
  await this.attempt(async () => ({ ...(await underHarnessClock(this, plugin, operation, asked, { sleep: async (s) => { own.push(s); } })), own }));
});
