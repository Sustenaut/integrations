"""cloudflare.dns: only `check()`. Terraform manages the records and `pubkit dns
check` reads them with dig, so no code here changes DNS. This says whether the
API token still works, which Terraform would otherwise find out on its next run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..calls import Caller, Check, IntegrationError, Request

PLUGIN = "cloudflare.dns"
VERIFY = "https://api.cloudflare.com/client/v4/user/tokens/verify"


class CloudflareDns:
    def __init__(self, caller: Caller, token: Callable[[], str]):
        self.caller, self.token = caller, token

    def check(self) -> Check:
        try:
            response = self.caller.call(PLUGIN, "check", Request("GET", VERIFY, {"Authorization": f"Bearer {self.token()}"}))
        except IntegrationError as e:
            return Check(PLUGIN, False, e.detail)
        status = (response.json() or {}).get("result", {}).get("status") if response.ok else None
        if status != "active":
            return Check(PLUGIN, False, f"the token is {status or f'refused: HTTP {response.status}'}")
        return Check(PLUGIN, True)


@dataclass
class FakeCloudflareDns:
    working: bool = True

    def check(self) -> Check:
        return Check(PLUGIN, self.working, "" if self.working else "the fake is set to fail")
