"""A stand-in for the GitHub test org, as a Transport: the calls the recording steps and
the janitor make (repositories, refs, trees, commits, the app's token), and what the
code under test calls in these scenarios. It keeps what it is asked, and answers as
GitHub does, races included: a ref moved by someone else is not fast-forwarded."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

from integrations.calls import Request, Response

MINTED = "ghs_minted"


def _sha(kind: str, data) -> str:
    return hashlib.sha1(f"{kind}:{json.dumps(data, sort_keys=True)}".encode()).hexdigest()


class StandInGitHub:
    def __init__(self, org: str = "SustenautTest", tokens: tuple[str, ...] = ()):
        self.org = org
        self.repos: dict[str, dict] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.commits: dict[str, dict] = {}
        self.tokens = set(tokens)
        self.public_key = None  # the app's, to check a JWT's signature
        self.app_id: str | None = None
        self.requests: list[dict] = []
        self.taken: int = 0  # this many names are taken, as another process's
        self.refuse: dict[str, int] = {}  # "METHOD /path-pattern" -> status
        self.now = lambda: datetime.now(timezone.utc)

    # --- state ---------------------------------------------------------------------

    def tree(self, files: dict[str, str]) -> str:
        sha = _sha("tree", files)
        self.trees[sha] = dict(files)
        return sha

    def commit(self, tree: str, parents: list[str], message: str = "") -> str:
        sha = _sha("commit", [tree, parents, message, len(self.commits)])
        self.commits[sha] = {"tree": tree, "parents": list(parents), "message": message}
        return sha

    def add_repo(self, name: str, created_at: str, files: dict[str, str] | None = None) -> None:
        refs = {}
        if files is not None:
            refs["main"] = self.commit(self.tree(files), [], "first")
        self.repos[name] = {"name": name, "created_at": created_at, "private": True, "description": "",
                            "default_branch": "main", "refs": refs}

    def files(self, name: str, branch: str) -> dict[str, str]:
        return dict(self.trees[self.commits[self.repos[name]["refs"][branch]]["tree"]])

    def ancestors(self, sha: str) -> set[str]:
        seen, todo = set(), [sha]
        while todo:
            at = todo.pop()
            if at not in seen:
                seen.add(at)
                todo += self.commits[at]["parents"]
        return seen

    # --- the transport -------------------------------------------------------------------

    def send(self, request: Request, timeout: float) -> Response:
        parts = urlsplit(request.url)
        path, query = parts.path, parse_qs(parts.query)
        body = json.loads(request.body) if request.body else None
        auth = {k.lower(): v for k, v in request.headers.items()}.get("authorization", "")
        self.requests.append({"method": request.method, "path": path, "query": parts.query, "auth": auth,
                              "body": body, "plugin": request.plugin, "operation": request.operation})
        for rule, status in self.refuse.items():
            method, _, pattern = rule.partition(" ")
            if method == request.method and re.fullmatch(pattern.replace("*", "[^/]+"), path):
                return self._json(status, {"message": "refused by the stand-in"})
        if parts.netloc != "api.github.com":
            return self._json(404, {"message": "Not Found"})
        m = re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        if m and request.method == "POST":
            return self._mint(auth)
        if auth.removeprefix("Bearer ") not in self.tokens:
            return self._json(401, {"message": "Bad credentials"})
        m = re.fullmatch(r"/orgs/([^/]+)/repos", path)
        if m:
            if unquote(m.group(1)) != self.org:
                return self._json(404, {"message": "Not Found"})
            return self._list(query) if request.method == "GET" else self._create(body)
        m = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", path)
        if not m or unquote(m.group(1)) != self.org or unquote(m.group(2)) not in self.repos:
            return self._json(404, {"message": "Not Found"})
        name, rest = unquote(m.group(2)), m.group(3) or ""
        repo = self.repos[name]
        if rest == "" and request.method == "GET":
            return self._json(200, self._shown(repo))
        if rest == "" and request.method == "DELETE":
            del self.repos[name]
            return Response(204)
        m = re.fullmatch(r"/git/ref/heads/(.+)", rest)
        if m and request.method == "GET":
            if not repo["refs"]:
                return self._json(409, {"message": "Git Repository is empty."})
            sha = repo["refs"].get(unquote(m.group(1)))
            return self._json(200, {"object": {"sha": sha, "type": "commit"}}) if sha else self._json(
                404, {"message": "Not Found"})
        m = re.fullmatch(r"/git/refs/heads/(.+)", rest)
        if m and request.method == "PATCH":
            branch = unquote(m.group(1))
            if branch not in repo["refs"]:
                return self._json(422, {"message": "Reference does not exist"})
            if not body.get("force") and repo["refs"][branch] not in self.ancestors(body["sha"]):
                return self._json(422, {"message": "Update is not a fast forward"})
            repo["refs"][branch] = body["sha"]
            return self._json(200, {"object": {"sha": body["sha"]}})
        if rest == "/git/refs" and request.method == "POST":
            branch = body["ref"].removeprefix("refs/heads/")
            if branch in repo["refs"]:
                return self._json(422, {"message": "Reference already exists"})
            repo["refs"][branch] = body["sha"]
            return self._json(201, {"object": {"sha": body["sha"]}})
        if rest == "/git/trees" and request.method == "POST":
            files = dict(self.trees[body["base_tree"]]) if body.get("base_tree") else {}
            for entry in body["tree"]:
                if entry.get("sha", "") is None:
                    files.pop(entry["path"], None)
                else:
                    files[entry["path"]] = entry["content"]
            return self._json(201, {"sha": self.tree(files)})
        if rest == "/git/commits" and request.method == "POST":
            return self._json(201, {"sha": self.commit(body["tree"], body["parents"], body["message"])})
        m = re.fullmatch(r"/git/commits/([0-9a-f]{40})", rest)
        if m and request.method == "GET" and m.group(1) in self.commits:
            found = self.commits[m.group(1)]
            return self._json(200, {"sha": m.group(1), "tree": {"sha": found["tree"]},
                                    "parents": [{"sha": p} for p in found["parents"]]})
        return self._json(404, {"message": "Not Found"})

    @staticmethod
    def _json(status: int, data) -> Response:
        return Response(status, json.dumps(data).encode(), headers={"Content-Type": "application/json"})

    def _shown(self, repo: dict) -> dict:
        return {"name": repo["name"], "full_name": f"{self.org}/{repo['name']}", "private": repo["private"],
                "default_branch": repo["default_branch"], "created_at": repo["created_at"],
                "owner": {"login": self.org}, "html_url": f"https://github.com/{self.org}/{repo['name']}"}

    def _create(self, body: dict) -> Response:
        name = body["name"]
        if self.taken > 0 or name in self.repos:
            self.taken = max(0, self.taken - 1)
            return self._json(422, {"message": "Repository creation failed.",
                                    "errors": [{"message": "name already exists on this account"}]})
        self.add_repo(name, self.now().strftime("%Y-%m-%dT%H:%M:%SZ"), {"README.md": f"# {name}\n"}
                      if body.get("auto_init") else None)
        self.repos[name].update(private=body.get("private", False), description=body.get("description", ""))
        return self._json(201, self._shown(self.repos[name]))

    def _list(self, query: dict) -> Response:
        page, per_page = int(query.get("page", ["1"])[0]), int(query.get("per_page", ["30"])[0])
        names = sorted(self.repos)[(page - 1) * per_page:page * per_page]
        return self._json(200, [self._shown(self.repos[n]) for n in names])

    def _mint(self, auth: str) -> Response:
        """A JWT the app's key signed, naming the app, is answered with a token."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        jwt = auth.removeprefix("Bearer ")
        try:
            head, claims, signature = jwt.split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
            self.public_key.verify(pad(signature), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            if json.loads(pad(claims))["iss"] != self.app_id:
                raise ValueError("another app")
        except Exception:  # noqa: BLE001 - any JWT that does not check out is refused, as GitHub does
            return self._json(401, {"message": "A JSON web token could not be decoded"})
        self.tokens.add(MINTED)
        return self._json(201, {"token": MINTED, "expires_at": "2099-01-01T00:00:00Z"})
