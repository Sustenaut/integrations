// The steps of github-recording.feature: testing-github.js's customer, its steps
// defined here as pubtest's register(api, { customers: [github] }) defines them, against
// a stand-in for the test org and a stand-in for pubtest's tape.
import { Given, Then, When } from "@cucumber/cucumber";
import { dig } from "@sustenaut/pubtest";
import { generateKeyPairSync } from "node:crypto";
import path from "node:path";
import { Caller, MemorySink } from "../caller.js";
import { github, hold, repositories, token, useNetwork } from "../testing-github.js";
import { Cassette, NAMES, Recorder, namesFrom, use } from "../transports.js";
import { StandInGitHub } from "./github-standin.mjs";

// The customer's steps, each given the scenario's pubtest context, as register() does.
const [holds, pushes] = github.steps;
Given(holds[1], function (name, doc) {
  return holds[2](this.pubtest, name, doc);
});
Given(pushes[1], function (branch, name, doc) {
  return pushes[2](this.pubtest, branch, name, doc);
});

Given("a stand-in GitHub test org", function () {
  this.standin = new StandInGitHub("SustenautTest", ["ghs_recorder"]);
  this.pubtest.cleanup(useNetwork(this.standin.fetch));
});

// What pubtest's Tape gives a customer: namesOf(), which marks the customer for its
// cleanup, run when the scenario ends.
Given("the scenario runs under pubtest's harness", function () {
  const tape = {
    names: {},
    async namesOf(customer) {
      return (this.names[customer] ??= {});
    },
  };
  this.pubtest.tape = tape;
  this.pubtest.cleanup(async () => {
    if (github.name in tape.names) await github.cleanup(tape, tape.names[github.name]);
  });
});

Given("the recorder app's id {string}, installation id {string} and a key of its own are in the environment", function (app, installation) {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const was = Object.fromEntries(["GITHUB_TEST_RECORDER_APP_ID", "GITHUB_TEST_RECORDER_INSTALLATION_ID", "GITHUB_TEST_RECORDER_APP_KEY"].map((n) => [n, process.env[n]]));
  process.env.GITHUB_TEST_RECORDER_APP_ID = app;
  process.env.GITHUB_TEST_RECORDER_INSTALLATION_ID = installation;
  process.env.GITHUB_TEST_RECORDER_APP_KEY = privateKey.export({ type: "pkcs8", format: "pem" });
  Object.assign(this.standin, { publicKey, appId: app });
  this.pubtest.cleanup(() => {
    for (const [n, v] of Object.entries(was)) {
      if (v === undefined) delete process.env[n];
      else process.env[n] = v;
    }
  });
});

// The value with each repository's real name in the org as <r>.
function shown(world, value) {
  let text = JSON.stringify(value);
  for (const r of Object.values(repositories(world.pubtest))) {
    if (r.recorded !== r.name) text = text.replaceAll(r.recorded.split("/")[1], "<r>");
  }
  return JSON.parse(text);
}

When("I read the stand-in test org", async function () {
  await this.attempt(() => {
    const logical = Object.fromEntries(Object.values(repositories(this.pubtest)).map((r) => [r.recorded.split("/")[1], r.name]));
    return Object.fromEntries(Object.entries(this.standin.repos).map(([name, repo]) => [logical[name] ?? name, {
      private: repo.private, description: repo.description,
      branches: Object.fromEntries(Object.entries(repo.refs).map(([b, at]) => [b, { files: this.standin.files(name, b), commits: this.standin.ancestors(at).size }])),
    }]));
  });
});

When("I read INTEGRATIONS_NAMES", async function () {
  await this.attempt(() => ({ recorded: repositories(this.pubtest)["o/r"].recorded, names: shown(this, namesFrom()) }));
});

When("I set up the GitHub repository {string} holding {string}", async function (name, files) {
  await this.attempt(() => hold(this.pubtest, name, JSON.parse(files)));
});

When("pubtest cleans up the scenario's test customer", async function () {
  await this.attempt(async () => {
    const { names } = this.pubtest.tape;
    await github.cleanup(this.pubtest.tape, names[github.name]);
    return { left: Object.keys(this.standin.repos).sort(), [NAMES]: process.env[NAMES], names };
  });
});

When("I ask how the stand-in test org was called", async function () {
  await this.attempt(() => this.standin.requests.map((r) => ({ call: `${r.plugin} ${r.operation} ${r.method} ${r.path}`, auth: r.auth.split(".").length === 3 ? "a JWT" : r.auth })));
});

