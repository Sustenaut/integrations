// clients/js/caller.js: what tests/vectors/ doesn't cover.
import { Given, Then, When } from "@cucumber/cucumber";
import { mock } from "node:test";
import { Answer, Caller, MemorySink, NullSink, OPERATIONS, POLICIES, pythonJson, readModes } from "../caller.js";
import { Cassette } from "../transports.js";
import { given, repositoryJson, saidSleep, toFakes } from "./helpers.mjs";

// A request as a step gives it: {"bytes": [...]} for a body of bytes.
function request(doc) {
  const made = JSON.parse(doc);
  if (made.body && typeof made.body === "object" && "bytes" in made.body) made.body = new Uint8Array(made.body.bytes);
  return made;
}

function made(world) {
  return new Caller("a-repo", "web", world.options);
}

Given("a caller", function () {
  this.sink = new MemorySink();
  this.options = { sink: this.sink, fetch: toFakes(this), sleep: saidSleep, newId: () => "id-1" };
});

Given("the time is {string}", function (at) {
  mock.timers.enable({ apis: ["Date"], now: Date.parse(at) });
});

Given("its clock reads, in turn, {string}", function (readings) {
  const left = readings.split(",").map(Number);
  this.options.clock = () => (left.length > 1 ? left.shift() : left[0]);
});

Given("its random source gives {float}", function (n) {
  this.options.random = () => n;
});

Given("it sleeps for real", function () {
  delete this.options.sleep;
});

Given("it calls {string} in {string} mode", function (plugin, mode) {
  this.options.modes = { ...this.options.modes, [plugin]: mode };
});

Given("its sink {word}, saying {string}", function (how, why) {
  this.options.sink = how === "throws"
    ? { write: () => { throw new Error(why); } }
    : { write: async () => { throw new Error(why); } };
});

// The caller's clock, in milliseconds, moves only as it sleeps and as each try takes time.
function standingClock(world) {
  world.now = 0;
  world.options.clock = () => world.now;
  world.options.sleep = async (s) => {
    process.stdout.write(`slept ${s}\n`);
    world.now += s * 1000;
  };
}

Given("its clock moves only as it sleeps and as each try takes {float} s", function (seconds) {
  standingClock(this);
  this.tryTakes = seconds * 1000;
});

Given("its clock moves only as it sleeps and as each try takes its whole timeout", function () {
  standingClock(this);
  // The clock moves when the try's signal would have fired, and says the timeout it gave.
  const real = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => {
    process.stdout.write(`timeout ${ms}\n`);
    this.now += ms;
    return real.call(AbortSignal, ms);
  };
  this.pubtest.cleanup(() => (AbortSignal.timeout = real));
});

Given("its fetch is a stand-in that answers {int}", function (status) {
  this.options.fetch = async (url, init) => {
    process.stdout.write(`${await given(url, init)}\n`);
    return new Response("{}", { status });
  };
});

Given("its fetch is a stand-in that answers {int} with the header {string} set to {string}", function (status, name, value) {
  this.options.fetch = async (url, init) => {
    process.stdout.write(`${await given(url, init)}\n`);
    this.now += this.tryTakes ?? 0;
    return new Response("{}", { status, headers: { [name]: value } });
  };
});

Given("its fetch is a stand-in that times out", function () {
  this.options.fetch = async () => {
    throw new DOMException("The operation timed out.", "TimeoutError");
  };
});

Given("the global fetch answers {int}", function (status) {
  mock.method(globalThis, "fetch", async () => new Response("{}", { status }));
});

Given("the {string} service answers {string} with {int} and a Retry-After an hour {word}", async function (name, req, status, when) {
  const at = Date.now() + (when === "ahead" ? 3600e3 : -3600e3);
  this.pubtest.fakes[name].answer(req, status, "{}", { "Retry-After": new Date(at).toUTCString() });
});

Given("a replay of:", function (doc) {
  this.replay = new Cassette({ responses: JSON.parse(doc) });
});

When("it calls {string} {string} with:", async function (plugin, operation, doc) {
  const started = Date.now();
  // A record a sink failed to take asynchronously is said by the next turn of the event loop.
  await this.attempt(async () => {
    const answer = await made(this).call(plugin, operation, request(doc));
    await new Promise((resolve) => setImmediate(resolve));
    return answer;
  });
  this.took = Date.now() - started;
});

When("the caller for {string} calls {string} {string} with:", async function (repo, plugin, operation, doc) {
  await this.attempt(() => made(this).forRepo(repo).call(plugin, operation, request(doc)));
});

When("a caller with only a repository calls {string} {string} with:", async function (plugin, operation, doc) {
  await this.attempt(() => new Caller("a-repo").call(plugin, operation, request(doc)));
});

When("it notes {string} {string} with:", async function (plugin, operation, doc) {
  await this.attempt(() => made(this).note(plugin, operation, JSON.parse(doc)));
});

When("I read the answer's JSON", async function () {
  const answer = this.pubtest.result;
  await this.attempt(() => answer.json());
});

When("I read the JSON of an answer with status {int}", async function (status) {
  await this.attempt(() => new Answer(status).json());
});

When("I make an answer with no status", async function () {
  await this.attempt(() => new Answer(null));
});

When("I make its caller for {string}", async function (repo) {
  await this.attempt(() => {
    const first = made(this);
    return { first, copy: first.forRepo(repo) };
  });
});

When("a record is written to the default sink", async function () {
  await this.attempt(() => new NullSink().write({}));
});

When("I read the modes of:", async function (doc) {
  await this.attempt(() => readModes(JSON.parse(doc)));
});

When("I fetch {string} from it {int} times", async function (url, times) {
  await this.attempt(async () => {
    const statuses = [];
    for (let i = 0; i < times; i++) statuses.push((await this.replay.fetch(url)).status);
    return statuses;
  });
});

When("I write as Python's json.dumps does:", async function (doc) {
  await this.attempt(() => pythonJson(JSON.parse(doc)));
});

// What differs between classes.json's timeouts and tries and caller.js's POLICIES, by class.
When("I compare caller.js's policies with {string}", async function (file) {
  const classes = await repositoryJson(this, file);
  await this.attempt(() => Object.entries(classes)
    .filter(([id, c]) => JSON.stringify(POLICIES[id]) !== JSON.stringify({ timeout: c.timeout, tries: c.tries }))
    .map(([id]) => id));
});

// What differs between a plugin file's operations and caller.js's OPERATIONS, by name.
When("I compare caller.js's operations of {string} with {string}", async function (plugin, file) {
  const { operations } = await repositoryJson(this, file);
  await this.attempt(() => operations
    .filter((o) => JSON.stringify(OPERATIONS[plugin]?.[o.name]) !== JSON.stringify({ class: o.class, route: o.route }))
    .map((o) => o.name));
});

Then("it took at least {int} ms", function (ms) {
  if (!(this.took >= ms)) throw new Error(`it took ${this.took} ms`);
});
