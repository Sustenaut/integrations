"""A Google access token from a service account's key: a signed JWT, traded in."""

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
