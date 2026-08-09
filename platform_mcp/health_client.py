"""Probe each configured service `/health`. A down service is DATA, not an
exception — probe() never raises; an unreachable or non-2xx service becomes an
ok:False row. `transport` is injectable so tests stub the network."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings

# Status/check values that count as healthy. Different services word it
# differently: the agents say "ok", bank-api (Rust) says "healthy". Match any.
_OK_WORDS = {"ok", "healthy", "up", "pass", "passing", "ready", "alive"}


def _check_ok(v) -> bool:
    """A sub-check value is healthy if it's a truthy bool or a healthy word.
    Services disagree on the type: the agents use booleans ({"qdrant": true}),
    bank-api uses strings ({"database": "healthy"})."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in _OK_WORDS
    return bool(v)


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
            ok = r.is_success and str(status).strip().lower() in _OK_WORDS
            # Sub-probes live under `checks` (agents) or `services` (bank-api);
            # normalize their values to booleans for metrics.service_health.
            raw = body.get("checks")
            if raw is None:
                raw = body.get("services", {})
            checks = {k: _check_ok(v) for k, v in (raw or {}).items()}
            return {"service": label, "ok": bool(ok), "status": status,
                    "checks": checks}
        except Exception as e:  # noqa: BLE001 — a down service is data
            return {"service": label, "ok": False, "status": "unreachable",
                    "checks": {}, "error": f"{type(e).__name__}: {e}"}

    def probe(self) -> list[dict]:
        return [self._one(label, url) for label, url in self._s.health_targets]
