// One module per use case: a tag in the spec, and the call it publishes. Each is
// built from the spec alone and committed, so a browser can load it from jsDelivr
// at a release's tag, and CI fails when it differs from what the spec gives.
//
//   npm run generate
import { build } from "esbuild";
import { createClient } from "@hey-api/openapi-ts";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
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
