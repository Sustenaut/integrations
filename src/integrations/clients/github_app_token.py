"""github.app-token: a GitHub App's installation tokens, for code that acts as
the app. Needs the `github-app` extra, which signs the app's JWT."""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Callable

from ..calls import Caller, Check, IntegrationError, Refused, Request

PLUGIN = "github.app-token"
API = "https://api.github.com"
# Tokens last an hour; one is reused for less than that.
REUSE_SECONDS = 45 * 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def signed_jwt(app_id: str, private_key: str, now: int) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    signature = key.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{_b64url(signature)}"


class AppToken:
    def __init__(self, caller: Caller, app_id: str, private_key: str, installation_id: str,
                 clock: Callable[[], float] = time.time):
        self.caller, self.app_id, self.key, self.installation_id = caller, app_id, private_key, installation_id
        self.clock = clock
        self._token: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()

    def __call__(self) -> str:
        """An installation token, reused until well before it expires."""
        with self._lock:
            token, until = self._token
            if self.clock() < until:
                return token
            response = self.caller.call(PLUGIN, "mint-in-app", Request(
                "POST", f"{API}/app/installations/{self.installation_id}/access_tokens",
                {"Authorization": f"Bearer {signed_jwt(self.app_id, self.key, int(self.clock()))}",
                 "Accept": "application/vnd.github+json"}))
            if not response.ok:
                raise Refused(PLUGIN, "mint-in-app", f"HTTP {response.status}")
            self._token = (response.json()["token"], self.clock() + REUSE_SECONDS)
            return self._token[0]

    def check(self) -> Check:
        try:
            self._token = ("", 0.0)
            self()
        except IntegrationError as e:
            return Check(PLUGIN, False, e.detail)
        return Check(PLUGIN, True)


class FakeAppToken:
    """A stand-in that hands out a fixed token and signs nothing."""

    def __init__(self, token: str = "fake-token", working: bool = True):
        self.token, self.working = token, working

    def __call__(self) -> str:
        return self.token

    def check(self) -> Check:
        return Check(PLUGIN, self.working, "" if self.working else "the fake is set to fail")
