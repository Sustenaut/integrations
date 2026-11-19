// The vectors in tests/vectors/, which tests/steps/test_vectors.py runs on calls.py too.
import { Given, When } from "@cucumber/cucumber";
import { readdir } from "node:fs/promises";
import path from "node:path";
import { Caller, MemorySink, Replay, Unavailable } from "../caller.js";
import { repositoryJson } from "./helpers.mjs";

const refusal = (rule) => rule && ((answer) => answer.json()[rule.key] === rule.equals);

Given("the call in {string}", async function (file) {
  this.vector = await repositoryJson(this, file);
});

// What the call came to, in the vector's "expect" shape: its status, the error it raised,
// how many requests went, the sleeps, and the record's fields the vector names.
When("caller.js makes it", async function () {
  const vector = this.vector;
  await this.attempt(async () => {
    const sink = new MemorySink();
    const naps = [];
    const replay = new Replay(vector.responses);
    const caller = new Caller("a-repo", "web", { sink, fetch: replay.fetch, modes: vector.modes, sleep: async (s) => naps.push(s), newId: () => "id-1", random: () => vector.random ?? 1 });
    let raises = null;
    let status;
    try {
      status = (await caller.call(vector.plugin, vector.operation, vector.request, { requestId: vector.request_id, refused: refusal(vector.refused) })).status;
    } catch (err) {
      if (!(err instanceof Unavailable)) throw err;
      raises = "Unavailable";
      status = sink.records[0].status;
    }
    const [record] = sink.records;
    return {
      status, raises, sent: replay.sent.length, sleeps: naps,
      record: Object.fromEntries(Object.keys(vector.expect.record).map((k) => [k, record[k]])),
    };
  });
});

When("I list the vectors in {string}", async function (dir) {
  await this.attempt(async () => (await readdir(path.join(this.pubtest.repository, dir)))
    .filter((n) => n.endsWith(".json") && n !== "sink.json").sort());
});
