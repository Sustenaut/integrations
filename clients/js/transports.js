// Generated from src/integrations/plugins/*.json and clients/js/src/transports.js by
// clients/js/generate.mjs: do not edit.

// Each record block in the registry, by file: a plugin's, or a shared _name.json's that
// plugins extend. record(pluginId) merges them as registry.record does.
export const RECORD = Object.freeze({
  "_github": {"keep_headers":["Content-Type","ETag","Location","Link","Retry-After","X-RateLimit-Remaining"],"volatile":{"headers":["Date","ETag","Last-Modified","X-GitHub-Request-Id","X-RateLimit-Reset"],"json":["$..created_at","$..updated_at","$..pushed_at","$..closed_at","$..merged_at","$..expires_at","$..node_id","$..id"],"values":{"sha":"\\b[0-9a-f]{40}\\b"}},"secret":{"headers":["Authorization"],"json":["$.token","$..private_key","$..client_secret"],"query":["access_token"]},"match":{"url":"scheme+host+path+sorted-query","body":"json","graphql":"query-name+variables"},"customers":"github.test"},
  "github.app-token": {"extends":"_github"},
  "github.board-read": {"extends":"_github"},
  "github.edit-pr": {"extends":"_github"},
  "github.repo-ops": {"extends":"_github"},
});

// Record and replay: the transports a test runs its calls on, and the one a Caller gets
// when it is given no fetch. src/integrations/transports.py, for Node 22 and Cloudflare
// Workers, with no dependencies; the vectors in tests/vectors/cassettes/ run on both.
//
// A transport is an object with a fetch(url, init) that answers a Response, as
// globalThis.fetch does. A Caller tells it the call's plugin, operation and timeout in
// init.integrations.
//
// - new Cassette(pathOrData) answers from a recording (integrations.cassette/1, or the
//   older { responses } format) and throws NoSnapshot for a call it has none of.
// - new Recorder(inner, path) sends through inner, keeps every call, and save() writes
//   them as a cassette, normalised by each plugin's record rules (RECORD above, from
//   plugins/*.json): only the headers worth keeping, volatile values as placeholders,
//   secrets never.
// - new Faulty(inner, rules) makes calls fail on purpose.
// - currentTransport() is the one use() installed, else the one INTEGRATIONS_MODE picks,
//   under the faults INTEGRATIONS_FAULTS names; cassetteReports() says which recorded
//   calls the cassettes it made answered, for a harness to check at exit.
// - useClock() sets the clock, sleep and jitter a Caller given none of them uses.
//
// Placeholders: a volatile value is written ⟦integrations:KIND:N⟧, the same one wherever
// the same value is; on replay it is answered as a stand-in of its kind made from its
// name, and a stand-in sent back reads as its placeholder again. Real text holding
// "⟦integrations:" is written ⟦integrations:escape⟧, and read back as it was. A
// placeholder first seen in a request is a value the code made itself: on replay it
// matches the first value of its kind the code sends there, and from then on is that
// value, in the answers and in what the code must send again. A value already another
// placeholder's matches none.
//
// Names: a recording against a test org names its repositories for real where a
// scenario says o/r. The Recorder's names, and INTEGRATIONS_NAMES (a JSON object from
// each real name to the scenario's), are written as the scenario's names wherever they
// appear as a whole word, so the cassette, and a replay, only ever see o/r. A name may be
// a label, ⟦integrations:commit:start⟧ for a commit's real sha: names are applied before
// the volatile patterns, so that sha is written as its label (kept in the cassette's map,
// kind commit), and a label is replayed as itself, kept as it is where the map knows it,
// in a URL percent-encoded or not.
//
// Where cassettes go: a cassette is written (save(), promote(), writeCassette()), and a
// cassette the environment names is read, only at a path that resolves, symlinks
// followed, inside the cassettes directory; anything else throws CassettePathError
// before a byte is read or written. The cassettes directory is INTEGRATIONS_CASSETTES
// when it is set, else the directory holding INTEGRATIONS_CASSETTE when that is set,
// else the directory of the path given (to the Recorder when it is made, or to promote()
// and writeCassette()), each resolved.
//
//   import { Cassette, use } from "@sustenaut/integrations/transports.js";
//   const restore = use(new Cassette(data));
//   try { ... } finally { restore(); }

import { pythonJson } from "./caller.js";

export const FORMAT = "integrations.cassette/1";
// A request body over this many bytes is kept as its digest, and matched by it.
export const DIGEST_OVER = 64 * 1024;
export const MODE = "INTEGRATIONS_MODE";
export const CASSETTE = "INTEGRATIONS_CASSETTE";
export const CASSETTES = "INTEGRATIONS_CASSETTES";
export const MARKER = "PUBTEST_RUN";
export const NAMES = "INTEGRATIONS_NAMES";
export const FAULTS = "INTEGRATIONS_FAULTS";
const MODES = ["live", "replay", "record"];

const PREFIX = "⟦integrations:";
const ESCAPE = "⟦integrations:escape⟧";
const SECRET = "⟦integrations:secret⟧";
// A placeholder's name is a number, ? (any value of its kind), or a label a scenario gave a
// value (⟦integrations:commit:start⟧), which is replayed as itself.
const LABEL = "[A-Za-z][A-Za-z0-9_-]*";
const PLACEHOLDER_SOURCE = `⟦integrations:(?:escape|secret|([a-z][a-z0-9-]*):(\\d+|\\?|${LABEL}))⟧`;
const placeholders = () => new RegExp(PLACEHOLDER_SOURCE, "g");
const isPlaceholder = (text) => new RegExp(`^${PLACEHOLDER_SOURCE}$`).exec(text);
// A known value is replaced where it is a whole value or a path segment from this long,
// and inside other text from MIN_INSIDE, so a short one can't hit by chance.
const MIN_WHOLE = 4;
const MIN_INSIDE = 8;
// A secret shorter than this is not looked for in what would be written.
const MIN_SECRET = 4;

const URL_PARTS = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#.*)?$/;
const whole = (source) => new RegExp(`^(?:${source})$`);
const SHAPES = [
  ["date", whole("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?")],
  ["http-date", whole("[A-Z][a-z]{2}, \\d{2} [A-Z][a-z]{2} \\d{4} \\d{2}:\\d{2}:\\d{2} GMT")],
  ["sha", whole("[0-9a-f]{40}")],
  ["token", whole("gh[opsur]_[A-Za-z0-9_]+")],
  ["digits", whole("[0-9]+")],
];
const GRAPHQL_NAME = /^\s*(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)/;
const STAND_IN_EPOCH = Date.UTC(2000, 0, 1);

// A call the cassette has no recording of. Its message says which, the nearest recorded
// call and how they differ, and how to record it.
export class NoSnapshot extends Error {
  constructor(message) {
    super(message);
    this.name = new.target.name;
  }
}

// Recorded calls the code never made: the code changed, or the cassette is stale.
export class UnusedCalls extends NoSnapshot {}

// A secret the recorder saw would be written: the recording is not saved.
export class SecretInRecording extends Error {
  constructor(message) {
    super(message);
    this.name = new.target.name;
  }
}

// A cassette path that resolves outside the cassettes directory: nothing is read or
// written there.
export class CassettePathError extends Error {
  constructor(message) {
    super(message);
    this.name = new.target.name;
  }
}

// INTEGRATIONS_MODE asks for something this process may not do.
export class ModeError extends Error {
  constructor(message) {
    super(message);
    this.name = new.target.name;
  }
}

// --- record rules -------------------------------------------------------------------

