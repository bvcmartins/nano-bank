"""HTTP client for the bank's service-plane back-office reads. Mints and caches
a service token (refreshing at 80% of its TTL) and fetches the five ops reads.
`transport` is injectable so tests can stub the network."""
from __future__ import annotations
import time
from typing import Optional

import httpx

from .config import Settings


class BankClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.nano_bank_api, timeout=settings.timeout, transport=transport
        )
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    def _bearer(self) -> str:
        # Refresh at 80% of TTL (or on first use / after expiry).
        if self._token is None or time.time() >= self._token_exp:
            r = self._http.post(
                "/api/v1/auth/service-token",
                json={"client_secret": self._s.service_client_secret},
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            ttl = float(body.get("expires_in", 900))
            self._token_exp = time.time() + ttl * 0.8
        return self._token

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self._http.get(path, params=params, headers={"authorization": f"Bearer {self._bearer()}"})
        r.raise_for_status()
        return r.json()

    def _post(self, path: str) -> dict:
        r = self._http.post(path, headers={"authorization": f"Bearer {self._bearer()}"})
        r.raise_for_status()
        return r.json()

    def float_(self) -> dict:
        return self._get("/api/v1/back-office/ops/float")

    def transactions(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/transactions", {"window": window})

    def rails(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/rails", {"window": window})

    def exceptions(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/exceptions", {"window": window})

    def cards(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/cards", {"window": window})

    def declines(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/declines", {"window": window})

    # --- Autonomous operational levers (self-verifying + audited server-side) ---
    # Each returns {"outcome": "executed"|"refused", ...}. The bank re-checks a
    # deterministic precondition and writes the attempt to the tamper-evident
    # agent-action ledger; the COO cannot bypass or suppress either.
    def cut_aft_batch(self) -> dict:
        return self._post("/api/v1/ops-levers/cut-aft-batch")

    def sweep_expired_etransfers(self) -> dict:
        return self._post("/api/v1/ops-levers/sweep-expired-etransfers")

    def reject_stale_wires(self) -> dict:
        return self._post("/api/v1/ops-levers/reject-stale-wires")

    def flush_notifications(self) -> dict:
        return self._post("/api/v1/ops-levers/flush-notifications")
