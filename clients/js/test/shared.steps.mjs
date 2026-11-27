// The suite's own steps, beside pubtest's: what a module exports, the refusal's fields, a
// result against a repository file, and the call records a caller kept.
import { Then, When } from "@cucumber/cucumber";
import { dig, same } from "@sustenaut/pubtest";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { avroMetadata, module, repositoryJson } from "./helpers.mjs";

const show = (v) => JSON.stringify(v, null, 2);

When("I look at {string} from {string}", async function (name, file) {
  await this.attempt(async () => (await module(file))[name]);
});

When("I ask whether {string} from {string} is frozen", async function (name, file) {
  await this.attempt(async () => Object.isFrozen((await module(file))[name]));
});

When("I ask whether {string} from {string} extends {string}", async function (name, file, parent) {
  await this.attempt(async () => {
    const exports = await module(file);
    return exports[name].prototype instanceof exports[parent];
  });
});

When("I ask whether {string} from {string} extends {string} from {string}", async function (name, file, parent, other) {
  await this.attempt(async () => (await module(file))[name].prototype instanceof (await module(other))[parent]);
});

When("I make a {string} from {string} of:", async function (name, file, doc) {
  const made = (await module(file))[name];
  await this.attempt(() => new made(...JSON.parse(doc)));
});


// The result, or a part of it, is what a repository file holds at a dotted key.
Then("the result is the JSON at {string} in {string}", async function (key, file) {
  if (this.pubtest.refused) throw new Error(`it was refused: ${this.pubtest.refused.message}`);
  const want = dig(await repositoryJson(this, file), key);
  if (!same(this.pubtest.result, JSON.stringify(want))) throw new Error(`the result is ${show(this.pubtest.result)}, not ${file}'s ${key}: ${show(want)}`);
});

Then("the result is the JSON in {string}", async function (file) {
  if (this.pubtest.refused) throw new Error(`it was refused: ${this.pubtest.refused.message}`);
  const want = await repositoryJson(this, file);
  if (!same(this.pubtest.result, JSON.stringify(want))) throw new Error(`the result is ${show(this.pubtest.result)}, not ${file}'s`);
});

// The call records the scenario's caller kept (this.sink): a row per record, in order,
// the header row naming the fields, each cell compared as pubtest compares a value.
function kept(world) {
  if (!world.sink) throw new Error("the scenario's caller has no sink of its own");
  return world.sink.records;
}

Then("the calls recorded are:", function (table) {
  const records = kept(this);
  const [keys, ...rows] = table.raw();
  if (records.length !== rows.length) throw new Error(`${records.length} calls were recorded: ${show(records)}`);
  rows.forEach((row, i) => keys.forEach((k, j) => {
    if (!same(dig(records[i], k), row[j])) throw new Error(`record ${i}'s ${k} is ${show(dig(records[i], k))}, not ${row[j]}`);
  }));
});

Then("no call was recorded", function () {
  const records = kept(this);
  if (records.length) throw new Error(`recorded: ${show(records)}`);
});

Then("the calls recorded have the fields, in order:", function (doc) {
  const want = doc.split(/\s+/).filter(Boolean);
  for (const record of kept(this)) {
    if (JSON.stringify(Object.keys(record)) !== JSON.stringify(want)) throw new Error(`a record's fields are ${Object.keys(record)}`);
  }
});

Then("the calls recorded do not include {string}", function (text) {
  const written = JSON.stringify(kept(this));
  if (written.includes(text)) throw new Error(`the records include ${text}: ${written}`);
});

// A call log file's metadata, and the sender's report in it (integrations.status).
async function metadataOf(world, file) {
  return avroMetadata(new Uint8Array(await readFile(path.join(world.pubtest.directory, file))));
}

Then("the report in {string} is:", async function (file, doc) {
  const got = JSON.parse((await metadataOf(this, file))["integrations.status"]);
  if (!same(got, doc)) throw new Error(`${file}'s report is ${show(got)}`);
});

Then("the report in {string} has {string} equal to {string}", async function (file, key, wanted) {
  const got = dig(JSON.parse((await metadataOf(this, file))["integrations.status"]), key);
  if (!same(got, wanted)) throw new Error(`${file}'s report's ${key} is ${show(got)}, not ${wanted}`);
});

Then("the Avro metadata of {string} has {string} equal to {string}", async function (file, key, wanted) {
  const got = (await metadataOf(this, file))[key];
  if (!same(got, wanted)) throw new Error(`${file}'s ${key} is ${show(got)}, not ${wanted}`);
});