const SECRET_HEADERS = ["Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie"];
const MATCH = { url: "scheme+host+path+sorted-query", body: "json", graphql: null };

// extra over base, shallow per field: lists joined, objects merged, the rest replaced.
function merge(base, extra) {
  const merged = { ...base };
  for (const [key, value] of Object.entries(extra)) {
    if (key === "extends") continue;
    const old = merged[key];
    if (Array.isArray(old) && Array.isArray(value)) merged[key] = [...old, ...value.filter((v) => !old.includes(v))];
    else if (isObject(old) && isObject(value)) merged[key] = merge(old, value);
    else merged[key] = value;
  }
  return merged;
}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function recordBlock(name, seen = []) {
  if (seen.includes(name)) throw new Error(`record rules extend themselves: ${[...seen, name].join(" -> ")}`);
  if (!(name in RECORD)) throw new Error(`no plugin or shared record rules ${name}`);
  const block = RECORD[name] ?? {};
  return block.extends ? merge(recordBlock(block.extends, [...seen, name]), block) : block;
}

// The record rules of pluginId, its extends merged, every field there, as registry.record
// gives them; a plugin with no record block, or none, gets the defaults.
export function record(pluginId) {
  let rules = {
    keep_headers: [], volatile: { headers: [], json: [], values: {} },
    secret: { headers: [...SECRET_HEADERS], json: [], query: [] }, match: { ...MATCH }, customers: null,
  };
  if (pluginId && !pluginId.startsWith("_") && RECORD[pluginId]) rules = merge(rules, recordBlock(pluginId));
  return rules;
}

export const registry = Object.freeze({ record });

// --- values and placeholders --------------------------------------------------------

// The kind of a volatile value, from its shape; null for one never replaced.
export function kindOf(value, otherwise = "text") {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "number") return Number.isInteger(value) ? "id" : "number";
  if (typeof value !== "string" || !value) return null;
  for (const [kind, shape] of SHAPES) if (shape.test(value)) return kind;
  return otherwise;
}

// The value a placeholder is replayed as: made from its name alone, of its kind's shape,
// and different for every name. A labelled one is itself.
export function standIn(kind, n) {
  if (typeof n !== "number") return placeholder(kind, n);
  const seed = sha256Hex(utf8(`integrations:${kind}:${n}`));
  const number = 7_000_000_000 + n * 1000 + (parseInt(seed.slice(0, 6), 16) % 1000);
  const at = new Date(STAND_IN_EPOCH + (n * 86400 + (parseInt(seed.slice(0, 8), 16) % 86400)) * 1000);
  if (kind === "sha") return seed.slice(0, 40);
  if (kind === "token" || kind === "secret") return `ghs_${seed.slice(0, 36)}`;
  if (kind === "date") return at.toISOString().replace(/\.\d{3}Z$/, "Z");
  if (kind === "http-date") return at.toUTCString();
  if (kind === "id") return number;
  if (kind === "number") return number + 0.5;
  if (kind === "digits") return String(number);
  return `${kind}-${seed.slice(0, 12)}`;
}

// A value as it reads inside text: a URL, a header, a string.
const textOf = (value) => (typeof value === "string" ? value : JSON.stringify(value));
const placeholder = (kind, n) => `⟦integrations:${kind}:${n}⟧`;
// Whether name is a labelled placeholder, ⟦integrations:KIND:LABEL⟧.
export function isLabel(name) {
  const m = isPlaceholder(name);
  return Boolean(m && m[2] && m[2] !== "?" && !/^\d+$/.test(m[2]));
}

// The placeholder a scenario's label for a value is: ⟦integrations:commit:start⟧. Throws
// for a label that is not a letter, then letters, digits, _ and -.
export function label(kind, name) {
  if (!new RegExp(`^${LABEL}$`).test(name ?? "") || !/^[a-z][a-z0-9-]*$/.test(kind)) throw new Error(`"${name}" is no label: a letter, then letters, digits, _ and -`);
  return placeholder(kind, name);
}

// Real text holding the prefix, escaped; a label the map knows is kept as it is.
function escapeText(text, labels = new Set()) {
  if (!text.includes(PREFIX)) return text;
  if (!labels.size) return text.split(PREFIX).join(ESCAPE);
  const keep = new RegExp([...labels].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|"), "g");
  let out = "";
  let at = 0;
  for (const m of text.matchAll(keep)) {
    out += text.slice(at, m.index).split(PREFIX).join(ESCAPE) + m[0];
    at = m.index + m[0].length;
  }
  return out + text.slice(at).split(PREFIX).join(ESCAPE);
}
const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// The text split into placeholders (true) and what lies between them.
function pieces(text) {
  const out = [];
  let at = 0;
  for (const m of text.matchAll(placeholders())) {
    if (m.index > at) out.push([false, text.slice(at, m.index)]);
    out.push([true, m[0]]);
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push([false, text.slice(at)]);
  return out;
}

// pattern replaced by fn(match), but never inside a placeholder.
function subOutside(text, pattern, fn) {
  if (!pattern) return text;
  return pieces(text).map(([held, p]) => (held ? p : p.replace(pattern, (m) => fn(m)))).join("");
}

// INTEGRATIONS_NAMES: a JSON object from each real name a recording meets to the name the
// scenario gives it; {} when it is not set. Throws when it is not such an object.
export function namesFrom(vars = env()) {
  const raw = vars[NAMES];
  if (!raw) return {};
  let names = null;
  try {
    names = JSON.parse(raw);
  } catch {
    // reported below
  }
  if (!isObject(names) || !Object.entries(names).every(([k, v]) => k && typeof v === "string")) {
    throw new Error(`${NAMES} is a JSON object from each real name to the scenario's, not ${JSON.stringify(raw)}`);
  }
  return names;
}

// Each real name in names, where it is a whole word (not inside a longer name), as the
// scenario's; the longest first.
function renamer(names) {
  const real = Object.keys(names).filter((k) => k !== names[k]).sort((a, b) => b.length - a.length || cmp(a, b));
  if (!real.length) return null;
  const pattern = new RegExp(`(?<![A-Za-z0-9_.-])(?:${real.map(escapeRegExp).join("|")})(?![A-Za-z0-9_-])`, "g");
  return (text) => subOutside(text, pattern, (v) => names[v]);
}

class ValueMap {
  known = new Map(); // a value's text -> its placeholder
  rename = null;
  labels = new Set(); // the labelled placeholders, which text keeps as they are
  #inside;

  knownText(text) {
    return text.length >= MIN_WHOLE ? this.known.get(text) ?? null : null;
  }

  inside() {
    if (this.#inside === undefined) {
      const long = [...this.known.keys()].filter((k) => k.length >= MIN_INSIDE)
        .sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0));
      this.#inside = long.length ? new RegExp(long.map(escapeRegExp).join("|"), "g") : null;
    }
    return this.#inside;
  }

  learnt() {
    this.#inside = undefined;
  }
}

class Writer extends ValueMap {
  byValue = new Map();
  entries = {};
  counts = {};
  secrets = new Map(); // a secret value -> where it was seen

  constructor(names = {}) {
    super();
    this.rename = renamer(names);
    this.labels = new Set(Object.values(names).filter(isLabel));
  }

  // Each label in text is in the cassette's map, first seen at where.
  labelled(text, where) {
    for (const m of text.matchAll(placeholders())) {
      if (this.labels.has(m[0]) && !(m[0] in this.entries)) this.entries[m[0]] = { kind: m[1], first: where };
    }
  }

