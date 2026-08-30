# COO CRM-Provisioning Lever Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the COO a new autonomous lever, `execute_provision_crm_mandate`, that ensures the personal manager has CRM access for a given bank customer — the only path by which that access is ever created.

**Architecture:** A new `CrmClient` in `operations/`, the exact shape of the existing `BankClient` (injectable `httpx` transport, settings-driven base URL), calling nano-bank-crm's `POST /api/agent/provision` (built in the CRM-side plan). One new tool joins the existing "Autonomous operational levers" section of `operations/mcp_server.py`, which is all the COO needs — its tool list is assembled dynamically from this MCP server at `/ask` time (`coo/tools.py`), so no change is needed in `coo/` itself.

**Tech Stack:** Python, httpx, FastMCP, pytest.

**Spec:** [nano-bank-crm's docs/specs/2026-08-29-personal-manager-agent-access-design.md](../../../../nano-bank-crm/docs/specs/2026-08-29-personal-manager-agent-access-design.md) §6 (this plan is the COO-side half; the CRM-side half is a sibling plan in the `nano-bank-crm` repo, `docs/plans/2026-08-30-personal-manager-agent-access-crm.md`, which this plan's Task 2 calls directly).

## Global Constraints

- Every existing lever in `operations/mcp_server.py` is self-verifying and audited **server-side**, on the system it acts against — the MCP tool code itself never writes to a ledger. This lever follows the same shape: the CRM's own `agent_actions` table (already built there) is the audit trail, not anything added here.
- `httpx.MockTransport` is how this codebase tests HTTP clients without a live server — see `operations/tests/test_bank_client.py`. `CrmClient` gets the identical treatment.
- No secret this lever handles (`crm_provisioning_token`) is ever logged, returned in a tool result, or otherwise made visible to the COO's own LLM loop — only the *result* of provisioning (mandate id, contact id, created flag) is.

---

### Task 1: `Settings` gains the CRM's provisioning credentials

**Files:**
- Modify: `operations/config.py`
- Test: `operations/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.crm_base_url: str`, `Settings.crm_tenant_slug: str`, `Settings.crm_provisioning_token: str` — consumed by Task 2's `CrmClient`.

- [ ] **Step 1: Write the failing test**

```python
# operations/tests/test_config.py — add these two tests
import pytest
from operations.config import Settings


def test_from_env_reads_crm_settings():
    s = Settings.from_env(
        {
            "SERVICE_CLIENT_SECRET": "shared-secret",
            "CRM_PROVISIONING_TOKEN": "co-token",
            "CRM_BASE_URL": "http://crm.test",
            "CRM_TENANT_SLUG": "acme",
        }
    )
    assert s.crm_base_url == "http://crm.test"
    assert s.crm_tenant_slug == "acme"
    assert s.crm_provisioning_token == "co-token"


def test_from_env_fails_loud_without_crm_provisioning_token():
    with pytest.raises(RuntimeError, match="CRM_PROVISIONING_TOKEN"):
        Settings.from_env({"SERVICE_CLIENT_SECRET": "shared-secret"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest operations/tests/test_config.py -v`
Expected: FAIL — `Settings` has no `crm_base_url` field (`TypeError` on construction inside `from_env`, or an `AttributeError` on the assertion, depending on how far the existing code gets).

- [ ] **Step 3: Implement**

In `operations/config.py`:

