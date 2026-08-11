"""Post a CTO action to the bank's ledger endpoint. Loud, not best-effort: an
autonomous action must never land without its audit row, so any failure raises
and the lever reports it. Mints + caches a service token like operations'
BankClient."""
from __future__ import annotations
import time
from typing import Optional

import httpx

from .config import Settings


class LedgerAudit:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(base_url=settings.bank_api, timeout=settings.timeout,
                                  transport=transport)
        self._token: Optional[str] = None
        self._exp: float = 0.0

    def _bearer(self) -> str:
        if self._token is None or time.time() >= self._exp:
            r = self._http.post("/api/v1/auth/service-token",
                                json={"client_secret": self._s.service_client_secret})
            r.raise_for_status()
            b = r.json()
            self._token = b["access_token"]
            self._exp = time.time() + float(b.get("expires_in", 900)) * 0.8
        return self._token

    def post_action(self, action: str, params: dict, effect: dict) -> dict:
        r = self._http.post(
            "/api/v1/agent-ledger/actions",
            headers={"authorization": f"Bearer {self._bearer()}"},
            json={"action": action, "params": params, "effect": effect})
        r.raise_for_status()
        return r.json()
