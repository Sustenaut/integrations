// The generated client, on a stand-in for fetch.
import { test } from "node:test";
import assert from "node:assert/strict";
import { propose } from "../github-edit-pr.js";

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
