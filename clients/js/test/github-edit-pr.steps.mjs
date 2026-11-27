// The generated github.edit-pr client, against pubtest's fake service.
import { Given, Then, When } from "@cucumber/cucumber";
import { mock } from "node:test";
import { flushTimings, propose, readFiles, snapshot } from "../github-edit-pr.js";

// The fake service's address: pubtest sets NAME_URL for a fake service NAME.
const address = (name) => process.env[`${name.replace(/[^A-Za-z0-9]/g, "_").toUpperCase()}_URL`];

Given("no timings are waiting", async function () {
  await flushTimings();
});

Given("a proposal of:", function (doc) {
  this.fields = JSON.parse(doc);
});

Given("the time stands still", function () {
  mock.timers.enable({ apis: ["Date"], now: 1_800_000_000_000 });
});

Given("new ids are {string}", function (id) {
  mock.method(crypto, "randomUUID", () => id);
});

Given("the service's fetch answers a proposal, and fails for the timings", function () {
  this.service = "https://s.example";
  this.fetch = async (request) => {
    if (request.url.endsWith("/timings")) throw new TypeError("network down");
    return new Response(JSON.stringify({ status: "stale", path: "a" }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
});

When("I propose it to {string} as {string}", async function (name, token) {
  await this.attempt(() => propose({ baseUrl: address(name), token, ...this.fields }));
});

When("I propose it to {string} as {string} with the request id {string}", async function (name, token, requestId) {
  await this.attempt(() => propose({ baseUrl: address(name), token, requestId, ...this.fields }));
});

When("I propose it to the service as {string}", async function (token) {
  await this.attempt(() => propose({ baseUrl: this.service, token, fetch: this.fetch, ...this.fields }));
});

// The twentieth call's time fills the batch, which sends itself without being waited for.
When("I propose it to {string} as {string} {int} times", async function (name, token, times) {
  await this.attempt(async () => {
    for (let i = 0; i < times; i += 1) await propose({ baseUrl: address(name), token, ...this.fields });
    await new Promise((resolve) => setTimeout(resolve, 200));
  });
});

When("I propose it with only a token", async function () {
  await this.attempt(() => propose({ token: "t", ...this.fields }));
});

When("I propose it with only a service", async function () {
  await this.attempt(() => propose({ baseUrl: "https://s.example", ...this.fields }));
});

When("I take a snapshot of {string} from {string} as {string}", async function (repo, name, token) {
  await this.attempt(() => snapshot({ baseUrl: address(name), token, repo }));
});

When("I read the files {string} of {string} at {string} from {string} as {string}", async function (paths, repo, commit, name, token) {
  await this.attempt(() => readFiles({ baseUrl: address(name), token, repo, commit, paths: paths.split(",") }));
});

When("the timings are sent", async function () {
  await this.attempt(() => flushTimings());
});

