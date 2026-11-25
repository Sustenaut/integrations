// A stand-in for the GitHub test org, as a fetch: tests/steps/github_standin.py in Node.
// The calls the recording steps make (repositories, refs, trees, commits, the app's
// token), and what the code under test calls in these scenarios; it keeps what it is
// asked, and answers as GitHub does, a ref someone else moved not fast-forwarded.
import { createHash, verify } from "node:crypto";

export const MINTED = "ghs_minted";

const sha = (kind, data) => createHash("sha1").update(`${kind}:${JSON.stringify(data)}`).digest("hex");
const json = (status, data) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

export class StandInGitHub {
  repos = {};
  trees = {};
  commits = {};
  requests = [];
  taken = 0;
  refuse = {}; // "METHOD /path-pattern" -> status
  publicKey = null;
  appId = null;

  constructor(org = "SustenautTest", tokens = []) {
    this.org = org;
    this.tokens = new Set(tokens);
  }

  tree(files) {
    const at = sha("tree", files);
    this.trees[at] = { ...files };
    return at;
  }

  commit(tree, parents, message = "") {
    const at = sha("commit", [tree, parents, message, Object.keys(this.commits).length]);
    this.commits[at] = { tree, parents: [...parents], message };
    return at;
  }

  addRepo(name, createdAt, files = null) {
    const refs = {};
    if (files !== null) refs.main = this.commit(this.tree(files), [], "first");
    this.repos[name] = { name, created_at: createdAt, private: true, description: "", default_branch: "main", refs };
  }

  files(name, branch) {
    return { ...this.trees[this.commits[this.repos[name].refs[branch]].tree] };
  }

  ancestors(at) {
    const seen = new Set();
    const todo = [at];
    while (todo.length) {
      const one = todo.pop();
      if (seen.has(one)) continue;
      seen.add(one);
      todo.push(...this.commits[one].parents);
    }
    return seen;
  }

  shown(repo) {
    return { name: repo.name, full_name: `${this.org}/${repo.name}`, private: repo.private, default_branch: repo.default_branch, created_at: repo.created_at, owner: { login: this.org }, html_url: `https://github.com/${this.org}/${repo.name}` };
  }

  fetch = async (url, init = {}) => {
    const target = new URL(String(url));
    const path = target.pathname;
    const method = init.method ?? "GET";
    const text = init.body === undefined || init.body === null ? "" : typeof init.body === "string" ? init.body : new TextDecoder().decode(init.body);
    const body = text ? JSON.parse(text) : null;
    const auth = new Headers(init.headers ?? {}).get("authorization") ?? "";
    this.requests.push({ method, path, auth, body, plugin: init.integrations?.plugin, operation: init.integrations?.operation });
    for (const [rule, status] of Object.entries(this.refuse)) {
      const [m, pattern] = rule.split(" ");
      if (m === method && new RegExp(`^${pattern.replaceAll("*", "[^/]+")}$`).test(path)) return json(status, { message: "refused by the stand-in" });
    }
    if (target.host !== "api.github.com") return json(404, { message: "Not Found" });
    if (/^\/app\/installations\/\d+\/access_tokens$/.test(path) && method === "POST") return this.#mint(auth);
    if (!this.tokens.has(auth.replace(/^Bearer /, ""))) return json(401, { message: "Bad credentials" });
    let m = /^\/orgs\/([^/]+)\/repos$/.exec(path);
    if (m) {
      if (decodeURIComponent(m[1]) !== this.org) return json(404, { message: "Not Found" });
      return method === "GET" ? this.#list(target.searchParams) : this.#create(body);
    }
    m = /^\/repos\/([^/]+)\/([^/]+)(\/.*)?$/.exec(path);
    if (!m || decodeURIComponent(m[1]) !== this.org || !(decodeURIComponent(m[2]) in this.repos)) return json(404, { message: "Not Found" });
    const name = decodeURIComponent(m[2]);
    const rest = m[3] ?? "";
    const repo = this.repos[name];
    if (rest === "" && method === "GET") return json(200, this.shown(repo));
    if (rest === "" && method === "DELETE") {
      delete this.repos[name];
      return new Response(null, { status: 204 });
    }
    if ((m = /^\/git\/ref\/heads\/(.+)$/.exec(rest)) && method === "GET") {
      if (!Object.keys(repo.refs).length) return json(409, { message: "Git Repository is empty." });
      const at = repo.refs[decodeURIComponent(m[1])];
      return at ? json(200, { object: { sha: at, type: "commit" } }) : json(404, { message: "Not Found" });
    }
    if ((m = /^\/git\/refs\/heads\/(.+)$/.exec(rest)) && method === "PATCH") {
      const branch = decodeURIComponent(m[1]);
      if (!(branch in repo.refs)) return json(422, { message: "Reference does not exist" });
      if (!body.force && !this.ancestors(body.sha).has(repo.refs[branch])) return json(422, { message: "Update is not a fast forward" });
      repo.refs[branch] = body.sha;
      return json(200, { object: { sha: body.sha } });
    }
    if (rest === "/git/refs" && method === "POST") {
      const branch = body.ref.replace(/^refs\/heads\//, "");
      if (branch in repo.refs) return json(422, { message: "Reference already exists" });
      repo.refs[branch] = body.sha;
      return json(201, { object: { sha: body.sha } });
    }
    if (rest === "/git/trees" && method === "POST") {
      const files = body.base_tree ? { ...this.trees[body.base_tree] } : {};
      for (const entry of body.tree) {
        if (entry.sha === null) delete files[entry.path];
        else files[entry.path] = entry.content;
      }
      return json(201, { sha: this.tree(files) });
    }
    if (rest === "/git/commits" && method === "POST") return json(201, { sha: this.commit(body.tree, body.parents, body.message) });
    if ((m = /^\/git\/commits\/([0-9a-f]{40})$/.exec(rest)) && method === "GET" && this.commits[m[1]]) {
      const found = this.commits[m[1]];
      return json(200, { sha: m[1], tree: { sha: found.tree }, parents: found.parents.map((p) => ({ sha: p })) });
    }
    return json(404, { message: "Not Found" });
  };

  #create(body) {
    if (this.taken > 0 || body.name in this.repos) {
      this.taken = Math.max(0, this.taken - 1);
      return json(422, { message: "Repository creation failed.", errors: [{ message: "name already exists on this account" }] });
    }
    this.addRepo(body.name, new Date().toISOString().slice(0, 19) + "Z", body.auto_init ? { "README.md": `# ${body.name}\n` } : null);
    Object.assign(this.repos[body.name], { private: body.private ?? false, description: body.description ?? "" });
    return json(201, this.shown(this.repos[body.name]));
  }

  #list(params) {
    const page = Number(params.get("page") ?? 1);
    const perPage = Number(params.get("per_page") ?? 30);
    return json(200, Object.keys(this.repos).sort().slice((page - 1) * perPage, page * perPage).map((n) => this.shown(this.repos[n])));
  }

  // A JWT the app's key signed, naming the app, is answered with a token.
  #mint(auth) {
    try {
      const [head, claims, signature] = auth.replace(/^Bearer /, "").split(".");
      const good = verify("RSA-SHA256", Buffer.from(`${head}.${claims}`), this.publicKey, Buffer.from(signature, "base64url"));
      if (!good || JSON.parse(Buffer.from(claims, "base64url").toString()).iss !== this.appId) throw new Error("not the app's");
    } catch {
      return json(401, { message: "A JSON web token could not be decoded" });
    }
    this.tokens.add(MINTED);
    return json(201, { token: MINTED, expires_at: "2099-01-01T00:00:00Z" });
  }
}