  volatile(value, kind, where) {
    const key = JSON.stringify(value);
    if (!this.byValue.has(key)) {
      this.counts[kind] = (this.counts[kind] ?? 0) + 1;
      const name = placeholder(kind, this.counts[kind]);
      this.byValue.set(key, name);
      this.entries[name] = { kind, first: where };
      if (kind !== "secret" && !this.known.has(textOf(value))) this.known.set(textOf(value), name);
      this.learnt();
    }
    return this.byValue.get(key);
  }

  secret(value, side, where) {
    this.sawSecret(value, where);
    if (side === "request" || value === null) return SECRET;
    return this.volatile(["secret", value], "secret", where);
  }

  sawSecret(value, where) {
    if (value !== null && typeof value === "object") {
      for (const v of Object.values(value)) this.sawSecret(v, where);
      return;
    }
    if (value === null || value === undefined || typeof value === "boolean") return;
    const text = textOf(value);
    if (text.length >= MIN_SECRET && !this.secrets.has(text)) this.secrets.set(text, where);
  }
}

class Reader extends ValueMap {
  values = new Map(); // a placeholder -> its stand-in, or the value it was bound to
  raw = false; // true: a volatile value no placeholder stands for is left as it is

  constructor(volatile) {
    super();
    for (const name of Object.keys(volatile ?? {})) {
      const m = isPlaceholder(name);
      if (!m || !m[1] || m[2] === "?") continue;
      if (isLabel(name)) this.labels.add(name);
      const value = standIn(m[1], /^\d+$/.test(m[2]) ? Number(m[2]) : m[2]);
      this.values.set(name, value);
      this.known.set(textOf(value), name);
    }
  }

  volatile(value, kind) {
    return this.known.get(textOf(value)) ?? (this.raw ? value : placeholder(kind, "?"));
  }

  // The same map, leaving a volatile value it does not know as it is.
  rawCopy() {
    const other = new Reader({});
    Object.assign(other, { values: this.values, known: this.known, labels: this.labels, raw: true });
    return other;
  }

  // name is value from now on: its stand-in no longer stands for it.
  bind(name, value) {
    const old = this.values.get(name);
    if (old !== undefined && this.known.get(textOf(old)) === name) this.known.delete(textOf(old));
    this.values.set(name, value);
    this.known.set(textOf(value), name);
    this.learnt();
  }

  secret() {
    return SECRET;
  }

  // Placeholders in recorded text as their stand-ins, the escape as it was.
  fillText(text) {
    return text.replace(placeholders(), (m) => (m === ESCAPE ? PREFIX : textOf(this.standIn(m))));
  }

  standIn(name) {
    if (this.values.has(name)) return this.values.get(name);
    const m = isPlaceholder(name);
    const n = m?.[2] && m[2] !== "?" ? m[2] : "0";
    return standIn(m?.[1] ?? "secret", /^\d+$/.test(n) ? Number(n) : n);
  }

  fill(value) {
    if (Array.isArray(value)) return value.map((v) => this.fill(v));
    if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, this.fill(v)]));
    if (typeof value === "string") return isPlaceholder(value) && value !== ESCAPE ? this.standIn(value) : this.fillText(value);
    return value;
  }
}

// --- JSONPath-lite --------------------------------------------------------------------

// $.a.b, $..key, $.a[*].b, $.a.* as steps: ["child", name], ["any", name] for a name at
// any depth, ["index", "*"].
export function parsePath(text) {
  const rest = text.startsWith("$") ? text.slice(1) : text;
  return [...rest.matchAll(/\.\.([A-Za-z0-9_*-]+)|\.([A-Za-z0-9_*-]+)|\[\*\]/g)]
    .map((m) => (m[1] ? ["any", m[1]] : m[2] ? ["child", m[2]] : ["index", "*"]));
}

export function pathMatches(steps, path) {
  if (!steps.length) return !path.length;
  const [how, name] = steps[0];
  if (how === "index") return path.length > 0 && typeof path[0] === "number" && pathMatches(steps.slice(1), path.slice(1));
  if (how === "child") return path.length > 0 && (name === "*" || path[0] === name) && pathMatches(steps.slice(1), path.slice(1));
  return path.some((p, i) => (name === "*" || p === name) && pathMatches(steps.slice(1), path.slice(i + 1)));
}

const dotted = (path) => path.map(String).join(".");

// --- normalising ------------------------------------------------------------------------

const utf8 = (text) => new TextEncoder().encode(text);
const strictUtf8 = new TextDecoder("utf-8", { fatal: true });

class Normaliser {
  constructor(rules, values) {
    this.rules = rules;
    this.map = values;
    this.volatilePaths = rules.volatile.json.map(parsePath);
    this.secretPaths = rules.secret.json.map(parsePath);
    this.patterns = Object.entries(rules.volatile.values).map(([kind, rx]) => [kind, new RegExp(rx, "g")]);
    this.keep = new Map(rules.keep_headers.map((h) => [h.toLowerCase(), h]));
    this.volatileHeaders = new Set(rules.volatile.headers.map((h) => h.toLowerCase()));
    this.secretHeaders = new Set(rules.secret.headers.map((h) => h.toLowerCase()));
    this.secretQuery = new Set(rules.secret.query);
  }

  text(text, where) {
    text = escapeText(text, this.map.labels);
    if (this.map.rename) { // before the volatile patterns: a labelled sha is its label
      text = this.map.rename(text);
      if (this.map instanceof Writer && this.map.labels.size) this.map.labelled(text, where);
    }
    const found = this.map.knownText(text);
    if (found) return found;
    text = subOutside(text, this.map.inside(), (v) => this.map.known.get(v));
    for (const [kind, pattern] of this.patterns) text = subOutside(text, pattern, (v) => this.map.volatile(v, kind, where));
    return text;
  }

  url(url, where) {
    for (const token of this.map.labels) { // a label sent back in a URL, percent-encoded or not
      for (const encoded of [encodeURI(token), encodeURIComponent(token)]) url = url.split(encoded).join(token);
    }
    const m = URL_PARTS.exec(url);
    if (!m) return this.text(url, where);
    const scheme = m[1].toLowerCase();
    const host = m[2].toLowerCase();
    const path = (m[3] || "/").split("/").map((segment) => (segment ? this.text(segment, where) : "")).join("/");
    const side = where.split(".")[2] ?? "request";
    const pairs = [];
    for (const pair of (m[4] ?? "").split("&")) {
      if (!pair) continue;
      const at = pair.indexOf("=");
      const [name, eq, value] = at < 0 ? [pair, "", ""] : [pair.slice(0, at), "=", pair.slice(at + 1)];
      pairs.push([name, eq, this.secretQuery.has(name) ? this.map.secret(value, side, where) : this.text(value, where)]);
    }
    pairs.sort((a, b) => cmp(a[0], b[0]) || cmp(a[2], b[2]));
    return `${scheme}://${host}${path}` + (pairs.length ? `?${pairs.map((p) => p.join("")).join("&")}` : "");
  }

