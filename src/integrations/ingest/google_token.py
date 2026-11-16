"""A Google access token: from a service account's key (a signed JWT, traded in), or
with no key, from the pod's projected token through Workload Identity Federation."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable
from urllib.parse import urlencode

from ..calls import Caller, Refused, Request
from ..clients.github_app_token import _b64url

PLUGIN = "google.bigquery"
SCOPE = "https://www.googleapis.com/auth/bigquery"
LIFETIME = 3600
# Traded in again well before Google would refuse it.
MARGIN = 300


class GoogleToken:
    """Callable: gives a token good for now."""

    def __init__(self, caller: Caller, key: dict, clock: Callable[[], float] = time.time):
        self.caller, self.key, self.clock = caller, key, clock
        self._token: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()

    def _assertion(self) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(self.clock())
        head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64url(json.dumps({"iss": self.key["client_email"], "scope": SCOPE, "aud": self.key["token_uri"],
                                     "iat": now, "exp": now + LIFETIME}).encode())
        private = serialization.load_pem_private_key(self.key["private_key"].encode(), password=None)
        signature = private.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{claims}.{_b64url(signature)}"

    def __call__(self) -> str:
        with self._lock:
            token, until = self._token
            if self.clock() < until:
                return token
            body = urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                              "assertion": self._assertion()}).encode()
            answer = self.caller.call(PLUGIN, "load", Request(
                "POST", self.key["token_uri"], {"Content-Type": "application/x-www-form-urlencoded"}, body,
                repeatable=True))
            if not answer.ok:
                raise Refused(PLUGIN, "load", f"token: HTTP {answer.status}")
            self._token = (answer.json()["access_token"], self.clock() + LIFETIME - MARGIN)
            return self._token[0]


TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
CLOUD = "https://www.googleapis.com/auth/cloud-platform"


class FederatedToken:
    """Callable, as GoogleToken: a token for a Google service account, with no key.

    `config` is an external_account credential file's JSON (Workload Identity
    Federation): the pod's projected Kubernetes token, read from
    credential_source.file each time, is traded at Google's STS (token_url) for a
    federated token, which then asks service_account_impersonation_url for the
    account's access token."""

    def __init__(self, caller: Caller, config: dict, clock: Callable[[], float] = time.time,
                 read: Callable[[str], str] | None = None):
        if config.get("type") != "external_account" or not config.get("service_account_impersonation_url"):
            raise ValueError("not an external_account credential that impersonates a service account")
        self.caller, self.config, self.clock = caller, config, clock
        self.read = read or (lambda path: open(path, encoding="utf-8").read())
        self._token: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()

    def _post(self, url: str, headers: dict, body: bytes, what: str) -> dict:
        answer = self.caller.call(PLUGIN, "load", Request("POST", url, headers, body, repeatable=True))
        if not answer.ok:
            raise Refused(PLUGIN, "load", f"{what}: HTTP {answer.status}")
        return answer.json() or {}

    def __call__(self) -> str:
        with self._lock:
            token, until = self._token
            if self.clock() < until:
                return token
            c = self.config
            subject = self.read(c["credential_source"]["file"]).strip()
            federated = self._post(c.get("token_url", "https://sts.googleapis.com/v1/token"),
                                   {"Content-Type": "application/x-www-form-urlencoded"}, urlencode({
                                       "grant_type": TOKEN_EXCHANGE, "audience": c["audience"], "scope": CLOUD,
                                       "requested_token_type": ACCESS_TOKEN, "subject_token": subject,
                                       "subject_token_type": c["subject_token_type"]}).encode(), "token exchange")
            del subject
            access = self._post(c["service_account_impersonation_url"],
                                {"Authorization": f"Bearer {federated['access_token']}",
                                 "Content-Type": "application/json"},
                                json.dumps({"scope": [SCOPE], "lifetime": f"{LIFETIME}s"}).encode(), "impersonation")
            self._token = (access["accessToken"], self.clock() + LIFETIME - MARGIN)
            return self._token[0]


def project_of(credential: dict) -> str:
    """The project of a key's account, or of the account an external_account acts as."""
    if credential.get("project_id"):
        return str(credential["project_id"])
    url = str(credential.get("service_account_impersonation_url", ""))
    account = url.rsplit("/", 1)[-1].split(":", 1)[0]
    if "@" in account and account.endswith(".iam.gserviceaccount.com"):
        return account.split("@", 1)[1].split(".", 1)[0]
    raise ValueError("the credential names no project")


def google_token(caller: Caller, credential: dict, clock: Callable[[], float] = time.time) -> Callable[[], str]:
    """The token callable for a credential: a key's (GoogleToken) or an external_account's (FederatedToken)."""
    if credential.get("type") == "external_account":
        return FederatedToken(caller, credential, clock)
    return GoogleToken(caller, credential, clock)
