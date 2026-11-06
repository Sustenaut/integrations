// The vectors in tests/vectors/, which tests/steps/test_vectors.py runs on calls.py too.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { Caller, MemorySink, Replay, Unavailable } from "../caller.js";

const VECTORS = new URL("../../../tests/vectors/", import.meta.url);
const names = (await readdir(VECTORS)).filter((n) => n.endsWith(".json") && n !== "sink.json").sort();

const refusal = (rule) => rule && ((answer) => answer.json()[rule.key] === rule.equals);

for (const name of names) {
  const vector = JSON.parse(await readFile(new URL(name, VECTORS), "utf8"));
  test(`${name}: ${vector.description}`, async () => {
    const sink = new MemorySink();
    const naps = [];
    const replay = new Replay(vector.responses);
    const caller = new Caller("a-repo", "web", { sink, fetch: replay.fetch, modes: vector.modes, sleep: async (s) => naps.push(s), newId: () => "id-1", random: () => vector.random ?? 1 });
    let raised = null;
    let status;
    try {
      status = (await caller.call(vector.plugin, vector.operation, vector.request, { requestId: vector.request_id, refused: refusal(vector.refused) })).status;
    } catch (err) {
      if (!(err instanceof Unavailable)) throw err;
      raised = "Unavailable";
      status = sink.records[0].status;
    }
    const { expect } = vector;
    assert.deepEqual([raised, status, replay.sent.length, naps], [expect.raises, expect.status, expect.sent, expect.sleeps]);
    const [record] = sink.records;
    assert.deepEqual(Object.fromEntries(Object.keys(expect.record).map((k) => [k, record[k]])), expect.record);
  });
}

test("there are vectors to run", () => assert.ok(names.length >= 10));
