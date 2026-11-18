// What the scenarios share: a fetch to pubtest's fake services, stand-ins that say on
// stdout what they were given (so pubtest's stdout steps read it), and reading
// repository files.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

/** A fetch that sends https://HOST/PATH to pubtest's fake service named HOST; a host with
 * no fake service does not answer. */
export function toFakes(world) {
  return async (url, init) => {
    const target = new URL(String(url));
    const fake = world.pubtest.fakes[target.hostname];
    if (!fake) throw new TypeError("fetch failed");
    return fetch(`${fake.url}${target.pathname}${target.search}`, init);
  };
}

/** What a fetch was given, as one line of JSON: bytes as a list of numbers, a form as its
 * fields (a file as its name and text), and the signal as live or aborted. */
export async function given(url, init = {}) {
  let body = init.body ?? null;
  if (body instanceof Uint8Array) body = [...body];
  else if (body instanceof FormData) {
    const fields = {};
    for (const [k, v] of body) fields[k] = v instanceof Blob ? { name: v.name, text: await v.text() } : v;
    body = fields;
  }
  const signal = init.signal instanceof AbortSignal ? (init.signal.aborted ? "aborted" : "live") : null;
  return JSON.stringify({ url: String(url), method: init.method ?? "GET", headers: init.headers ?? {}, body, signal });
}

/** A sleep that says each nap on stdout, in seconds, and doesn't wait. */
export const saidSleep = async (seconds) => {
  process.stdout.write(`slept ${seconds}\n`);
};

/** A repository file, read as JSON. */
export async function repositoryJson(world, file) {
  return JSON.parse(await readFile(path.join(world.pubtest.repository, file), "utf8"));
}

/** A module of clients/js (or of the repository, from ../../). */
export function module(file) {
  return import(new URL(`../${file}`, import.meta.url));
}

/** pubtest's value rule for a docstring or step value: JSON when it reads as JSON. */
export function value(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** An Avro container file's metadata: just enough of the format to read it back. */
export function avroMetadata(bytes) {
  let at = 4; // after "Obj\x01"
  const long = () => {
    let z = 0n;
    for (let shift = 0n; ; shift += 7n) {
      const b = bytes[at++];
      z |= BigInt(b & 0x7f) << shift;
      if (!(b & 0x80)) break;
    }
    return Number((z >> 1n) ^ -(z & 1n));
  };
  const string = () => {
    const n = long();
    return new TextDecoder().decode(bytes.slice(at, (at += n)));
  };
  const meta = {};
  for (let n = long(); n; n = long()) for (let i = 0; i < n; i++) meta[string()] = string();
  return meta;
}

/** A call record, as a sender sends it: request id r<n>, and these fields. */
export const record = (n, fields = {}) => ({
  at: "2026-11-06T09:00:00Z", repo: "storage", app: "worker", plugin: "github.repo-ops", operation: "check-push",
  request_id: `r${n}`, latency_ms: 40, status: 200, outcome: "ok", retries: 0, bytes_in: 10, bytes_out: 0,
  mode: "required", route: "routed", ...fields,
});

/** "r1,r2" as the records r1 and r2; "" as none. */
export const records = (ids, fields = {}) => ids.split(",").filter(Boolean).map((id) => record(Number(id.slice(1)), fields));

/** An R2 bucket that keeps what it is given as files in the scenario's scratch directory,
 * and refuses while it is down. */
export function bucket(world) {
  return {
    down: false,
    async put(key, data) {
      if (this.down) throw new Error("R2 is down");
      const file = path.join(world.pubtest.directory, key);
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(file, data);
    },
  };
}
