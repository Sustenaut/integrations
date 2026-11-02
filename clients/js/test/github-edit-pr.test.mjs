// The generated client, on a stand-in for fetch.
import { test } from "node:test";
import assert from "node:assert/strict";
import { flushTimings, propose, readFiles, snapshot } from "../github-edit-pr.js";

const answering = (status, value, seen = []) => async (request) => {
  seen.push(request);
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
};
const fields = { repo: "o/r", branch: "b", title: "t", changes: { "a.txt": "new\n" } };

test("it posts to the use case's address with the person's token", async () => {
  const seen = [];
  const answer = { status: "opened", url: "u", number: 1, branch: "b", commit: "c" };
  const result = await propose({ baseUrl: "https://service.example", token: "person", fetch: answering(200, answer, seen), requestId: "r1", ...fields });
  assert.deepEqual(result, answer);
  assert.equal(seen[0].url, "https://service.example/v1/github/edit-pr/proposals");
  assert.equal(seen[0].method, "POST");
  assert.equal(seen[0].headers.get("authorization"), "Bearer person");
  assert.equal(seen[0].headers.get("x-request-id"), "r1");
  const sent = await seen[0].json();
  assert.deepEqual(sent, fields);
});

test("a stale answer is an answer, not an error", async () => {
  const result = await propose({ baseUrl: "https://s.example", token: "t", fetch: answering(200, { status: "stale", path: "a" }), ...fields });
  assert.deepEqual(result, { status: "stale", path: "a" });
});

test("a refusal throws the service's words and status", async () => {
  await assert.rejects(
    propose({ baseUrl: "https://s.example", token: "t", fetch: answering(403, { detail: "not served here" }), ...fields }),
    { message: "not served here", status: 403 },
  );
});

test("a validation error has no single message, so it says the status", async () => {
  await assert.rejects(
    propose({ baseUrl: "https://s.example", token: "t", fetch: answering(422, { detail: [{ msg: "x" }] }), ...fields }),
    { message: "The service said 422", status: 422 },
  );
});

test("it needs a service and a token", async () => {
  await assert.rejects(propose({ token: "t", ...fields }), /needs a baseUrl/);
  await assert.rejects(propose({ baseUrl: "https://s.example", ...fields }), /needs a token/);
});

test("a snapshot sends its fields in the query", async () => {
  const seen = [];
  const answer = { branch: "main", commit: "c", can_push: true, paths: ["a"], truncated: false };
  const result = await snapshot({ baseUrl: "https://s.example", token: "t", fetch: answering(200, answer, seen), repo: "o/r" });
  assert.deepEqual(result, answer);
  assert.equal(seen[0].method, "GET");
  assert.equal(seen[0].url, "https://s.example/v1/github/edit-pr/snapshot?repo=o%2Fr");
  assert.equal(seen[0].headers.get("authorization"), "Bearer t");
});

test("files are read in one call, in the body", async () => {
  const seen = [];
  const result = await readFiles({ baseUrl: "https://s.example", token: "t", fetch: answering(200, { files: { a: "text", b: null } }, seen), repo: "o/r", commit: "c", paths: ["a", "b"] });
  assert.deepEqual(result, { files: { a: "text", b: null } });
  assert.deepEqual(await seen[0].json(), { repo: "o/r", commit: "c", paths: ["a", "b"] });
});

test("a call without an id gets one, and its time goes to the service in a batch", async () => {
  await flushTimings();
  const seen = [];
  const fetch = answering(200, { status: "stale", path: "a" }, seen);
  await propose({ baseUrl: "https://s.example", token: "t", fetch, ...fields });
  await propose({ baseUrl: "https://s.example", token: "t", fetch, requestId: "mine", ...fields });
  assert.equal(seen.length, 2);
  const made = seen[0].headers.get("x-request-id");
  assert.match(made, /^[0-9a-f-]{36}$/);
  await flushTimings();
  assert.equal(seen.length, 3);
  assert.equal(seen[2].url, "https://s.example/v1/github/edit-pr/timings");
  assert.equal(seen[2].headers.get("authorization"), null);
  const sent = await seen[2].json();
  assert.equal(sent.repo, "o/r");
  assert.deepEqual(sent.timings.map((t) => t.request_id), [made, "mine"]);
  assert.ok(sent.timings.every((t) => Number.isInteger(t.total_ms) && t.total_ms >= 0));
});

test("a failed call's time is kept too, and a batch that can't be sent is let go", async () => {
  await flushTimings();
  await assert.rejects(propose({ baseUrl: "https://s.example", token: "t", fetch: answering(403, { detail: "no" }), ...fields }));
  await flushTimings();
  await propose({ baseUrl: "https://s.example", token: "t", fetch: async (request) => {
    if (request.url.endsWith("/timings")) throw new TypeError("network down");
    return new Response(JSON.stringify({ status: "stale", path: "a" }), { status: 200, headers: { "Content-Type": "application/json" } });
  }, ...fields });
  await flushTimings();
});

test("a full batch sends itself", async () => {
  await flushTimings();
  const seen = [];
  const fetch = answering(200, { status: "stale", path: "a" }, seen);
  for (let i = 0; i < 20; i += 1) await propose({ baseUrl: "https://s.example", token: "t", fetch, ...fields });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.ok(seen.some((r) => r.url.endsWith("/timings")));
});
