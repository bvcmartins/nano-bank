"""HTTP client the delegation lever uses to reach the in-cluster coder service.
Mirrors audit.LedgerAudit's httpx shape; no auth (in-cluster service-to-service)."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings


class CoderClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(base_url=settings.coder_url,
                                  timeout=settings.coder_timeout, transport=transport)

    def code_task(self, kind: str, task: str) -> dict:
        r = self._http.post("/code-task", json={"kind": kind, "task": task})
        r.raise_for_status()
        return r.json()