  headers(headers, side, where) {
    const lower = new Map(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
    // By the rules' names, in their order, in both languages.
    for (const name of this.rules.secret.headers) if (lower.has(name.toLowerCase())) this.secretHeader(lower.get(name.toLowerCase()), `${where}.${name}`);
    const kept = {};
    for (const out of this.rules.keep_headers) {
      const low = out.toLowerCase();
      if (this.secretHeaders.has(low) || !lower.has(low) || out in kept) continue;
      const value = lower.get(low);
      kept[out] = this.volatileHeaders.has(low)
        ? this.map.volatile(value, kindOf(value, "header") ?? "header", `${where}.${out}`)
        : this.text(value, `${where}.${out}`);
    }
    return kept;
  }

  secretHeader(value, where) {
    if (!(this.map instanceof Writer)) return;
    this.map.sawSecret(value, where);
    const rest = value.trim().split(/\s+/);
    if (rest.length > 1) this.map.sawSecret(value.trim().slice(rest[0].length).trim(), where);
  }

  json(value, side, where, path) {
    if (path.length && value !== null && this.secretPaths.some((s) => pathMatches(s, path))) {
      return this.map.secret(value, side, `${where}.${dotted(path)}`);
    }
    if (Array.isArray(value)) return value.map((v, i) => this.json(v, side, where, [...path, i]));
    if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, this.json(v, side, where, [...path, k])]));
    if (path.length && this.volatilePaths.some((s) => pathMatches(s, path))) {
      const kind = kindOf(value);
      if (kind) return this.map.volatile(value, kind, `${where}.${dotted(path)}`);
    }
    if (typeof value === "string") return this.text(value, `${where}.${dotted(path)}`);
    if (typeof value === "number") return this.map.knownText(JSON.stringify(value)) ?? value;
    return value;
  }

  body(raw, side, where) {
    if (!raw?.length) return null;
    let text;
    try {
      text = strictUtf8.decode(raw);
    } catch {
      return { base64: toBase64(raw) };
    }
    if ((this.rules.match.body ?? "json") === "json" && ["{", "["].includes(text.trimStart()[0])) {
      try {
        return { json: this.json(JSON.parse(text), side, `${where}.body`, []) };
      } catch (err) {
        if (!(err instanceof SyntaxError)) throw err;
      }
    }
    return { text: this.text(text, `${where}.body`) };
  }

  request(request, where) {
    let body = this.body(request.body, "request", where);
    if (request.body && request.body.length > DIGEST_OVER) body = digest(body);
    return { method: request.method.toUpperCase(), url: this.url(request.url, `${where}.url`), headers: this.headers(request.headers, "request", `${where}.headers`), body };
  }

  response(response, where) {
    if (response.status === null) return { status: null, headers: {}, body: null, error: response.error || "no answer" };
    return { status: response.status, headers: this.headers(response.headers, "response", `${where}.headers`), body: this.body(response.body, "response", where) };
  }
}

const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// JSON with sorted keys and no spaces: what a body's digest and its match read.
export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value ?? null);
}

// A normalised request body as its SHA-256 and length.
export function digest(body) {
  if (body === null || "sha256" in body) return body;
  const data = "json" in body ? utf8(canonical(body.json)) : "text" in body ? utf8(body.text) : fromBase64(body.base64);
  return { sha256: sha256Hex(data), length: data.length };
}

// A GraphQL body as its operation's name and variables, when the plugin matches so.
function graphql(body, rules) {
  if (rules.match.graphql !== "query-name+variables" || !body || !("json" in body)) return body;
  const query = isObject(body.json) ? body.json.query : undefined;
  if (typeof query !== "string") return body;
  let name = body.json.operationName;
  if (!name) name = GRAPHQL_NAME.exec(query)?.[1] ?? query.split(/\s+/).filter(Boolean).join(" ");
  return { graphql: { operation: name, variables: body.json.variables ?? null } };
}

// --- requests and answers as fetch has them ---------------------------------------------------

// What a fetch was asked, as a transport reads it: method, url, headers (an object),
// body (bytes or null), and the call's plugin, operation and timeout from a Caller.
export async function readRequest(url, init = {}) {
  const given = typeof Request !== "undefined" && url instanceof Request ? url : null;
  const headers = {};
  new Headers(init.headers ?? given?.headers ?? {}).forEach((v, k) => {
    headers[k] = v;
  });
  let body = init.body ?? null;
  if (body === null && given && given.body) body = new Uint8Array(await given.arrayBuffer());
  if (typeof body === "string") body = utf8(body);
  else if (body instanceof ArrayBuffer) body = new Uint8Array(body);
  else if (ArrayBuffer.isView(body)) body = new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  else if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) body = utf8(body.toString());
  else if ((typeof FormData !== "undefined" && body instanceof FormData) || (typeof Blob !== "undefined" && body instanceof Blob)) {
    // Encoded as fetch would send it, with the Content-Type (a form's boundary) it would add.
    const encoded = new Request("https://body.invalid/", { method: "POST", body });
    body = new Uint8Array(await encoded.arrayBuffer());
    if (!("content-type" in headers) && encoded.headers.get("content-type")) headers["content-type"] = encoded.headers.get("content-type");
  } else if (body !== null) throw new TypeError("a transport here reads a text, bytes, form or blob body only");
  const { plugin = null, operation = null, timeout = Infinity } = init.integrations ?? {};
  return { method: (init.method ?? given?.method ?? "GET").toUpperCase(), url: given ? given.url : String(url), headers, body, plugin, operation, timeout };
}

const NULL_BODY = new Set([101, 204, 205, 304]);

// A string body would get a Content-Type of its own, which the recording didn't have.
function answer(status, body, headers) {
  return new Response(NULL_BODY.has(status) ? null : typeof body === "string" ? utf8(body) : body, { status, headers });
}

async function readAnswer(response) {
  const headers = {};
  response.headers.forEach((v, k) => {
    headers[k] = v;
  });
  return { status: response.status, headers, body: new Uint8Array(await response.arrayBuffer()), error: null };
}

const asTransport = (t) => (typeof t === "function" ? { fetch: t } : t);
const noAnswer = (error) => new TypeError(String(error ?? "no answer").replace(/^no answer: /, ""));

// --- the cassette ---------------------------------------------------------------

function oldAnswer(found) {
  if ("error" in found) return { status: null, headers: {}, body: null, error: found.error };
  return { status: found.status, headers: { ...(found.headers ?? {}) }, body: "text" in found ? { text: found.text } : { json: found.json ?? null } };
}

function nodeModule(name) {
  const found = globalThis.process?.getBuiltinModule?.(name);
  if (!found) throw new Error("a cassette file needs Node 22.3 or later to be read or written: give its data instead");
  return found;
}

const nodeFs = () => nodeModule("node:fs");
const nodePath = () => nodeModule("node:path");

// p made absolute, without folding its ".." into what comes before, which a symlink may
// change: resolved() follows each part as the file system would.
function absolute(p) {
  const path = nodePath();
  const text = String(p).replace(/[\\/]+$/, "") || path.sep;
  return path.isAbsolute(text) ? text : `${globalThis.process.cwd()}${path.sep}${text}`;
}

// p with every symlink followed and every "." and ".." taken, as Python's Path.resolve()
// gives it: the parts that exist as the file system resolves them, the rest as written.
function resolved(p, hops = 0) {
  const fs = nodeFs();
  const path = nodePath();
  const start = absolute(p);
  let at = path.parse(start).root;
  for (const part of start.slice(at.length).split(/[\\/]+/)) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      at = path.dirname(at);
      continue;
    }
    const next = path.join(at, part);
    let link = null;
    try {
      if (fs.lstatSync(next).isSymbolicLink()) link = fs.readlinkSync(next);
    } catch (err) {
      if (err?.code !== "ENOENT" && err?.code !== "ENOTDIR") throw err;
    }
    if (link === null) {
      at = next;
      continue;
    }
    if (hops >= 40) throw new CassettePathError(`${p}: too many symlinks`);
    at = resolved(path.isAbsolute(link) ? link : `${at}${path.sep}${link}`, hops + 1);
  }
  return at;
}

