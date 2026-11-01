// Against the Prism mock of the spec: node test/mock.mjs http://127.0.0.1:4010
import assert from "node:assert/strict";
import { propose, readFiles, snapshot } from "../github-edit-pr.js";

const baseUrl = process.argv[2];
const answer = await propose({ baseUrl, token: "t", repo: "o/r", branch: "b", title: "t", changes: { a: "b" } });
assert.ok(["opened", "stale", "nothing to change"].includes(answer.status));
await assert.rejects(propose({ baseUrl, token: "t", repo: "o/r" }), { status: 422 });
const taken = await snapshot({ baseUrl, token: "t", repo: "o/r" });
assert.equal(typeof taken.commit, "string");
assert.equal(typeof (await readFiles({ baseUrl, token: "t", repo: "o/r", commit: "c", paths: ["a"] })).files, "object");
console.log("the client and the mock agree");
