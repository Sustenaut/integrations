// pubtest's record/replay harness, end to end, under cucumber-js: the steps of
// tests/features/harness-recording.feature and harness-replay.feature, the same
// text as tests/steps/test_harness.py has in Python. They live here, not in
// pubtest, because they need a real Caller and transports.js, and pubtest's own
// tests must not depend on integrations, whose suite runs on pubtest. Each
// scenario runs a small suite of its own, with its own pubrules.json, so this
// suite never turns cassettes on for itself.
import * as cucumber from "@cucumber/cucumber";
import { integrationsVersion } from "@sustenaut/pubtest/cassettes";
import { Result } from "@sustenaut/pubtest";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const modules = path.resolve(here, "..", "node_modules");
// `the program under test is "pubtest"` runs the pubtest this suite installed.
process.env.PUBTEST_BIN_DIR ??= path.join(modules, ".bin");

// The inner suite: its support code, its program, and its node_modules (this
// suite's, linked, where @sustenaut/integrations is this checkout).
const INNER_STEPS = `import * as cucumber from "@cucumber/cucumber";
import { Caller } from "@sustenaut/integrations/caller.js";
import { callerOptions } from "@sustenaut/pubtest/cassettes";
import { register } from "@sustenaut/pubtest";

process.env.PATH = \`\${new URL("../bin", import.meta.url).pathname}:\${process.env.PATH}\`;
const network = {
  fetch: async (url) => new Response(JSON.stringify({ name: String(url).split("/").pop(), updated_at: new Date().toISOString().replace(/\\.\\d+Z$/, "Z") }),
    { status: 200, headers: { "content-type": "application/json" } }),
};
const get = (world, url) => world.attempt(async () => (await new Caller("inner", "suite", callerOptions(world.pubtest)).call("github.repo-ops", "call", { method: "GET", url })).json());
register(cucumber, { network });
cucumber.When("the inner client gets {string}", function (url) { return get(this, url); });
`;
// The same suite with a test customer, inner.watched, whose cleanup, and the cleanup its
// step gives the scenario, each write to cleanups.txt what they see: the mode, whether
// INTEGRATIONS_CASSETTE is the one the scenario's step saw, and whether it failed. The
// customer's cleanup makes a call, as integrations' GitHub customer's does, and its step
// "the watched customer's cleanup fails" breaks it. After the scenarios, the file says
// what INTEGRATIONS_CASSETTE is.
const WATCHED = `import fs from "node:fs";
const log = (line) => fs.appendFileSync(new URL("../cleanups.txt", import.meta.url), \`\${line}\\n\`);
let seen = null;
let breaks = false;
const sees = (tape) => {
  const cassette = process.env.INTEGRATIONS_CASSETTE === undefined ? "no cassette" : process.env.INTEGRATIONS_CASSETTE === seen ? "the scenario's cassette" : "another cassette";
  return \`in \${process.env.INTEGRATIONS_MODE} mode, with \${cassette}, after it \${tape.failed ? "failed" : "passed"}\`;
};
const watched = {
  name: "inner.watched",
  setup: () => ({ "the repository": "watched-1" }),
  async cleanup(tape, names) {
    const answer = await new Caller("inner", "cleanup").call("github.repo-ops", "call", { method: "DELETE", url: \`https://api.github.com/repos/o/\${names["the repository"]}\` });
    log(\`the customer's cleanup of \${answer.json().name}, \${sees(tape)}\`);
    if (breaks) throw new Error("the watched cleanup broke");
  },
  steps: [
    ["Given", "the watched customer's repository {string}", async (ctx, logical) => {
      seen = process.env.INTEGRATIONS_CASSETTE;
      breaks = false;
      if (!(logical in await ctx.tape.namesOf("inner.watched"))) throw new Error(\`no \${logical}\`);
      ctx.cleanup(() => log(\`the scenario's cleanup, \${sees(ctx.tape)}\`));
    }],
    ["Given", "the watched customer's cleanup fails", () => { breaks = true; }],
  ],
};
cucumber.AfterAll(() => log(\`after the scenarios, INTEGRATIONS_CASSETTE is \${process.env.INTEGRATIONS_CASSETTE ?? "unset"}\`));
`;
// The same suite with a test customer, pubtest.example, that names one repository,
// made up while recording, as tests/harness/customers/pubtest_example_customer.py does.
const EXAMPLE = `const example = {
  name: "pubtest.example",
  setup: () => ({ "the repository": "pubtest-example-1" }),
  cleanup() {},
  steps: [
    ["Given", "the example customer's repository {string}", async (ctx, logical) => {
      const names = await ctx.tape.namesOf("pubtest.example");
      if (!(logical in names)) throw new Error(\`the example customer has no \${logical}: \${JSON.stringify(names)}\`);
    }],
  ],
};
cucumber.When("the inner client gets the example customer's repository {string}", async function (logical) {
  const real = (await this.pubtest.tape.namesOf("pubtest.example"))[logical];
  return get(this, \`https://api.github.com/repos/pubtest-example/\${real}\`);
});
`;
const INNER_CLIENT = `import { Caller } from "@sustenaut/integrations/caller.js";
const answer = await new Caller("inner", "program").call("github.repo-ops", "call", { method: "GET", url: process.argv[2] });
console.log(JSON.stringify(answer.json()));
`;
// What a harness sets for the programs it runs, which an inner suite must not inherit.
const HARNESS = ["PUBTEST_RUN", "INTEGRATIONS_MODE", "INTEGRATIONS_CASSETTE", "INTEGRATIONS_CASSETTES", "INTEGRATIONS_CASSETTE_REPORT", "PUBTEST_ONLY", "PUBTEST_PLAN", "PUBTEST_RECORD", "PUBTEST_CASSETTES"];