When("I ask for the token the code under test calls GitHub with", async function () {
  await this.attempt(() => token(this.pubtest));
});

When("I list the scenario's GitHub repositories", async function () {
  await this.attempt(() => Object.fromEntries(Object.entries(repositories(this.pubtest)).map(([n, r]) => [n, { recorded: r.recorded, files: r.files, pushes: r.pushes }])));
});

// --- the code under test --------------------------------------------------------------

Given("the code under test records its calls", function () {
  this.recorder = new Recorder(this.standin.fetch);
  this.pubtest.cleanup(use(this.recorder));
});

Given("the code under test replays the cassette in {string}", function (file) {
  this.cassette = new Cassette(path.join(this.pubtest.repository, file), { mode: "replay" });
  this.pubtest.cleanup(use(this.cassette));
});

// What a client of GitHub does, through a Caller given no fetch: the one the harness
// uses, a Recorder or a Cassette.
async function codeUnderTest(world, name) {
  const caller = new Caller("a-repo", "web", { sink: (world.sink ??= new MemorySink()), sleep: async () => {} });
  const base = `https://api.github.com/repos/${repositories(world.pubtest)[name].recorded}`;
  const headers = { Authorization: `Bearer ${await token(world.pubtest)}` };
  return (operation, method, at, body) => caller.call("github.edit-pr", operation, { method, url: base + at, headers, body: body === undefined ? null : JSON.stringify(body) });
}

When("the code under test reads the repository {string}", async function (name) {
  await this.attempt(async () => (await (await codeUnderTest(this, name))("read", "GET", "")).json());
});

Given("the code under test read {string} of {string}", async function (branch, name) {
  const answer = await (await codeUnderTest(this, name))("read", "GET", `/git/ref/heads/${branch}`);
  if (!answer.ok) throw new Error(`reading ${branch} answered ${answer.status}`);
  this.read = { ...this.read, [branch]: answer.json().object.sha };
});

When("the code under test commits {string} as {string} on what it read of {string} of {string}", async function (file, text, branch, name) {
  await this.attempt(async () => {
    const call = await codeUnderTest(this, name);
    const parent = this.read[branch];
    const tree = (await call("read", "GET", `/git/commits/${parent}`)).json().tree.sha;
    const made = (await call("open-pr", "POST", "/git/trees", { base_tree: tree, tree: [{ path: file, mode: "100644", type: "blob", content: text }] })).json().sha;
    const commit = (await call("open-pr", "POST", "/git/commits", { message: "mine", tree: made, parents: [parent] })).json().sha;
    const answer = await call("open-pr", "PATCH", `/git/refs/heads/${branch}`, { sha: commit, force: false });
    return { status: answer.status, message: answer.json().message };
  });
});

Then("the recording's answer {int} is:", function (n, doc) {
  const call = this.recorder.recording().calls[n];
  const body = call.response.body.json;
  const got = { url: call.request.url, full_name: body.full_name, html_url: body.html_url, owner: body.owner.login };
  const wanted = JSON.parse(doc);
  if (JSON.stringify(got) !== JSON.stringify(wanted)) throw new Error(`the recording's answer ${n} is ${JSON.stringify(got)}, not ${JSON.stringify(wanted)}`);
});

Then("every call the cassette holds was made", function () {
  const left = this.cassette.unused();
  if (left.length) throw new Error(`never made: ${left.join(", ")}`);
});

// All of text is pattern, in which only * is special, as pubtest's matches.
const matches = (pattern, text) => new RegExp(`^${pattern.split("*").map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`, "s").test(text);

Then("the result's {string} matches {string}", function (key, pattern) {
  if (this.pubtest.refused) throw new Error(`it was refused: ${this.pubtest.refused.message}`);
  const got = dig(this.pubtest.result, key);
  if (typeof got !== "string" || !matches(pattern, got)) throw new Error(`the result's ${key} is ${JSON.stringify(got)}, not ${pattern}`);
});

Then("the result's {string} is:", function (key, doc) {
  if (this.pubtest.refused) throw new Error(`it was refused: ${this.pubtest.refused.message}`);
  const got = dig(this.pubtest.result, key);
  const wanted = JSON.parse(doc);
  const canonical = (v) => JSON.stringify(v, (k, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort()) : x));
  if (canonical(got) !== canonical(wanted)) throw new Error(`the result's ${key} is ${JSON.stringify(got)}, not ${doc}`);
});