```python
@dataclass
class Settings:
    nano_bank_api: str
    service_client_secret: str
    mcp_port: int
    timeout: float
    crm_base_url: str
    crm_tenant_slug: str
    crm_provisioning_token: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env
        secret = e.get("SERVICE_CLIENT_SECRET")
        if not secret:
            raise RuntimeError(
                "SERVICE_CLIENT_SECRET is not set. It must match the bank's "
                "NANO_BANK__SECURITY__SERVICE_CLIENT_SECRET; refusing to fall "
                "back to the well-known dev default."
            )
        crm_token = e.get("CRM_PROVISIONING_TOKEN")
        if not crm_token:
            raise RuntimeError(
                "CRM_PROVISIONING_TOKEN is not set. It must match nano-bank-crm's "
                "CO_PROVISIONING_TOKEN; refusing to run the CRM-provisioning lever "
                "without it."
            )
        return cls(
            nano_bank_api=e.get("NANO_BANK_API", "http://localhost:8081"),
            service_client_secret=secret,
            mcp_port=int(e.get("MCP_PORT", "8092")),
            timeout=float(e.get("REQUEST_TIMEOUT", "10.0")),
            crm_base_url=e.get("CRM_BASE_URL", "http://localhost:3000"),
            crm_tenant_slug=e.get("CRM_TENANT_SLUG", "acme"),
            crm_provisioning_token=crm_token,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest operations/tests/test_config.py -v`
Expected: PASS, all prior tests in the file still green.

- [ ] **Step 5: Commit**

```bash
git add operations/config.py operations/tests/test_config.py
git commit -m "feat(operations): add CRM provisioning settings"
```

---

### Task 2: `operations/crm_client.py`

**Files:**
- Create: `operations/crm_client.py`
- Test: `operations/tests/test_crm_client.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces: `CrmClient.ensure_mandate(customer_id: str, contact_name: str) -> dict`, returning the CRM's own JSON response verbatim — `{"mandateId": ..., "contactId": ..., "created": bool}`. Consumed by Task 3's new tool.

- [ ] **Step 1: Write the failing tests**

```python
# operations/tests/test_crm_client.py
import json
import httpx
from operations.config import Settings
from operations.crm_client import CrmClient


def _settings():
    return Settings(
        nano_bank_api="http://bank.test",
        service_client_secret="secret",
        mcp_port=8092,
        timeout=5.0,
        crm_base_url="http://crm.test",
        crm_tenant_slug="acme",
        crm_provisioning_token="co-token",
    )


def test_ensure_mandate_posts_the_expected_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"mandateId": "m-1", "contactId": "c-1", "created": True})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.ensure_mandate("cust-200", "Priya Nair")

    assert seen["path"] == "/api/agent/provision"
    assert seen["body"] == {
        "tenantSlug": "acme",
        "serviceToken": "co-token",
        "customerId": "cust-200",
        "contactName": "Priya Nair",
    }
    assert result == {"mandateId": "m-1", "contactId": "c-1", "created": True}


def test_ensure_mandate_raises_on_a_non_2xx_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "INVALID_CREDENTIALS", "message": "no"}})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    try:
        client.ensure_mandate("cust-1", "Someone")
        assert False, "expected an HTTPStatusError"
    except httpx.HTTPStatusError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest operations/tests/test_crm_client.py -v`
Expected: FAIL — `operations.crm_client` does not exist.

- [ ] **Step 3: Implement**

```python
# operations/crm_client.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest operations/tests/test_crm_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add operations/crm_client.py operations/tests/test_crm_client.py
git commit -m "feat(operations): CrmClient for the mandate-provisioning endpoint"
```

---

### Task 3: The `execute_provision_crm_mandate` lever

**Files:**
- Modify: `operations/mcp_server.py`

**Interfaces:**
- Consumes: `CrmClient.ensure_mandate` (Task 2).
- Produces: `execute_provision_crm_mandate(customer_id: str, contact_name: str) -> dict`, an MCP tool the COO's agent discovers automatically — no change needed in `coo/`. `build_mcp`'s signature changes from `build_mcp(bank: BankClient)` to `build_mcp(bank: BankClient, crm: CrmClient)`, so `main()` changes too.

No dedicated TDD cycle for the tool registration itself — this repo has no `test_mcp_server.py` (existing levers are exercised through `BankClient`'s own tests, which Task 2 already mirrors for `CrmClient`). Verification is Step 3: importing `build_mcp` and confirming the new tool is present.

- [ ] **Step 1: Add the import and update `build_mcp`'s signature**

In `operations/mcp_server.py`:

```python
from .bank_client import BankClient
from .crm_client import CrmClient
from . import metrics
```

```python
def build_mcp(bank: BankClient, crm: CrmClient) -> FastMCP:
```

- [ ] **Step 2: Add the tool, in the "Autonomous operational levers" section**

Immediately after `execute_flush_notifications` and before `operations_health`:

```python
    @mcp.tool()
    def execute_provision_crm_mandate(customer_id: str, contact_name: str) -> dict:
        """Ensure the personal manager has CRM access for a bank customer:
        creates (or reuses) a CRM contact and a mandate scoped to exactly that
        customer's own records — never anyone else's. Idempotent: calling this
        again for a customer who already has access returns the existing
        mandate rather than creating a duplicate, so use it freely, including
        as a safety check before assuming access exists. The personal manager
        cannot request this itself — provisioning CRM access is your call, not
        its own, so it asks you rather than acting on its own authority.
        `contact_name` should be the customer's real name (from a customer
        profile you already have), used only if no CRM contact exists yet."""
        return _stringify(crm.ensure_mandate(customer_id, contact_name))
