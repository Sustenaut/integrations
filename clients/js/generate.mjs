// One module per use case: a tag in the spec, and the call it publishes. Each is
// built from the spec alone and committed, so a browser can load it from jsDelivr
// at a release's tag, and CI fails when it differs from what the spec gives.
//
// And caller.js: src/caller.js after the tables calls.py reads, each class's timeout
// and tries (classes.json) and each operation's class and route (plugins/*.json), so
// both languages call the same way. CI fails when it differs from what they give.
//
//   npm run generate
import { build } from "esbuild";
import { createClient } from "@hey-api/openapi-ts";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Each call says where its fields go: a request's body, or its query.
const USE_CASES = [
  {
    tag: "github.edit-pr",
    file: "github-edit-pr.js",
    calls: [
      { name: "propose", fields: "body" },
      { name: "snapshot", fields: "query" },
      { name: "readFiles", fields: "body" },
    ],
  },
];

const HEADER = (tag) =>
  `// Generated from openapi/integrations.json by clients/js/generate.mjs: do not edit.\n// The ${tag} client of the integrations service.`;

// Takes what the service's call takes, and baseUrl, token and fetch. Answers what
// the service answers, or throws an Error with the response's status.
const wrapper = ({ name, fields }) => `
export async function ${name}({ baseUrl, token, fetch, requestId, ...fields }) {
  if (!baseUrl) throw new TypeError("${name} needs a baseUrl");
  if (!token) throw new TypeError("${name} needs a token");
  const id = requestId ?? crypto.randomUUID();
  const started = Date.now();
  const { data, error, response } = await generated.${name}({
    baseUrl,
    fetch,
    auth: token,
    headers: { "X-Request-Id": id },
    ${fields}: fields,
  });
  remember({ baseUrl, fetch, repo: fields.repo }, { request_id: id, total_ms: Date.now() - started });
  if (response.ok) return data;
  const detail = error?.detail;
  const failure = new Error(typeof detail === "string" ? detail : \`The service said \${response.status}\`);
  failure.status = response.status;
  throw failure;
}
`;

// How long each call took as the app saw it goes to the service in batches: when the
// page is hidden or left, or at BATCH. A timing that can't be sent is let go, as it
// mustn't get in the way of anything the person is doing.
const TIMINGS = `
const BATCH = 20;
const waiting = new Map();

function remember(target, timing) {
  const key = JSON.stringify([target.baseUrl, target.repo]);
  const entry = waiting.get(key) ?? { target, timings: [] };
  entry.timings.push(timing);
  waiting.set(key, entry);
  if (entry.timings.length >= BATCH) void flushTimings();
}

export async function flushTimings() {
  const entries = [...waiting.values()];
  waiting.clear();
  await Promise.all(entries.map(({ target: { baseUrl, fetch, repo }, timings }) =>
    generated.reportTimings({ baseUrl, fetch, keepalive: true, body: { repo, timings } }).catch(() => {})));
}

if (typeof addEventListener === "function") {
  addEventListener("pagehide", () => void flushTimings());
  addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") void flushTimings();
  });
}
`;

for (const { tag, file, calls } of USE_CASES) {
  const work = await mkdtemp(join(tmpdir(), "client-"));
  try {
    await createClient({
      input: "../../openapi/integrations.json",
      output: join(work, "gen"),
      parser: { filters: { tags: { include: [tag] } } },
      plugins: ["@hey-api/client-fetch", "@hey-api/sdk", "@hey-api/typescript"],
      logs: { level: "silent" },
    });
    const entry = join(work, "entry.ts");
    await writeFile(entry, `import * as generated from "./gen/sdk.gen";\n${TIMINGS}${calls.map(wrapper).join("\n")}`);
    await build({
      entryPoints: [entry],
      bundle: true,
      format: "esm",
      minify: true,
      legalComments: "none",
      banner: { js: HEADER(tag) },
      outfile: file,
      absWorkingDir: process.cwd(),
      logLevel: "warning",
    });
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

const REGISTRY = "../../src/integrations";
const classes = JSON.parse(await readFile(`${REGISTRY}/classes.json`, "utf8"));
const policies = Object.fromEntries(Object.entries(classes).map(([id, c]) => [id, { timeout: c.timeout, tries: c.tries }]));
const operations = {};
for (const file of (await readdir(`${REGISTRY}/plugins`)).filter((f) => f.endsWith(".json")).sort()) {
  const plugin = JSON.parse(await readFile(`${REGISTRY}/plugins/${file}`, "utf8"));
  operations[plugin.id] = Object.fromEntries(plugin.operations.map((o) => [o.name, { class: o.class, route: o.route }]));
}
// One entry a line, so a change to the registry is a one-line change here.
const table = (value) => `{\n${Object.entries(value).map(([k, v]) => `  ${JSON.stringify(k)}: ${JSON.stringify(v)},`).join("\n")}\n}`;
await writeFile("caller.js", [
  "// Generated from src/integrations/classes.json, src/integrations/plugins/*.json and",
  "// clients/js/src/caller.js by clients/js/generate.mjs: do not edit.",
  "",
  "// Each class's timeout in seconds and how often a call is tried.",
  `export const POLICIES = Object.freeze(${table(policies)});`,
  "",
  "// Each plugin's operations: its class, and how its calls reach the call log.",
  `export const OPERATIONS = Object.freeze(${table(operations)});`,
  "",
  await readFile("src/caller.js", "utf8"),
].join("\n"));