// The directory cassettes are written in, and an environment-named one read from,
// resolved: INTEGRATIONS_CASSETTES when it is set, else the directory holding
// INTEGRATIONS_CASSETTE, else the directory of given; null with none of them. A
// cassette's own name is not resolved here, so one that is a symlink out of its
// directory still lands outside it.
export function cassettesDir(given = null) {
  const vars = env();
  if (vars[CASSETTES]) return resolved(vars[CASSETTES]);
  const named = vars[CASSETTE] || given;
  if (!named) return null;
  const whole = absolute(named);
  return resolved(whole.slice(0, whole.lastIndexOf(nodePath().sep)) || nodePath().sep);
}

function refused(p, target, root) {
  return new CassettePathError(`the cassette ${p} is at ${target}, outside the cassettes directory ${root}: cassettes go in ${CASSETTES}, else beside ${CASSETTE}`);
}

const within = (target, root) => target.startsWith(root.endsWith(nodePath().sep) ? root : `${root}${nodePath().sep}`);

// p resolved, symlinks followed, when that is inside root (by default cassettesDir(p));
// else CassettePathError.
export function cassettePath(p, root = null) {
  const base = root === null ? cassettesDir(p) : resolved(root);
  const target = resolved(p);
  if (base === null || !within(target, base)) throw refused(p, target, base);
  return target;
}

function load(source) {
  if (typeof source !== "string") return [source, source.scenario || source.description || "a cassette"];
  const data = JSON.parse(nodeFs().readFileSync(source, "utf8"));
  return [data, data.scenario || source.split("/").pop().replace(/\.[^.]*$/, "")];
}

const env = () => globalThis.process?.env ?? {};

// A transport that answers from a recording: path (or the data itself), in
// integrations.cassette/1 or the older { responses } format, the shared vectors among
// them. A request finds its recorded call by plugin, operation, method, URL (scheme,
// host, path, the query sorted) and body, each normalised as the plugin's record rules
// say, and is answered with the first such call not answered yet; an old-format list
// answers in turn, its last one for good. With none left, it throws NoSnapshot. mode is
// the INTEGRATIONS_MODE it runs under: in record mode close() only warns.
export class Cassette {
  sent = [];

  constructor(path, { plugins = registry, mode } = {}) {
    const [data, scenario] = load(path);
    Object.assign(this, { scenario, plugins, mode: mode ?? env()[MODE] ?? "live" });
    let volatile = {};
    if (data.responses) this.calls = Cassette.#old(data.responses);
    else if (data.format === FORMAT) {
      const current = data.current ?? {};
      volatile = current.volatile ?? {};
      this.calls = (current.calls ?? []).map((c) => ({ plugin: c.plugin ?? null, operation: c.operation ?? null, request: c.request, response: c.response, sticky: false, used: 0 }));
    } else throw new Error(`${scenario} is not a cassette: no format ${FORMAT} and no responses`);
    this.reader = new Reader(volatile);
    this.loose = new Set(Object.entries(volatile).filter(([n, e]) => (e.first ?? "").split(".")[2] === "request" && !isLabel(n)).map(([n]) => n));
  }

  static #old(responses) {
    const calls = [];
    for (const [key, found] of Object.entries(responses)) {
      const at = key.indexOf(" ");
      const [method, url] = at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
      const answers = Array.isArray(found) ? found : [found];
      answers.forEach((a, i) => calls.push({ plugin: null, operation: null, request: { method, url, body: "*" }, response: oldAnswer(a), sticky: i === answers.length - 1, used: 0 }));
    }
    return calls;
  }

  // A placeholder first seen in a request, and not yet bound, is a value the code made
  // itself, which matches any value of its kind.
  #loosen(text) {
    return text.replace(placeholders(), (m, kind) => (this.loose.has(m) ? placeholder(kind, "?") : m));
  }

  #bodyKey(body, rules) {
    if (body !== null && body !== undefined && !("sha256" in body) && this.loose.size) body = JSON.parse(this.#loosen(JSON.stringify(body)));
    return canonical(graphql(body ?? null, rules));
  }

  #recordedKey(call, rules) {
    const { request } = call;
    if (call.plugin === null) return [request.method.toUpperCase(), new Normaliser(rules, new Reader({})).url(request.url, "url"), "*"];
    return [request.method.toUpperCase(), this.#loosen(request.url), this.#bodyKey(request.body ?? null, rules)];
  }

  #liveKey(request, rules) {
    const asked = new Normaliser(rules, this.reader).request(request, "calls.?.request");
    asked.url = this.#loosen(asked.url);
    return [asked.method, asked.url, this.#bodyKey(asked.body, rules), asked];
  }

  fetch = async (url, init = {}) => {
    const request = await readRequest(url, init);
    this.sent.push(request);
    const rules = this.plugins.record(request.plugin);
    const [method, normalUrl, body, asked] = this.#liveKey(request, rules);
    let found = null;
    for (const call of this.calls) {
      if (call.plugin !== null && request.plugin && (call.plugin !== request.plugin || call.operation !== request.operation)) continue;
      const want = this.#recordedKey(call, rules);
      if (want[0] !== method || want[1] !== normalUrl || (want[2] !== "*" && want[2] !== body)) continue;
      if (!call.used) {
        const bound = this.#bindings(call, request, rules);
        if (bound === null) continue;
        for (const [name, value] of bound) {
          this.reader.bind(name, value);
          this.loose.delete(name);
        }
        found = call;
        break;
      }
      if (call.sticky && found === null) found = call;
    }
    if (found === null) throw new NoSnapshot(this.#noSnapshot(request, asked, body, rules));
    found.used += 1;
    return this.#answer(found.response);
  };

  // The values request sends where call's recorded request holds a loose placeholder:
  // each one binds it. Empty when there is none; null when they do not fit the map: one
  // placeholder sent as two values, or a value that is already another placeholder's.
  #bindings(call, request, rules) {
    const found = new Map();
    if (call.plugin === null || !this.loose.size) return found;
    const recorded = call.request;
    const body = recorded.body ?? null;
    if (body !== null && "sha256" in body) return found;
    const texts = [recorded.url, canonical(graphql(body, rules))];
    if (!texts.some((t) => [...t.matchAll(placeholders())].some((m) => this.loose.has(m[0])))) return found;
    const raw = new Normaliser(rules, this.reader.rawCopy()).request(request, "calls.?.request");
    const sent = [raw.url, canonical(graphql(raw.body, rules))];
    for (const [i, text] of texts.entries()) {
      const [pattern, groups] = bindingPattern(text, this.loose, i === 1);
      const m = pattern.exec(sent[i]);
      if (m === null) return new Map();
      for (const [group, [name, how]] of Object.entries(groups)) {
        const value = boundValue(m.groups[group], how);
        if (typeof value === "string" && isPlaceholder(value)) {
          if (value !== name) return null;
          continue;
        }
        if (found.has(name) && textOf(found.get(name)) !== textOf(value)) return null;
        found.set(name, value);
      }
    }
    const seen = new Set();
    for (const [name, value] of found) {
      const known = this.reader.known.get(textOf(value));
      if ((known !== undefined && known !== name) || seen.has(textOf(value))) return null;
      seen.add(textOf(value));
    }
    return found;
  }

  // What was replayed: how many calls the cassette holds, which of them (by index)
  // answered, how many requests were sent and how many were answered.
  report() {
    return { calls: this.calls.length, used: this.calls.flatMap((c, i) => (c.used ? [i] : [])), sent: this.sent.length, answered: this.calls.reduce((n, c) => n + c.used, 0) };
  }

  #answer(recorded) {
    if (recorded.status === null || recorded.status === undefined) throw noAnswer(recorded.error);
    const headers = Object.fromEntries(Object.entries(recorded.headers ?? {}).map(([k, v]) => [k, this.reader.fillText(v)]));
    const { body } = recorded;
    let data = "";
    if (body && "json" in body) data = pythonJson(this.reader.fill(body.json));
    else if (body && "text" in body) data = this.reader.fillText(body.text);
    else if (body && "base64" in body) data = fromBase64(body.base64);
    return answer(recorded.status, data, headers);
  }

  #noSnapshot(request, asked, body, rules) {
    const lines = [`no recorded call in ${this.scenario} for ${describe(request.plugin, request.operation, asked.method, asked.url)}`];
    let best = null;
    let bestScore = -1;
    this.calls.forEach((call, i) => {
      const want = this.#recordedKey(call, rules);
      const same = [null, request.plugin].includes(call.plugin) && [null, request.operation].includes(call.operation);
      let score = 4 * same + 2 * (want[0] === asked.method) + 3 * (want[1] === asked.url) + (want[2] === "*" || want[2] === body);
      score = 2 * score + !call.used;
      if (score > bestScore) [best, bestScore] = [[i, call, want], score];
    });
    if (best === null) lines.push("nearest: none, the cassette has no calls");
    else {
      const [i, call, want] = best;
      const used = call.used ? `, already answered ${call.used} time${call.used !== 1 ? "s" : ""}` : "";
      lines.push(`nearest: call ${i}, ${describe(call.plugin, call.operation, want[0], want[1])}${used}`);
      lines.push(...differences(want, [asked.method, asked.url, body]));
    }
    lines.push(`record it: pubtest record "${this.scenario}"`);
    return lines.join("\n");
  }

  // The recorded calls never made, as "plugin operation METHOD url".
  unused() {
    return this.calls.filter((c) => !c.used).map((c) => describe(c.plugin, c.operation, c.request.method.toUpperCase(), c.request.url));
  }

  // Throws UnusedCalls when a recorded call was never made; in record mode it only warns,
  // as the recording being made replaces this one.
  close() {
    const left = this.unused();
    if (!left.length) return;
    const message = [`${left.length} recorded call${left.length !== 1 ? "s" : ""} in ${this.scenario} never made:`, ...left.map((c) => `  ${c}`), `record it: pubtest record "${this.scenario}"`].join("\n");
    if (this.mode === "record") {
      console.warn(message);
      return;
    }
    throw new UnusedCalls(message);
  }
}