```

- [ ] **Step 3: Update `main()`**

```python
def main():
    settings = Settings.from_env()
    mcp = build_mcp(BankClient(settings), CrmClient(settings))
    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)
```

- [ ] **Step 4: Verify the tool is registered**

```bash
python -c "
from operations.config import Settings
from operations.bank_client import BankClient
from operations.crm_client import CrmClient
from operations.mcp_server import build_mcp
import asyncio

s = Settings.from_env({'SERVICE_CLIENT_SECRET': 'x', 'CRM_PROVISIONING_TOKEN': 'y'})
mcp = build_mcp(BankClient(s), CrmClient(s))
tools = asyncio.run(mcp.list_tools())
names = [t.name for t in tools]
assert 'execute_provision_crm_mandate' in names, names
print('ok:', names)
"
```

Expected: prints `ok: [...]` with `execute_provision_crm_mandate` in the list.

- [ ] **Step 5: Commit**

```bash
git add operations/mcp_server.py
git commit -m "feat(operations): execute_provision_crm_mandate lever"
```

---

### Task 4: Document the new environment variables

**Files:**
- Modify: `operations/.env.example` (or wherever this service's env template lives — check for one alongside `coo/`'s; if none exists for `operations/`, add the variables to the nearest existing compose/env file that already sets `SERVICE_CLIENT_SECRET` for this service)

- [ ] **Step 1: Add the three new variables**

```
# nano-bank-crm's POST /api/agent/provision — must match that repo's
# CO_PROVISIONING_TOKEN exactly.
CRM_PROVISIONING_TOKEN=dev-only-not-a-real-secret
CRM_BASE_URL=http://localhost:3000
CRM_TENANT_SLUG=acme
```

- [ ] **Step 2: Commit**

```bash
git add -A -- operations/
git commit -m "docs(operations): document the CRM provisioning env vars"
```

## Self-Review Notes

- **Spec coverage:** §6 (COO-owned provisioning) is this plan in full. The lever is idempotent (Task 2's client just relays the CRM's own idempotency; Task 3's docstring tells the COO's LLM this explicitly, so it calls freely rather than treating it as a one-shot action).
- **Dependency on the sibling plan:** Task 2's tests mock the HTTP boundary, so this plan does not need nano-bank-crm's `POST /api/agent/provision` to exist yet to be implemented and merged — but Task 4's manual smoke test (if run) does need it live. Sequence: land the CRM-side plan first if an end-to-end check matters before merging this one; otherwise the two can proceed in parallel against the documented contract.
- **Type consistency:** `CrmClient.ensure_mandate`'s return shape (`mandateId`/`contactId`/`created`) matches the CRM-side plan's Task 5 response exactly, camelCase preserved rather than converted to snake_case — this is relayed to the COO's LLM verbatim via `_stringify`, and it costs nothing to keep it identical to what the CRM actually returns rather than translating it.
