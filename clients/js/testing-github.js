// GitHub's steps for a Node suite whose code calls GitHub through integrations: what a
// scenario's repositories hold, made in the GitHub test org when recording and read from
// the cassette when replaying. src/integrations/testing/github.py, as a test customer
// pubtest's register(api, { customers: [github] }) takes (pubtest 0.3.0 or later):
//
//   import * as cucumber from "@cucumber/cucumber";
//   import { register } from "@sustenaut/pubtest";
//   import { github } from "@sustenaut/integrations/testing/github.js";
//   register(cucumber, { customers: [github] });
//
// - Given the GitHub repository "o/r" holds: + docstring (a JSON object from each file's
//   path to its text). Replaying, it registers the repository under its own name: the
//   code under test calls o/r, as the cassette does. Recording (INTEGRATIONS_MODE=record),
//   it makes the private repository rec-<consumer>-<run>-<process>-<n> in the test org
//   holding exactly those files, in one commit on its default branch (an empty object: an
//   empty repository), and the code under test calls that: repositories(ctx)["o/r"].recorded.
//   That commit is labelled "start" (a later repository's, "start-<name>").
// - Given someone else pushes to "BRANCH" of "o/r": + docstring (each file's path to its
//   new text, or null to delete it), or ... of "o/r" as commit "L": to label the commit.
//   Recording, a real commit on BRANCH (made from the default branch when there is none),
//   so the code under test meets the race for real; replaying, nothing, as the cassette
//   holds what the code met.
//
// Commit labels: a labelled commit's real sha goes in INTEGRATIONS_NAMES as
// ⟦integrations:commit:L⟧, so the Recorder writes the label and a replay hands the code
// under test the label itself. commit(ctx, "o/r", "L") is that commit: its real sha
// recording, the placeholder replaying. A scenario's labels are its own, one commit each;
// an unknown one throws. The process part of a repository's name keeps parallel workers'
// names apart; nothing else in the test org may be named rec-*, as the janitor deletes them.
//
// What the steps themselves call when recording goes through a Caller of their own,
// straight to GitHub (useNetwork() puts a stand-in there for a suite's own tests), and is
// never in the cassette. The repositories' real names reach the Recorder through
// INTEGRATIONS_NAMES, which programs the scenario runs inherit, so the cassette only ever
// sees the scenario's names; a scenario's repositories share one owner, the test org.
// When the scenario ends, passed or failed, pubtest runs the customer's cleanup: its
// repositories are deleted and INTEGRATIONS_NAMES is as it was. A run cut short leaves
// them to the janitor (integrations test-org sweep), which deletes rec-* older than a day.
//
// token(ctx) is the token the code under test calls GitHub with: the recorder app's when
// recording, a stand-in replaying. The org is INTEGRATIONS_TEST_ORG, else SustenautTest;
// the recorder app's credentials are GITHUB_TEST_RECORDER_APP_ID, GITHUB_TEST_RECORDER_APP_KEY
// and GITHUB_TEST_RECORDER_INSTALLATION_ID (or GITHUB_TEST_RECORDER_TOKEN), which
// ./with-secrets exports from Bitwarden.

import { Caller } from "./caller.js";
import { LIVE, MARKER, MODE, NAMES, label, namesFrom } from "./transports.js";

export const CUSTOMER = "github.test";
export const ORG = "INTEGRATIONS_TEST_ORG";
export const DEFAULT_ORG = "SustenautTest";
export const TOKEN = "GITHUB_TEST_RECORDER_TOKEN";
export const APP_ID = "GITHUB_TEST_RECORDER_APP_ID";
export const APP_KEY = "GITHUB_TEST_RECORDER_APP_KEY";
export const INSTALLATION_ID = "GITHUB_TEST_RECORDER_INSTALLATION_ID";
export const DESCRIPTION = "A recording's repository: its run deletes it, and the janitor any left after a day";
// A token's stand-in in replay: a request's Authorization is neither recorded nor matched.
export const REPLAY_TOKEN = `ghs_${"0".repeat(36)}`;
const PLUGIN = "github.repo-ops";
const API = "https://api.github.com";
const SETTLE_TRIES = 10;

