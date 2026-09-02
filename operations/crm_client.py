"""HTTP client for the CRM's mandate-provisioning endpoint — the COO's one
write into nano-bank-crm. `transport` is injectable so tests can stub the
network, the same shape as BankClient."""
from __future__ import annotations
from typing import Optional

import httpx

from .config import Settings


class CrmClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.crm_base_url, timeout=settings.timeout, transport=transport
        )

    def ensure_mandate(self, customer_id: str, contact_name: str) -> dict:
        """Get-or-create: idempotent on the CRM side. Raises httpx.HTTPStatusError
        on anything but 2xx — a bad provisioning token, an unknown tenant, or a
        misconfigured grantor all look identical from here, by the CRM's own
        design (see nano-bank-crm's POST /api/agent/provision)."""
        r = self._http.post(
            "/api/agent/provision",
            json={
                "tenantSlug": self._s.crm_tenant_slug,
                "serviceToken": self._s.crm_provisioning_token,
                "customerId": customer_id,
                "contactName": contact_name,
            },
        )
        r.raise_for_status()
        return r.json()