// text as a pattern a request's text matches, each loose placeholder in it a group: in
// JSON, a whole value (a string or a number) or a piece of a string.
function bindingPattern(text, loose, jsonText) {
  const out = [];
  const groups = {};
  const parts = pieces(text);
  for (let i = 0; i < parts.length; i++) {
    const [held, piece] = parts[i];
    if (!held || !loose.has(piece)) {
      out.push(escapeRegExp(piece));
      continue;
    }
    const group = `g${Object.keys(groups).length}`;
    const after = parts[i + 1]?.[1] ?? "";
    if (jsonText && out.join("").endsWith('"') && after.startsWith('"')) {
      out[out.length - 1] = out.at(-1).slice(0, -1); // the quote goes into the group: a string, or the number it stood for
      parts[i + 1] = [parts[i + 1][0], after.slice(1)];
      out.push(String.raw`(?<${group}>"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)`);
      groups[group] = [piece, "json"];
    } else if (jsonText) {
      out.push(String.raw`(?<${group}>(?:[^"\\]|\\.)+?)`);
      groups[group] = [piece, "inside"];
    } else {
      out.push(`(?<${group}>.+?)`);
      groups[group] = [piece, "text"];
    }
  }
  return [new RegExp(`^(?:${out.join("")})$`, "s"), groups];
}

function boundValue(text, how) {
  if (how === "json") return JSON.parse(text);
  if (how === "inside") return JSON.parse(`"${text}"`);
  return text;
}

const describe = (plugin, operation, method, url) => `${plugin || "*"} ${operation || "*"} ${method} ${url}`;
const short = (text, limit = 60) => (text.length <= limit ? text : `${text.slice(0, limit - 1)}…`);

function jsonDifferences(want, got, path, out) {
  if (out.length >= 3) return;
  if (isObject(want) && isObject(got)) {
    for (const key of [...new Set([...Object.keys(want), ...Object.keys(got)])].sort()) {
      if (!(key in got)) out.push(`${path}.${key}: recorded ${short(canonical(want[key]))}, not sent`);
      else if (!(key in want)) out.push(`${path}.${key}: sent ${short(canonical(got[key]))}, not recorded`);
      else jsonDifferences(want[key], got[key], `${path}.${key}`, out);
      if (out.length >= 3) return;
    }
  } else if (Array.isArray(want) && Array.isArray(got) && want.length === got.length) {
    want.forEach((w, i) => jsonDifferences(w, got[i], `${path}[${i}]`, out));
  } else if (canonical(want) !== canonical(got)) {
    out.push(`${path}: recorded ${short(canonical(want))}, sent ${short(canonical(got))}`);
  }
}

const sized = (body) => (isObject(body) && "sha256" in body ? `${body.length} bytes, sha256 ${body.sha256.slice(0, 12)}` : short(canonical(body)));

function differences(want, got) {
  const out = [];
  if (want[0] !== got[0]) out.push(`method: recorded ${want[0]}, sent ${got[0]}`);
  if (want[1] !== got[1]) out.push(`url: recorded ${want[1]}, sent ${got[1]}`);
  if (want[2] !== "*" && want[2] !== got[2]) {
    const w = JSON.parse(want[2]);
    const g = JSON.parse(got[2]);
    if ((isObject(w) && "sha256" in w) || (isObject(g) && "sha256" in g)) return [...out, `body: recorded ${sized(w)}, sent ${sized(g)}`];
    const wk = isObject(w) ? Object.keys(w) : [];
    if (wk.length === 1 && isObject(g) && Object.keys(g).length === 1 && Object.keys(g)[0] === wk[0]) {
      const [kind] = wk;
      if (kind === "json" || kind === "graphql") {
        const found = [];
        jsonDifferences(w[kind], g[kind], "$", found);
        return [...out, ...found.map((line) => `body ${line}`)];
      }
      out.push(`body: recorded ${short(canonical(w[kind]))}, sent ${short(canonical(g[kind]))}`);
    } else out.push(`body: recorded ${short(canonical(w))}, sent ${short(canonical(g))}`);
  }
  return out;
}

// --- the recorder ---------------------------------------------------------------

const timestamp = (date) => date.toISOString().slice(0, 19) + "Z";
const monotonic = globalThis.performance?.now ? () => performance.now() : () => Date.now();

// A transport that sends through inner and keeps every call, in order, with how long it
// took; save() writes them to path as a cassette. What is written follows each plugin's
// record rules: the headers it keeps (of requests and answers), text, JSON and binary
// bodies (base64), a request body over 64 KiB as its digest, volatile values as
// placeholders. A secret is never written: secret headers are dropped, secret JSON paths
// and query parameters replaced, and save() throws SecretInRecording when a secret it saw
// would still be written anywhere. names (from each real name to the scenario's), over
// INTEGRATIONS_NAMES as it is when the recording is written, are written as the
// scenario's names. clock() is in milliseconds. save() writes only inside
// the cassettes directory (cassettesDir()), settled when the recorder is made:
// INTEGRATIONS_CASSETTES, else the directory of INTEGRATIONS_CASSETTE, else that of
// path; a path resolving outside it throws CassettePathError.
export class Recorder {
  calls = [];

