// clients/js/storage.js, on the vectors in tests/vectors/storage/ that
// tests/steps/test_storage_client.py runs too, and what they don't cover.
import { Given, When } from "@cucumber/cucumber";
import { Caller, MemorySink, Replay } from "../caller.js";
import { SPEC, Storage, StorageError, StorageUnavailable } from "../storage.js";
import { given, repositoryJson, saidSleep, toFakes } from "./helpers.mjs";

const BASE = "https://storage.example";
const camel = (name) => name.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
const value = (arg) => (arg && typeof arg === "object" && "bytes" in arg ? new TextEncoder().encode(arg.bytes) : arg);

// A result as the vectors write it: bytes as {text}.
function plain(result) {
  if (result instanceof Uint8Array) return { text: new TextDecoder().decode(result) };
  if (result && typeof result === "object" && "hash" in result && result.body instanceof Uint8Array) return { body: plain(result.body), hash: result.hash };
  return result ?? null;
}

Given("the storage call in {string}", async function (file) {
  this.vector = await repositoryJson(this, file);
});

// What the call came to, in the vector's "expect" shape: the error raised, the result,
// the sleeps, each request sent (its method and URL, and the headers and JSON the vector
// names), and each record's fields the vector names.
When("storage.js makes it", async function () {
  const vector = this.vector;
  await this.attempt(async () => {
    const sink = new MemorySink();
    const naps = [];
    const replay = new Replay(vector.responses);
    const caller = new Caller("a-repo", "web", { sink, fetch: replay.fetch, sleep: async (s) => naps.push(s), newId: () => "id-1", random: () => 1 });
    const { tenant, token, request_id: requestId, user_agent: userAgent } = vector.client;
    const storage = new Storage({ tenant, token, requestId, userAgent, baseUrl: BASE, caller });
    const kwargs = Object.fromEntries(Object.entries(vector.kwargs).map(([k, v]) => [camel(k), v]));
    const { expect } = vector;
    let raises = null;
    let result = null;
    try {
      result = plain(await storage[camel(vector.call)](...vector.args.map(value), ...(Object.keys(kwargs).length ? [kwargs] : [])));
    } catch (err) {
      if (!(err instanceof StorageError || err instanceof StorageUnavailable)) throw err;
      raises = { type: err.constructor.name, status: err.status, error: err.error };
    }
    const sent = replay.sent.map((s, i) => {
      const want = expect.sent[i] ?? {};
      return {
        method: s.method, url: s.url,
        ...(want.headers ? { headers: Object.fromEntries(Object.keys(want.headers).map((k) => [k, s.headers[k]])) } : {}),
        ...(want.json ? { json: JSON.parse(s.body) } : {}),
      };
    });
    const records = sink.records.map((r, i) => Object.fromEntries(Object.keys(expect.records[i] ?? {}).map((k) => [k, r[k]])));
    return { raises, result, sleeps: naps, sent, records };
  });
});

// What differs between storage.js's SPEC and the lock's repository, commit and sha256.
When("I compare storage.js's SPEC with {string}", async function (file) {
  const lock = await repositoryJson(this, file);
  const want = { repository: lock.repository, commit: lock.commit, sha256: lock.sha256 };
  await this.attempt(() => [...new Set([...Object.keys(SPEC), ...Object.keys(want)])].filter((k) => SPEC[k] !== want[k]));
});

function client(world, options) {
  world.sink = new MemorySink();
  world.options = { sink: world.sink, fetch: toFakes(world), sleep: saidSleep, newId: () => "id-1", random: () => 1 };
  world.client = () => new Storage({ baseUrl: BASE, caller: new Caller("a-repo", "web", world.options), ...options });
}

Given("a storage client for the tenant {string} with the token {string}", function (tenant, token) {
  client(this, { tenant, token });
});

Given("a storage client for the tenant {string} whose token a function gives as {string}", function (tenant, token) {
  client(this, { tenant, token: async () => token });
});

// The token file says each read on stdout, and holds each token in turn, its last for good.
Given("a storage client for the tenant {string} whose token file {string} holds {string}, then {string}", function (tenant, tokenFile, first, second) {
  const tokens = [first, second];
  const readFile = async (path, encoding) => {
    process.stdout.write(`read ${path} ${encoding}\n`);
    return `${tokens.length > 1 ? tokens.shift() : tokens[0]}\n`;
  };
  client(this, { tenant, tokenFile, readFile });
});

Given("its fetch is a stand-in", function () {
  this.options.fetch = async (url, init) => {
    process.stdout.write(`${await given(url, init)}\n`);
    return new Response("{}", { status: 200 });
  };
});

When("it uploads {string} as:", async function (text, doc) {
  await this.attempt(() => this.client().uploadFile(new Blob([text]), JSON.parse(doc)));
});

When("it calls {string} with:", async function (method, doc) {
  this.made = this.client();
  await this.attempt(() => this.made[method](...JSON.parse(doc)));
});

When("its client for the tenant {string} calls {string} with:", async function (tenant, method, doc) {
  await this.attempt(() => this.made.forTenant(tenant)[method](...JSON.parse(doc)));
});
