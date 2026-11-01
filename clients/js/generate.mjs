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

const USE_CASES = [{ tag: "github.edit-pr", file: "github-edit-pr.js", calls: ["propose"] }];

const HEADER = (tag) =>
  `// Generated from openapi/integrations.json by clients/js/generate.mjs: do not edit.\n// The ${tag} client of the integrations service.`;

// Takes what the service's call takes, and baseUrl, token and fetch. Answers what
// the service answers, or throws an Error with the response's status.
const wrapper = (call) => `
export async function ${call}({ baseUrl, token, fetch, requestId, ...fields }) {
  if (!baseUrl) throw new TypeError("${call} needs a baseUrl");
  if (!token) throw new TypeError("${call} needs a token");
  const { data, error, response } = await generated.${call}({
    baseUrl,
    fetch,
    auth: token,
    headers: requestId ? { "X-Request-Id": requestId } : undefined,
    body: fields,
  });
  if (response.ok) return data;
  const detail = error?.detail;
  const failure = new Error(typeof detail === "string" ? detail : \`The service said \${response.status}\`);
  failure.status = response.status;
  throw failure;
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
    await writeFile(entry, `import * as generated from "./gen/sdk.gen";\n${calls.map(wrapper).join("\n")}`);
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