  constructor(inner, path = null, { plugins = registry, scenario = null, consumer = null, clock = monotonic, now = () => new Date(), names = {} } = {}) {
    Object.assign(this, { inner: asTransport(inner), path, plugins, scenario, consumer, clock, now, names: { ...names } });
    this.root = cassettesDir(path);
  }

  fetch = async (url, init = {}) => {
    const request = await readRequest(url, init);
    const started = this.clock();
    let response;
    try {
      response = await this.inner.fetch(url, init);
    } catch (err) {
      if (err instanceof NoSnapshot) throw err;
      this.calls.push([request, { status: null, headers: {}, body: new Uint8Array(), error: `no answer: ${err?.message ?? err}` }, Math.floor(this.clock() - started + 0.5)]);
      throw err;
    }
    const got = await readAnswer(response);
    this.calls.push([request, got, Math.floor(this.clock() - started + 0.5)]);
    return answer(got.status, got.body, got.headers);
  };

  // The calls kept so far, normalised: a cassette's current. Throws SecretInRecording
  // when a secret would be in it.
  recording() {
    const values = new Writer({ ...namesFrom(), ...this.names });
    let calls = [];
    for (let pass = 0; pass < 2; pass++) { // the second time, every value the first met is known everywhere
      calls = this.calls.map(([request, response, elapsed], i) => {
        const normaliser = new Normaliser(this.plugins.record(request.plugin), values);
        return {
          kind: "http", plugin: request.plugin || null, operation: request.operation || null,
          request: normaliser.request(request, `calls.${i}.request`), response: normaliser.response(response, `calls.${i}.response`), elapsed_ms: elapsed,
        };
      });
    }
    const current = { recorded: { at: timestamp(this.now()), integrations: "js", consumer: this.consumer }, volatile: values.entries, calls };
    noSecrets(current, values.secrets);
    return current;
  }

  // Writes the recording to path (Node). Into an existing cassette it goes as the
  // candidate, current, and the file's previous current becomes its baseline unless it
  // has one: a baseline is the last accepted recording, which a re-record never
  // overwrites and only promote() moves.
  save(path = this.path) {
    if (!path) throw new Error("a recording needs a path to be saved to");
    const fs = nodeFs();
    const root = this.root ?? cassettesDir(path);
    const target = cassettePath(path, root);
    const current = this.recording();
    const old = fs.existsSync(target) ? JSON.parse(fs.readFileSync(target, "utf8")) : {};
    let baseline = null;
    if (old.format === FORMAT) baseline = old.baseline ?? old.current ?? null;
    const scenario = this.scenario || old.scenario || String(path).split(/[\\/]/).pop().replace(/\.[^.]*$/, "");
    writeCassette(target, { format: FORMAT, scenario, current, baseline }, root);
    return path;
  }
}

function noSecrets(current, secrets) {
  current.calls.forEach((call, i) => {
    for (const side of ["request", "response"]) {
      const written = JSON.stringify(call[side]);
      for (const [secret, seen] of secrets) {
        if (written.includes(secret) || written.includes(JSON.stringify(secret).slice(1, -1))) {
          throw new SecretInRecording(`a secret seen at ${seen} would be written in calls.${i}.${side}: the recording is not saved`);
        }
      }
    }
  });
}

// Writes data as a cassette at path, resolved, symlinks followed, and only when that is
// inside root (by default cassettesDir(path)): else CassettePathError, and nothing is
// written. Returns where it wrote.
export function writeCassette(path, data, root = null) {
  const fs = nodeFs();
  const base = root === null ? cassettesDir(path) : resolved(root);
  const target = resolved(path);
  if (base === null || !within(target, base)) throw refused(path, target, base);
  fs.mkdirSync(nodePath().dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(data, null, 1) + "\n");
  return target;
}

// Accepts the cassette's current recording: it becomes the baseline. The cassette must
// resolve inside cassettesDir(path), else CassettePathError.
export function promote(path) {
  const fs = nodeFs();
  const root = cassettesDir(path);
  const target = cassettePath(path, root);
  const data = JSON.parse(fs.readFileSync(target, "utf8"));
  if (data.format !== FORMAT) throw new Error(`${path} is not an ${FORMAT} cassette`);
  data.baseline = data.current;
  writeCassette(target, data, root);
}

// --- faults ---------------------------------------------------------------

const glob = (pattern) => new RegExp(`^${pattern.split("*").map(escapeRegExp).join(".*")}$`);
const isUpper = (w) => w === w.toUpperCase() && w !== w.toLowerCase();
const EFFECTS = new Set(["no-answer", "status", "slow", "malformed", "then-recorded"]);

// One rule of Faulty: which calls (match), which of them (nth), what happens (effect).
export class Fault {
  seen = 0;

  constructor(rule) {
    this.rule = rule;
    const words = (rule.match ?? "*").split(/\s+/).filter(Boolean);
    this.plugin = words[0] ?? "*";
    let at = 1;
    this.operation = null;
    if (words.length > at && !isUpper(words[at])) this.operation = words[at++];
    this.method = words[at] ?? null;
    this.path = words.length > at + 1 ? glob(words[at + 1]) : null;
    this.nth = String(rule.nth ?? "*");
    const effect = String(rule.effect);
    const space = effect.indexOf(" ");
    [this.effect, this.argument] = space < 0 ? [effect, ""] : [effect.slice(0, space), effect.slice(space + 1)];
    if (!EFFECTS.has(this.effect)) throw new Error(`no fault "${rule.effect}": no-answer, status N, slow S, malformed or then-recorded`);
  }

  matches(request) {
    const plugin = request.plugin ?? "";
    if (this.plugin !== "*" && plugin !== this.plugin && !plugin.startsWith(`${this.plugin}.`)) return false;
    if (this.operation && this.operation !== request.operation) return false;
    if (this.method && this.method !== request.method.toUpperCase()) return false;
    const m = URL_PARTS.exec(request.url);
    return !this.path || this.path.test(m ? m[3] || "/" : request.url);
  }

  applies() {
    if (this.nth === "*") return true;
    if (this.nth === "first") return this.seen === 1;
    if (this.nth.startsWith("after ")) return this.seen > Number(this.nth.slice(6));
    return this.seen === Number(this.nth);
  }
}

// A transport that makes calls through inner fail on purpose, by rules, each an object:
// - match: "PLUGIN[ OPERATION][ METHOD PATH]", PATH a pattern in which * is anything
//   ("github.edit-pr open-pr POST /repos/*/pulls"); "*" is any plugin, and a PLUGIN is
//   also every plugin under it, before a dot (github is every github.* plugin);
// - nth: which of the calls it matches: "*" (every one, the default), "first", N (the
//   Nth, from 1) or "after N";
// - effect: "no-answer"; "status N", with the rule's body (text, or JSON) and headers;
//   "slow S", which advances advance(S), a test's clock, and never sleeps, then answers,
//   or gives no answer when S is past the call's timeout; "malformed", the answer with
//   half its body and then text no parser reads; or "then-recorded", which passes it through.
// The first rule that matches a call and whose nth it is decides; a call no rule decides
// passes through. Only no-answer and status keep the call from inner.
export class Faulty {
  slept = 0;

  constructor(inner, rules, { advance = () => {} } = {}) {
    Object.assign(this, { inner: asTransport(inner), rules: rules.map((r) => new Fault(r)), advance });
  }