const withCustomers = (extra, customers) => INNER_STEPS.replace("register(cucumber, { network });", `${extra}register(cucumber, { network, customers: [${customers}] });`);

cucumber.Given("an inner suite with the feature {string}:", function (name, doc) { innerSuite(this.pubtest, name, doc, INNER_STEPS); });
cucumber.Given("an inner suite with a watched test customer and the feature {string}:", function (name, doc) {
  innerSuite(this.pubtest, name, doc, withCustomers(WATCHED, "watched"));
});
cucumber.Given("an inner suite with the example test customer and the feature {string}:", function (name, doc) {
  innerSuite(this.pubtest, name, doc, withCustomers(EXAMPLE, "example"));
});
cucumber.Given("an inner suite on the real network with the feature {string}:", function (name, doc) {
  innerSuite(this.pubtest, name, doc, INNER_STEPS.replace("register(cucumber, { network });", "register(cucumber);"));
});

function innerSuite(ctx, name, doc, steps) {
  const root = ctx.directory;
  const write = (p, text, mode) => {
    fs.mkdirSync(path.dirname(path.join(root, p)), { recursive: true });
    fs.writeFileSync(path.join(root, p), text);
    if (mode) fs.chmodSync(path.join(root, p), mode);
  };
  write(`features/${name}`, `${doc}\n`);
  write("package.json", JSON.stringify({ type: "module", devDependencies: { "@sustenaut/integrations": "github:Sustenaut/integrations#v0.16.0" } }));
  write("cucumber.json", JSON.stringify({ default: { paths: ["features/*.feature"], import: ["support/steps.mjs"] } }));
  write("support/steps.mjs", steps);
  write("bin/inner-client.mjs", INNER_CLIENT);
  write("bin/inner-client", `#!/bin/sh\nexec node "$(dirname "$0")/inner-client.mjs" "$@"\n`, 0o755);
  if (!fs.existsSync(path.join(root, "node_modules"))) fs.symlinkSync(modules, path.join(root, "node_modules"));
}

cucumber.When("the inner suite runs", function () {
  const env = { ...process.env };
  for (const key of HARNESS) delete env[key];
  const bin = path.join(modules, "@cucumber", "cucumber", "bin", "cucumber.js");
  this.pubtest.acting();
  const done = spawnSync(process.execPath, [bin], { cwd: this.pubtest.directory, env, encoding: "utf8", timeout: 300_000 });
  this.pubtest.ran(new Result({ code: done.status ?? 1, stdout: done.stdout, stderr: done.stderr, argv: ["cucumber-js"] }));
});

cucumber.Then("the inner run says {string}", function (text) {
  const said = this.pubtest.outcome.stdout + this.pubtest.outcome.stderr;
  if (!said.includes(text)) throw new Error(said);
});

// The integrations release the inner suite pins (its package.json), as pubtest stamps it.
cucumber.Then("the cassette {string} was recorded with this integrations", function (file) {
  const current = JSON.parse(fs.readFileSync(path.join(this.pubtest.directory, file), "utf8")).current;
  const wanted = integrationsVersion(this.pubtest.directory);
  if (current.recorded.integrations !== wanted) throw new Error(`recorded with ${current.recorded.integrations}, not ${wanted}`);
});
