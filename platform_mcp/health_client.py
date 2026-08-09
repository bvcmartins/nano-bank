"""Probe each configured service `/health`. A down service is DATA, not an
exception — probe() never raises; an unreachable or non-2xx service becomes an
ok:False row. `transport` is injectable so tests stub the network."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings


class HealthClient:
    def __init__(self, settings: Settings,
                 transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(timeout=settings.timeout, transport=transport)

    def _one(self, label: str, url: str) -> dict:
        try:
            r = self._http.get(url)
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {}
            status = body.get("status", "ok" if r.is_success else "error")
            ok = r.is_success and status == "ok"
            return {"service": label, "ok": bool(ok), "status": status,
                    "checks": body.get("checks", {})}
        except Exception as e:  # noqa: BLE001 — a down service is data
            return {"service": label, "ok": False, "status": "unreachable",
                    "checks": {}, "error": f"{type(e).__name__}: {e}"}

    def probe(self) -> list[dict]:
        return [self._one(label, url) for label, url in self._s.health_targets]