const env = () => globalThis.process?.env ?? {};
const recordingMode = () => env()[MODE] === "record";
let network = null;
// This process's part of a repository's name, so parallel workers' and separate runs'
// processes never pick the same one; the number counts within it.
const PROCESS = crypto.randomUUID().replaceAll("-", "").slice(0, 6);
let numbers = 0;
const START = "start";
const LABEL = /^[A-Za-z][A-Za-z0-9_-]*$/;
const recordings = new WeakMap(); // a scenario's tape -> its recording

// Until restore() is called, the steps' own calls go to fetch: a suite's tests of recording
// put a stand-in for the test org here.
export function useNetwork(fetch) {
  const was = network;
  network = fetch;
  return function restore() {
    network = was;
  };
}

class Refused extends Error {
  constructor(message) {
    super(`${PLUGIN} test-org: ${message}`);
    this.name = "Refused";
  }
}

const b64url = (data) => Buffer.from(data).toString("base64url");

// The recorder app's installation token, minted with a JWT its key signs.
async function mint(caller, appId, key, installationId) {
  const { createSign } = await import("node:crypto");
  const now = Math.floor(Date.now() / 1000);
  const head = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = b64url(JSON.stringify({ iat: now - 60, exp: now + 540, iss: String(appId) }));
  const signature = createSign("RSA-SHA256").update(`${head}.${claims}`).sign(key, "base64url");
  const answer = await caller.call("github.app-token", "mint-in-app", {
    method: "POST", url: `${API}/app/installations/${installationId}/access_tokens`,
    headers: { Authorization: `Bearer ${head}.${claims}.${signature}`, Accept: "application/vnd.github+json" },
  });
  if (!answer.ok) throw new Refused(`minting the recorder app's token: HTTP ${answer.status}`);
  return answer.json().token;
}

// The recorder app's token: GITHUB_TEST_RECORDER_TOKEN, else minted from its id, key and
// installation id (once). Throws, naming them, when they are not set.
export function tokenFromEnv(caller, vars = env()) {
  if (vars[TOKEN]) return async () => vars[TOKEN];
  const missing = [APP_ID, APP_KEY, INSTALLATION_ID].filter((n) => !vars[n]);
  if (missing.length) {
    throw new Error(`the GitHub test org needs the recorder app's credentials, and ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} not set: run it through ./with-secrets, which exports ${APP_ID}, ${APP_KEY} and ${INSTALLATION_ID} from Bitwarden's github-test-recorder-* items (or set ${TOKEN} to an installation token)`);
  }
  let minted = null;
  return async () => (minted ??= mint(caller, vars[APP_ID], vars[APP_KEY], vars[INSTALLATION_ID]));
}

const message = (answer) => {
  try {
    const m = answer.json()?.message;
    return m ? `: ${m}` : "";
  } catch {
    return "";
  }
};
const blob = (path, text) => (text === null ? { path, mode: "100644", type: "blob", sha: null } : { path, mode: "100644", type: "blob", content: text });
const enc = encodeURIComponent;
const encBranch = (b) => b.split("/").map(enc).join("/");

// The test org's repositories, as src/integrations/clients/github_test_org.py has them.
export class TestOrg {
  // sleep is the wait for a new repository's first branch: the one given, else the
  // Caller's, looked up at each call, so a harness's useClock() reaches it.
  constructor(caller, token, org = DEFAULT_ORG, { sleep = null } = {}) {
    Object.assign(this, { caller, token, org, given: sleep });
  }

  sleep(seconds) {
    return (this.given ?? this.caller.sleep)(seconds);
  }

  async #call(method, path, body) {
    const headers = { Authorization: `Bearer ${await this.token()}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return this.caller.call(PLUGIN, "test-org", { method, url: `${API}${path}`, headers, body: body === undefined ? null : JSON.stringify(body) });
  }

  #ok(answer, doing) {
    if (!answer.ok) throw new Refused(`${doing}: HTTP ${answer.status}${message(answer)}`);
    return answer.body.length ? answer.json() : null;
  }

  #repo(name) {
    return `/repos/${enc(this.org)}/${enc(name)}`;
  }

  async #ref(name, branch) {
    const answer = await this.#call("GET", `${this.#repo(name)}/git/ref/heads/${encBranch(branch)}`);
    if (answer.status === 404 || answer.status === 409) return null;
    return this.#ok(answer, `reading ${branch} of ${this.org}/${name}`).object.sha;
  }

  async #commit(name, entries, text, parents, baseTree = null) {
    const tree = this.#ok(await this.#call("POST", `${this.#repo(name)}/git/trees`, { tree: entries, ...(baseTree ? { base_tree: baseTree } : {}) }), `writing a tree in ${this.org}/${name}`).sha;
    return this.#ok(await this.#call("POST", `${this.#repo(name)}/git/commits`, { message: text, tree, parents }), `writing a commit in ${this.org}/${name}`).sha;
  }

