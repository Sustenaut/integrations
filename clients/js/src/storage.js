// The storage service's client: one tenant's files, named heads and buffers, each call
// made through a Caller (caller.js), so it has its class's timeout and retries and
// writes its record as plugin storage.api. ROUTES above is generated from the service's
// own spec; this is the part written by hand, as src/integrations/clients/storage.py is.
//
//   import { Storage } from "@sustenaut/integrations/storage.js";
//   const storage = new Storage({ tenant: "opportunity", tokenFile: "/var/run/secrets/storage/token", caller });
//   const { id } = await storage.uploadFile(bytes, { name: "offer.pdf", subject: "card-42" });
//   const files = await storage.listFiles({ subject: "card-42" });
//
// Storage answering no is a StorageError (NotFound for a 404) with its status and its
// {"error"}; storage failing or not answering is Unavailable, with them where it answered.

const PLUGIN = "storage.api";
const BASE_URL = "https://storage.sustenaut.eu";

// Storage said no: status is its HTTP status, error its reason.
export class StorageError extends Refused {
  constructor(operation, status, error) {
    super(PLUGIN, operation, `HTTP ${status}${error ? ` ${error}` : ""}`);
    Object.assign(this, { status, error });
  }
}

export class NotFound extends StorageError {}

// Storage failed (5xx, 429) or didn't answer (status null).
export class StorageUnavailable extends Unavailable {
  constructor(cause) {
    super(cause.plugin, cause.operation, cause.detail, cause.answer);
    Object.assign(this, { status: cause.answer?.status ?? null, error: reason(cause.answer) });
  }
}

// Storage's {"error": ...}, or "" when the body has none.
function reason(answer) {
  try {
    const error = answer?.json()?.error;
    return error === undefined || error === null ? "" : String(error).slice(0, 200);
  } catch {
    return "";
  }
}

const BUFFER_ROUTES = { inbox: ["Inbox", "path"], rejected: ["Rejected", "path"], batches: ["Batches", "name"] };

export class Storage {
  // token is a string or a function giving one; tokenFile is read for each request, as
  // Kubernetes renews it. requestId, when given, goes with every call as X-Request-Id.
  constructor({ tenant, baseUrl = BASE_URL, token, tokenFile, readFile, caller, requestId, userAgent } = {}) {
    if (!tenant) throw new TypeError("a Storage needs a tenant");
    if (!token && !tokenFile) throw new TypeError("a Storage needs a token or a tokenFile");
    Object.assign(this, { tenant, baseUrl: baseUrl.replace(/\/+$/, ""), token, tokenFile, readFile, requestId, userAgent });
    this.caller = caller ?? new Caller(tenant);
  }

  // The same client, for another tenant.
  forTenant(tenant) {
    const { baseUrl, token, tokenFile, readFile, caller, requestId, userAgent } = this;
    return new Storage({ tenant, baseUrl, token, tokenFile, readFile, caller, requestId, userAgent });
  }

  // --- Files