  fetch = async (url, init = {}) => {
    const request = await readRequest(url, init);
    let chosen = null;
    for (const fault of this.rules) {
      if (!fault.matches(request)) continue;
      fault.seen += 1;
      if (chosen === null && fault.applies()) chosen = fault;
    }
    if (chosen === null || chosen.effect === "then-recorded") return this.inner.fetch(url, init);
    if (chosen.effect === "no-answer") throw new TypeError("fault injected");
    if (chosen.effect === "status") {
      const { body = null, headers = {} } = chosen.rule;
      return answer(Number(chosen.argument), body === null ? "" : typeof body === "string" ? body : pythonJson(body), headers);
    }
    if (chosen.effect === "slow") {
      const seconds = Number(chosen.argument);
      if (seconds >= request.timeout) {
        this.advance(request.timeout);
        this.slept += request.timeout;
        throw new TypeError(`timed out after ${request.timeout} s`);
      }
      this.advance(seconds);
      this.slept += seconds;
      return this.inner.fetch(url, { ...init, integrations: { ...(init.integrations ?? {}), timeout: request.timeout - seconds } });
    }
    const got = await readAnswer(await this.inner.fetch(url, init)); // malformed
    const half = got.body.slice(0, Math.floor(got.body.length / 2));
    const tail = utf8("⟦malformed");
    const body = new Uint8Array(half.length + tail.length);
    body.set(half);
    body.set(tail, half.length);
    return answer(got.status, body, got.headers);
  };
}

// --- the current transport --------------------------------------------------------

const installed = [];
const byMode = new Map();
const named = new Map(); // each cassette's INTEGRATIONS_CASSETTE, as it was given
const faulty = new Map();
const clocks = [];
export const LIVE = Object.freeze({ fetch: (...a) => globalThis.fetch(...a) });

// INTEGRATIONS_MODE, checked: live (the default), replay or record. A mode other than
// live needs PUBTEST_RUN, which only a test harness sets, so a process misconfigured into
// answering from cassettes refuses to start; and INTEGRATIONS_CASSETTE, the cassette's file.
export function checkMode(vars = env()) {
  const mode = vars[MODE] || "live";
  if (!MODES.includes(mode)) throw new ModeError(`${MODE} is '${mode}': it is live, replay or record`);
  if (mode !== "live" && !vars[MARKER]) throw new ModeError(`${MODE}=${mode} without ${MARKER}: only a test harness answers from or records cassettes, so this process does not start`);
  if (mode !== "live" && !vars[CASSETTE]) throw new ModeError(`${MODE}=${mode} needs ${CASSETTE}, the cassette's file`);
  return mode;
}

// INTEGRATIONS_FAULTS: a JSON list of Faulty's rules, which a harness sets so the programs
// it runs fail as it does; [] when it is not set. Throws ModeError when it is set without
// PUBTEST_RUN, and an Error when it is no list of rules.
export function faultRules(vars = env()) {
  const raw = vars[FAULTS];
  if (!raw) return [];
  if (!vars[MARKER]) throw new ModeError(`${FAULTS} without ${MARKER}: only a test harness makes calls fail on purpose, so this process does not start`);
  let rules = null;
  try {
    rules = JSON.parse(raw);
  } catch {
    // reported below
  }
  if (!Array.isArray(rules) || !rules.every((r) => isObject(r) && "effect" in r)) throw new Error(`${FAULTS} is a JSON list of Faulty's rules, each with an effect, not ${JSON.stringify(raw)}`);
  for (const rule of rules) new Fault(rule); // an effect that is not one is refused here, not at the call
  return rules;
}

// The transport use() installed, else the one INTEGRATIONS_MODE picks: globalThis.fetch
// (live), new Cassette(INTEGRATIONS_CASSETTE) (replay) or new Recorder(LIVE,
// INTEGRATIONS_CASSETTE) (record), which saves when the process exits; under Faulty with
// INTEGRATIONS_FAULTS's rules when it names some (faultRules()), which count this
// process's calls. Throws CassettePathError when INTEGRATIONS_CASSETTE resolves outside
// the cassettes directory (INTEGRATIONS_CASSETTES, if set).
export function currentTransport() {
  const mode = checkMode();
  if (installed.length) return installed.at(-1);
  const path = mode === "live" ? "" : cassettePath(env()[CASSETTE]);
  const rules = faultRules();
  const key = `${mode} ${path}`;
  if (!byMode.has(key)) {
    if (mode === "live") byMode.set(key, LIVE);
    else if (mode === "replay") {
      byMode.set(key, new Cassette(path, { mode }));
      named.set(key, env()[CASSETTE]);
    } else {
      const recorder = new Recorder(LIVE, path);
      globalThis.process?.on?.("exit", () => {
        if (!recorder.calls.length) return;
        try {
          recorder.save();
        } catch (err) {
          console.error(`integrations: the recording was not saved: ${err?.message ?? err}`);
        }
      });
      byMode.set(key, recorder);
    }
  }
  if (!rules.length) return byMode.get(key);
  const faulted = `${key} ${env()[FAULTS]}`;
  if (!faulty.has(faulted)) faulty.set(faulted, new Faulty(byMode.get(key), rules));
  return faulty.get(faulted);
}

// For a harness to check at exit what a process it ran replayed: one entry per cassette
// currentTransport() answered from in this process, its INTEGRATIONS_CASSETTE as the
// process was given it (cassette) and its report().
export function cassetteReports() {
  return [...byMode.entries()].filter(([key, t]) => t instanceof Cassette && named.has(key))
    .map(([key, t]) => ({ cassette: named.get(key), ...t.report() }));
}

const nap = (seconds) => new Promise((resolve) => setTimeout(resolve, seconds * 1000));
const monotonicMs = globalThis.performance?.now ? () => performance.now() : () => Date.now();

// The clock (milliseconds), sleep (seconds) and random a Caller given none of them uses:
// the ones useClock() installed, else performance.now, a timer and Math.random.
export function currentClock() {
  let clock = monotonicMs;
  let sleep = nap;
  let random = Math.random;
  for (const c of clocks) [clock, sleep, random] = [c.clock ?? clock, c.sleep ?? sleep, c.random ?? random];
  return { clock, sleep, random };
}

// Until restore() is called, a Caller given no clock, sleep or random uses these, each
// looked up at each call, so a harness reaches a Caller made before it. Each one not
// given stays as it was. For test harnesses only.
export function useClock({ clock, sleep, random } = {}) {
  const entry = { clock, sleep, random };
  clocks.push(entry);
  return function restore() {
    const at = clocks.lastIndexOf(entry);
    if (at >= 0) clocks.splice(at, 1);
  };
}

// Until restore() is called, currentTransport() is transport (an object with a fetch, or
// a fetch), so a Caller given no fetch calls through it. For test harnesses only.
export function use(transport) {
  const entry = asTransport(transport);
  installed.push(entry);
  return function restore() {
    const at = installed.lastIndexOf(entry);
    if (at >= 0) installed.splice(at, 1);
  };
}

// --- bytes ---------------------------------------------------------------

function toBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function fromBase64(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

// SHA-256, in hex: stand-ins and digests are made synchronously, and a Worker has no
// node:crypto.
export function sha256Hex(bytes) {
  const length = bytes.length;
  const padded = new Uint8Array(((length + 9 + 63) >> 6) << 6);
  padded.set(bytes);
  padded[length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(length / 0x20000000));
  view.setUint32(padded.length - 4, (length << 3) >>> 0);
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const w = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let at = 0; at < padded.length; at += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(at + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, k] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (k + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      [k, g, f, e, d, c, b, a] = [g, f, e, (d + t1) >>> 0, c, b, a, (t1 + t2) >>> 0];
    }
    [a, b, c, d, e, f, g, k].forEach((v, i) => {
      h[i] = (h[i] + v) >>> 0;
    });
  }
  return [...h].map((v) => v.toString(16).padStart(8, "0")).join("");
}