  // Make the private repository name holding exactly files, in one commit on its default
  // branch, or empty when there are none: [branch, commit or null]. A name taken already
  // throws an error whose taken is true.
  async create(name, files) {
    const answer = await this.#call("POST", `/orgs/${enc(this.org)}/repos`, { name, private: true, auto_init: Object.keys(files).length > 0, description: DESCRIPTION });
    if (answer.status === 422 && new TextDecoder().decode(answer.body).includes("already exists")) throw Object.assign(new Refused(`the test org has a repository ${name} already`), { taken: true });
    const branch = this.#ok(answer, `making ${this.org}/${name}`).default_branch ?? "main";
    if (!Object.keys(files).length) return [branch, null];
    let first = null;
    for (let i = 0; i < SETTLE_TRIES && first === null; i++) {
      first = await this.#ref(name, branch);
      if (first === null) await this.sleep(1);
    }
    if (first === null) throw new Refused(`${this.org}/${name} has no ${branch} after ${SETTLE_TRIES} s`);
    const commit = await this.#commit(name, Object.keys(files).sort().map((p) => blob(p, files[p])), "What the scenario's repository holds", []);
    this.#ok(await this.#call("PATCH", `${this.#repo(name)}/git/refs/heads/${encBranch(branch)}`, { sha: commit, force: true }), `pointing ${branch} of ${this.org}/${name} at its files (from ${first})`);
    return [branch, commit];
  }

  // Someone else's commit on branch: each file set to its text, or deleted where it is
  // null; a branch that does not exist is made from the default branch. The commit.
  async push(name, branch, files) {
    let head = await this.#ref(name, branch);
    const made = head === null;
    if (made) {
      const fallback = this.#ok(await this.#call("GET", this.#repo(name)), `reading ${this.org}/${name}`).default_branch;
      head = fallback !== branch ? await this.#ref(name, fallback) : null;
    }
    const baseTree = head === null ? null : this.#ok(await this.#call("GET", `${this.#repo(name)}/git/commits/${head}`), `reading ${head} in ${this.org}/${name}`).tree.sha;
    const commit = await this.#commit(name, Object.keys(files).sort().map((p) => blob(p, files[p])), "Someone else's commit", head ? [head] : [], baseTree);
    if (made) this.#ok(await this.#call("POST", `${this.#repo(name)}/git/refs`, { ref: `refs/heads/${branch}`, sha: commit }), `making ${branch} of ${this.org}/${name}`);
    else this.#ok(await this.#call("PATCH", `${this.#repo(name)}/git/refs/heads/${encBranch(branch)}`, { sha: commit, force: false }), `pushing to ${branch} of ${this.org}/${name}`);
    return commit;
  }

  // Delete the repository name; false when it was not there.
  async delete(name) {
    const answer = await this.#call("DELETE", this.#repo(name));
    if (answer.status === 404) return false;
    this.#ok(answer, `deleting ${this.org}/${name}`);
    return true;
  }
}

const slug = (text, limit) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, limit).replace(/-+$/, "") || "x";

function ownerAndName(name) {
  const at = name.indexOf("/");
  const [owner, repo] = at < 0 ? [name, ""] : [name.slice(0, at), name.slice(at + 1)];
  if (!owner || !repo || repo.includes("/")) throw new Error(`"${name}" is no repository: a scenario names one "owner/name", such as "o/r"`);
  return [owner, repo];
}