  // Stores one file under its content's hash, and answers {id, name, url, type, size, label, subject, kept}.
  async uploadFile(data, { name, label, subject, kept, for: forWhom, ...options } = {}) {
    if (!name) throw new TypeError("uploadFile needs the file's name");
    const form = new FormData();
    form.append("file", data instanceof Blob ? data : new Blob([data]), name);
    form.append("name", name);
    for (const [field, value] of [["label", label], ["subject", subject], ["kept", kept], ["for", forWhom]]) {
      if (value !== undefined && value !== null) form.append(field, String(value));
    }
    return (await this.#call("uploadFile", "write", { body: form }, options)).value;
  }

  // A stored file's bytes.
  async getFile(id, name, options = {}) {
    return (await this.#call("getFile", "read", { path: { id, name } }, options)).value;
  }

  // Every file whose name starts with prefix (f/, f/<id>/ ...), from every page; with
  // subject, only the files an upload gave that subject.
  async listFiles({ prefix = "f/", subject, limit, ...options } = {}) {
    const files = await this.#pages(prefix, limit, "read", options);
    return subject === undefined ? files : files.filter((f) => f.metadata?.subject === subject);
  }

  // --- Named heads

  // The file a head points at, and its hash (etag).
  async getHead(name, options = {}) {
    const { value, answer } = await this.#call("getHead", "read", { path: { name } }, options);
    return { body: value, hash: answer.etag?.replace(/^W\//, "").replaceAll('"', "") ?? null };
  }

  // Moves the head to a stored file's hash, if it still points at ifMatch; without
  // ifMatch, only if it has never moved. Answers {hash, key, time, principal}.
  async moveHead(name, hash, { ifMatch, ...options } = {}) {
    const headers = ifMatch ? { "If-Match": `"${ifMatch}"` } : { "If-None-Match": "*" };
    return (await this.#call("moveHead", "write", { path: { name }, headers, body: { hash } }, options)).value;
  }

  // Where the head points, then where it pointed, newest first.
  async headLog(name, options = {}) {
    return (await this.#call("getHeadLog", "read", { path: { name } }, options)).value.log;
  }

  // --- Buffers: place is the file's place in the tenant, such as inbox/calls/x.avro.

  async putBuffer(place, data, options = {}) {
    return (await this.#buffer("put", place, options, data)).value;
  }

  async getBuffer(place, options = {}) {
    return (await this.#buffer("get", place, options)).value;
  }

  async deleteBuffer(place, options = {}) {
    await this.#buffer("delete", place, options);
  }

  // Every file under prefix (inbox/, batches/, rejected/ and folders under them), from every page.
  async listBuffer(prefix, { limit, ...options } = {}) {
    return this.#pages(prefix, limit, "buffer", options);
  }

  async #buffer(verb, place, options, data) {
    const [folder, ...rest] = String(place).split("/");
    const route = BUFFER_ROUTES[folder];
    if (!route || !rest.length) throw new TypeError(`${place} is not in inbox/, batches/ or rejected/`);
    const [name, param] = route;
    return this.#call(`${verb}${name}`, "buffer", { path: { [param]: rest.join("/") }, body: data }, options);
  }

  async #pages(prefix, limit, operation, options) {
    const found = [];
    let cursor;
    do {
      const page = (await this.#call("list", operation, { query: { prefix, limit, cursor } }, options)).value;
      found.push(...page.files);
      cursor = page.cursor ?? undefined;
    } while (cursor);
    return found;
  }

  // One call: the route's URL, the token, the body as the route takes it, and the answer
  // as it gives it. asOperation records it as another storage.api operation.
  async #call(id, operation, { path = {}, query = {}, headers = {}, body } = {}, { requestId = this.requestId, asOperation = operation } = {}) {
    const route = ROUTES[id];
    let url = this.baseUrl + route.path.replace(/\{(\w+)\}/g, (_, name) => {
      if (name === "tenant") return encodeURIComponent(this.tenant);
      const value = String(path[name]);
      return route.slashes.includes(name) ? value.split("/").map(encodeURIComponent).join("/") : encodeURIComponent(value);
    });
    const search = new URLSearchParams();
    for (const name of route.query) if (query[name] !== undefined && query[name] !== null) search.append(name, String(query[name]));
    if (search.size) url += `?${search}`;
    const sent = { Authorization: `Bearer ${await this.#token()}`, ...headers };
    if (requestId) sent["X-Request-Id"] = requestId;
    if (this.userAgent) sent["User-Agent"] = this.userAgent;
    let payload = body;
    const raw = typeof body === "string" || body instanceof Uint8Array || body instanceof ArrayBuffer || body instanceof Blob;
    if (route.body === "application/json" && body !== undefined && !raw) payload = JSON.stringify(body);
    if (route.body && route.body !== "multipart/form-data") sent["Content-Type"] = route.body;
    let answer;
    try {
      answer = await this.caller.call(PLUGIN, asOperation, { method: route.method, url, headers: sent, body: payload ?? null }, { requestId });
    } catch (err) {
      throw err instanceof Unavailable ? new StorageUnavailable(err) : err;
    }
    if (!answer.ok) {
      const Refusal = answer.status === 404 ? NotFound : StorageError;
      throw new Refusal(asOperation, answer.status, reason(answer));
    }
    let value = null;
    if (route.answer === "json") value = answer.json();
    else if (route.answer === "bytes") value = answer.body;
    return { value, answer };
  }

  async #token() {
    const { token, tokenFile, readFile } = this;
    if (token) return typeof token === "function" ? await token() : token;
    // Named in a variable so a Worker's bundler doesn't look for node:fs, which only Node has.
    const fs = "node:fs/promises";
    const read = readFile ?? (await import(fs)).readFile;
    return (await read(tokenFile, "utf8")).trim();
  }
}
