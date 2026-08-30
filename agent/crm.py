"""The personal manager's CRM identity: a client for the two endpoints it may
call itself (lookup, token-mint — see nano-bank-crm's docs/plans/2026-08-30-
personal-manager-agent-access-crm.md Task 6), and a resolver that lazily directs
the COO to provision access on a cache miss (Task 5 there; the COO-side lever
lives in this repo's operations/mcp_server.py, added by
docs/superpowers/plans/2026-08-30-coo-crm-provisioning-lever.md).

The personal manager never calls POST /api/agent/provision itself — creating a
mandate is the COO's call, not this agent's own. See the design spec's §6 for
why: an agent that could grant itself scope would be partially setting its own
authority, the one thing the CRM's mandate model is built to prevent.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from .config import Settings

# The exact tool names nano-bank-crm's packages/policy/src/tools.ts generates
# for the fixed scopes packages/policy/src/provisioning.ts grants
# (read:Contact.*, read:Activity.*, write:Activity.*) — see that repo's
# ensurePersonalManagerMandate. If those scopes ever change, this set must
# change with them; there is no way to discover it at runtime without an extra
# tools/list round trip on every session, which is not worth paying for a set
# that changes only when a human edits provisioning.ts.
CRM_LLM_TOOL_NAMES = frozenset({
    "query_Contact", "get_Contact",
    "query_Activity", "get_Activity", "create_Activity", "update_Activity",
    "get_approval",
})


class CrmClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self._s = settings
        self._http = httpx.Client(base_url=settings.crm_base_url, timeout=10.0, transport=transport)

    def lookup_mandate(self, customer_id: str) -> Optional[str]:
        r = self._http.get(
            "/api/agent/mandate",
            params={"tenantSlug": self._s.crm_tenant_slug, "customerId": customer_id},
            headers={"authorization": f"Bearer {self._s.crm_lookup_token}"},
        )
        r.raise_for_status()
        return r.json().get("mandateId")

    def issue_token(self, mandate_id: str) -> dict:
        r = self._http.post(
            "/api/agent/token",
            json={
                "tenantSlug": self._s.crm_tenant_slug,
                "agentId": self._s.crm_agent_id,
                "secret": self._s.crm_agent_secret,
                "mandateId": mandate_id,
            },
        )
        r.raise_for_status()
        return r.json()


async def _post_ask(base_url: str, message: str) -> None:
    """A minimal, local POST to the COO's /ask — deliberately not importing
    csuite.collab.post_ask. agent/ ships in its own container
    (agent/Containerfile.api copies only this directory); csuite/ is not
    available at runtime there, so this stays self-contained rather than
    coupling the image to an unrelated package."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(base_url.rstrip("/") + "/ask", json={"message": message})
        r.raise_for_status()


@dataclass
class _Cached:
    mandate_id: str
    token: str
    token_exp: float


class CrmTokenResolver:
    """Resolves a CRM token per customer: cache, else look up an existing
    mandate, else direct the COO to provision one and confirm it landed."""

    def __init__(
        self,
        settings: Settings,
        crm: CrmClient,
        ask_coo: Optional[Callable[[str], Awaitable[None]]],
        profile_lookup: Callable[[str], str],
    ):
        self._settings = settings
        self._crm = crm
        self._ask_coo = ask_coo
        self._profile_lookup = profile_lookup
        self._cache: dict[str, _Cached] = {}

    async def resolve(self, customer_id: str) -> Optional[str]:
        cached = self._cache.get(customer_id)
        if cached and time.time() < cached.token_exp:
            return cached.token

        mandate_id = cached.mandate_id if cached else self._crm.lookup_mandate(customer_id)

        if mandate_id is None:
            if self._ask_coo is None:
                return None
            name = self._profile_lookup(customer_id)
            await self._ask_coo(
                f"Ensure CRM access exists for bank customer {customer_id}, "
                f"whose name is {name}. Call execute_provision_crm_mandate."
            )
            mandate_id = self._crm.lookup_mandate(customer_id)
            if mandate_id is None:
                return None

        issued = self._crm.issue_token(mandate_id)
        expires_at = datetime.fromisoformat(issued["expiresAt"].replace("Z", "+00:00"))
        ttl = (expires_at - datetime.now(timezone.utc)).total_seconds()
        # 80% of TTL, matching BankClient's own refresh margin (operations/bank_client.py).
        self._cache[customer_id] = _Cached(
            mandate_id=mandate_id, token=issued["token"], token_exp=time.time() + max(ttl, 0) * 0.8
        )
        return issued["token"]