// The repository under test's name: its origin's, else its checkout's directory's.
export async function consumerName(root) {
  const { execFileSync } = await import("node:child_process");
  const path = await import("node:path");
  const git = (...args) => {
    try {
      return execFileSync("git", ["-C", root, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    } catch {
      return "";
    }
  };
  const origin = git("remote", "get-url", "origin");
  if (origin) return origin.replace(/\/+$/, "").split("/").pop().split(":").pop().replace(/\.git$/, "");
  const top = git("rev-parse", "--show-toplevel");
  return path.basename(top || root);
}

// One scenario's repositories in the test org: made as its steps say, their names in
// INTEGRATIONS_NAMES, deleted by close().
export class Recording {
  made = [];
  names = {};
  owner = null;

  constructor(org, consumer, run) {
    Object.assign(this, { org, consumer: slug(consumer, 40), run: slug(run, 16), before: env()[NAMES] });
  }

  async create(logical, files) {
    const [owner, repo] = ownerAndName(logical);
    if (this.owner !== null && owner !== this.owner) throw new Error(`a scenario's repositories are all in the test org, so they share one owner: "${logical}" is not "${this.owner}/…"`);
    let name = null;
    let result = null;
    for (let i = 0; i < 100 && result === null; i++) {
      name = `rec-${this.consumer}-${this.run}-${PROCESS}-${(numbers += 1)}`;
      this.made.push(name); // deleted when the scenario ends, however far this gets
      try {
        result = await this.org.create(name, files);
      } catch (err) {
        if (!err.taken) throw err;
        this.made.splice(this.made.indexOf(name), 1); // another process's: the next number
      }
    }
    if (result === null) throw new Error(`the test org has every name rec-${this.consumer}-${this.run}-${PROCESS}-N tried`);
    this.owner = owner;
    const real = `${this.org.org}/${name}`;
    Object.assign(this.names, { [real]: logical, [name]: repo, [this.org.org]: owner });
    process.env[NAMES] = JSON.stringify({ ...namesFrom(), ...this.names });
    return { name: logical, recorded: real, files: { ...files }, branch: result[0], head: result[1], pushes: [], commits: {} };
  }

  // A labelled commit's real sha is written as its label, ⟦integrations:commit:L⟧.
  label(sha, name) {
    this.names[sha] = label("commit", name);
    process.env[NAMES] = JSON.stringify({ ...namesFrom(), ...this.names });
  }

  async push(repository, branch, files) {
    return this.org.push(repository.recorded.split("/").slice(1).join("/"), branch, files);
  }

  // Delete what the scenario made, and put INTEGRATIONS_NAMES back.
  async close() {
    const errors = [];
    for (const name of [...this.made].reverse()) {
      try {
        await this.org.delete(name);
      } catch (err) {
        errors.push(`${this.org.org}/${name}: ${err?.message ?? err}`);
      }
    }
    this.made = [];
    if (this.before === undefined) delete process.env[NAMES];
    else process.env[NAMES] = this.before;
    if (errors.length) throw new Error(`the test org's repositories were not all deleted (the janitor will): ${errors.join("; ")}`);
  }
}

// The scenario's recording, made at its first step that needs it; pubtest runs the
// customer's cleanup, which closes it, when the scenario ends.
async function recording(ctx) {
  if (!ctx.tape) throw new Error(`recording GitHub repositories needs pubtest's record/replay harness: register(api, { customers: [github] }) from @sustenaut/integrations/testing/github.js`);
  await ctx.tape.namesOf(CUSTOMER); // so pubtest runs this customer's cleanup
  let found = recordings.get(ctx.tape);
  if (!found) {
    const consumer = await consumerName(ctx.repository ?? process.cwd());
    const caller = new Caller(consumer, "pubtest record", { fetch: network ?? LIVE.fetch });
    const org = new TestOrg(caller, tokenFromEnv(caller), env()[ORG] || DEFAULT_ORG);
    found = new Recording(org, consumer, env()[MARKER] || "run");
    recordings.set(ctx.tape, found);
    ctx.githubRecording = found;
  }
  return found;
}

// The scenario's GitHub repositories, by the name its steps give them.
export function repositories(ctx) {
  return (ctx.githubRepositories ??= {});
}

function checkFiles(name, files, deleting) {
  const fits = (v) => typeof v === "string" || (deleting && v === null);
  if (!files || typeof files !== "object" || Array.isArray(files) || !Object.values(files).every(fits)) {
    throw new Error(`${name}'s files are a JSON object from each file's path to ${deleting ? "its text, or null to delete it" : "its text"}`);
  }
  return files;
}

// The scenario's commit labels, each to the repository it is in: one map for the
// scenario, as its placeholders are.
const labels = (ctx) => (ctx.githubLabels ??= {});

function claim(ctx, name, wanted) {
  if (!LABEL.test(wanted)) throw new Error(`"${wanted}" is no commit label: a letter, then letters, digits, _ and -`);
  if (wanted in labels(ctx)) throw new Error(`the commit label "${wanted}" is taken already, by ${labels(ctx)[wanted]}: a scenario's labels are its own, one commit each`);
  labels(ctx)[wanted] = name;
  return wanted;
}

// The label of the commit a repository's files are in: "start" for the first repository a
// scenario fills, "start-<name>" for each later one.
export function startLabel(ctx, name) {
  if (!(START in labels(ctx))) return START;
  return `${START}-${ownerAndName(name)[1].replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

async function labelled(ctx, repository, wanted, sha) {
  const name = claim(ctx, repository.name, wanted);
  if (recordingMode()) {
    (await recording(ctx)).label(sha, name);
    repository.commits[name] = sha;
  } else repository.commits[name] = label("commit", name);
}

// Registers the repository name holding files: replaying, under its own name; recording,
// made in the test org. The commit its files are in is labelled (startLabel()).
export async function hold(ctx, name, files) {
  ownerAndName(name);
  checkFiles(name, files, false);
  const repository = recordingMode() ? await (await recording(ctx)).create(name, files) : { name, recorded: name, files: { ...files }, branch: null, head: null, pushes: [], commits: {} };
  if (Object.keys(files).length) await labelled(ctx, repository, startLabel(ctx, name), repository.head);
  repositories(ctx)[name] = repository;
  return repository;
}

// Someone else's commit on branch of name, labelled asCommit if given: made for real when
// recording, only noted when replaying.
export async function push(ctx, name, branch, files, asCommit = null) {
  const repository = repositories(ctx)[name];
  if (!repository) throw new Error(`no GitHub repository "${name}": a step says what it holds first`);
  checkFiles(name, files, true);
  if (asCommit !== null && (!LABEL.test(asCommit) || asCommit in labels(ctx))) claim(ctx, name, asCommit); // says why it can't be
  const pushed = { branch, files: { ...files }, ...(asCommit === null ? {} : { label: asCommit }) };
  let sha = null;
  if (recordingMode()) sha = pushed.commit = await (await recording(ctx)).push(repository, branch, files);
  if (asCommit !== null) await labelled(ctx, repository, asCommit, sha);
  repository.pushes.push(pushed);
  return repository;
}

// The commit of name a step labelled wanted: its real sha recording, its placeholder
// ⟦integrations:commit:L⟧ replaying, which is what the cassette hands the code under test
// for it, so a scenario's expectations are the same in both.
export function commit(ctx, name, wanted) {
  const repository = repositories(ctx)[name];
  if (!repository) throw new Error(`no GitHub repository "${name}": a step says what it holds first`);
  if (!(wanted in repository.commits)) throw new Error(`${name} has no commit labelled "${wanted}": its labels are ${Object.keys(repository.commits).sort().join(", ") || "none"}`);
  return repository.commits[wanted];
}

// The token the code under test calls GitHub with: the recorder app's when recording, a
// stand-in replaying.
export async function token(ctx) {
  if (!recordingMode()) return REPLAY_TOKEN;
  if (!ctx.githubRecording) throw new Error("recording, the token is the recorder app's: a step says what a GitHub repository holds first");
  return ctx.githubRecording.org.token();
}

// The test customer pubtest's register(api, { customers: [github] }) takes.
export const github = {
  name: CUSTOMER,
  setup() {
    return {};
  },
  async cleanup(tape) {
    const found = recordings.get(tape);
    recordings.delete(tape);
    if (found) await found.close();
  },
  steps: [
    ["Given", "the GitHub repository {string} holds:", (ctx, name, doc) => hold(ctx, name, JSON.parse(doc))],
    ["Given", "someone else pushes to {string} of {string}:", (ctx, branch, name, doc) => push(ctx, name, branch, JSON.parse(doc))],
    ["Given", "someone else pushes to {string} of {string} as commit {string}:", (ctx, branch, name, asCommit, doc) => push(ctx, name, branch, JSON.parse(doc), asCommit)],
  ],
};
