# Personal-Manager CRM Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the personal manager read and log CRM data for its bound customer, using CRM tools it never has to hand-write — the CRM's own `/api/agent/mcp` endpoint already generates them from the mandate's scopes.

**Architecture:** A new `agent/crm.py` resolves a per-customer CRM token lazily (mandate lookup → COO directive on miss → token mint), the same three-step shape as the CRM-side plan's Task 5/6 split, but from the consuming side. `nano_manager.py`'s `_mcp_session` gains a second MCP server entry pointed at the CRM, alongside the existing "nano" one — no bespoke CRM tool code, because `query_Contact`/`get_Contact`/`query_Activity`/`get_Activity`/`create_Activity`/`update_Activity` already exist as soon as the mandate does (this is a correction to the design spec, which sketched three hand-written tools before this plan's author read `packages/policy/src/tools.ts` and found the CRM already generates them).

**Tech Stack:** Python, httpx, LangChain (`langchain_mcp_adapters`), pytest.

**Spec:** [nano-bank-crm's docs/specs/2026-08-29-personal-manager-agent-access-design.md](../../../../nano-bank-crm/docs/specs/2026-08-29-personal-manager-agent-access-design.md) §5 and §7 (§7's three bespoke tools are superseded by this plan — see Self-Review Notes). Depends on nano-bank-crm's `docs/plans/2026-08-30-personal-manager-agent-access-crm.md` (Tasks 5–6, the two REST endpoints this plan calls) and this repo's sibling `docs/superpowers/plans/2026-08-30-coo-crm-provisioning-lever.md` (the COO lever this plan directs).

## Global Constraints

- **No tool in `agent/mcp_server.py` (the personal manager's OWN MCP server) ever takes a customer id or a token as an argument** — every existing tool reads both from context, bound by trusted headers the LLM never sees (`agent/mcp_server.py`'s `bind()`/`BindMiddleware`). This plan does not touch that file at all; it reaches the CRM as a **second, separate** MCP server, authenticated the identical way (a header the LLM never constructs).
- Every new dataclass/client follows the existing shape exactly: `Settings.from_env(env=None)`, an injectable `httpx` transport for tests (`operations/bank_client.py`'s pattern, mirrored already in the CRM-side plan's `CrmClient`).
- Backward compatibility is a hard requirement, not a nice-to-have: every signature change in this plan adds an **optional, defaulted** parameter, so every existing test in `agent/tests/` keeps passing unmodified. Verified per-task below, not assumed.
- The personal manager never calls the CRM's provisioning endpoint (`POST /api/agent/provision`) — only `GET /api/agent/mandate` (read-only) and `POST /api/agent/token` (mint against an already-existing mandate). Provisioning is the COO's call; see the CRM-side plan's Task 5/6 split.

---

### Task 1: `Settings` gains the CRM and COO endpoints

**Files:**
- Modify: `agent/config.py`
- Test: `agent/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.crm_base_url`, `Settings.crm_tenant_slug`, `Settings.crm_agent_id`, `Settings.crm_agent_secret`, `Settings.crm_lookup_token`, `Settings.coo_base_url` — all consumed by Task 2.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_config.py — add this test
def test_from_env_reads_crm_and_coo_settings():
    s = Settings.from_env(
        {
            "CRM_BASE_URL": "http://crm.test",
            "CRM_TENANT_SLUG": "acme",
            "CRM_AGENT_ID": "agent-1",
            "CRM_AGENT_SECRET": "agent-secret",
            "CRM_LOOKUP_TOKEN": "lookup-token",
            "COO_BASE_URL": "http://coo.test",
        }
    )
    assert s.crm_base_url == "http://crm.test"
    assert s.crm_tenant_slug == "acme"
    assert s.crm_agent_id == "agent-1"
    assert s.crm_agent_secret == "agent-secret"
    assert s.crm_lookup_token == "lookup-token"
    assert s.coo_base_url == "http://coo.test"
```

Check `agent/tests/test_config.py`'s existing tests first (read the file) so this new test's `Settings.from_env({...})` call still supplies whatever other fields are currently required without defaults — merge into the existing minimal fixture dict rather than replacing it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_config.py -v`
Expected: FAIL — `Settings` has no `crm_base_url` field.

- [ ] **Step 3: Implement**

In `agent/config.py`, add fields to the dataclass and defaults to `from_env`:

```python
@dataclass
class Settings:
    # ... existing fields unchanged ...
    crm_base_url: str
    crm_tenant_slug: str
    crm_agent_id: str
    crm_agent_secret: str
    crm_lookup_token: str
    coo_base_url: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            # ... existing fields unchanged ...
            crm_base_url=g("CRM_BASE_URL", "http://localhost:3000"),
            crm_tenant_slug=g("CRM_TENANT_SLUG", "acme"),
            crm_agent_id=g("CRM_AGENT_ID"),
            crm_agent_secret=g("CRM_AGENT_SECRET"),
            crm_lookup_token=g("CRM_LOOKUP_TOKEN"),
            coo_base_url=g("COO_BASE_URL", "http://localhost:8093"),
        )
```

Deliberately **not** fail-loud here (unlike `operations/config.py`'s `CRM_PROVISIONING_TOKEN`): a personal manager instance with CRM credentials unset should still serve nano-bank data — CRM access degrades to "unavailable" per-customer (Task 2 surfaces this as a clear per-call error, not a startup crash), matching this repo's existing "degrade, don't break" instinct for the banking integration.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_config.py -v`
Expected: PASS, all prior tests in the file still green.

- [ ] **Step 5: Commit**

```bash
git add agent/config.py agent/tests/test_config.py
git commit -m "feat(agent): add CRM and COO settings"
```

---

### Task 2: `agent/crm.py` — client, token resolver, tool allowlist

**Files:**
- Create: `agent/crm.py`
- Test: `agent/tests/test_crm.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - `CrmClient.lookup_mandate(customer_id: str) -> Optional[str]`
  - `CrmClient.issue_token(mandate_id: str) -> dict` (`{"token": str, "expiresAt": str}`)
  - `CRM_LLM_TOOL_NAMES: frozenset[str]` — the exact tool names `packages/policy/src/tools.ts` generates for this mandate's fixed scopes (`read:Contact.*`, `read:Activity.*`, `write:Activity.*`), consumed by Task 3's `agent_tools()`.
  - `CrmTokenResolver.resolve(customer_id: str) -> Optional[str]` (async) — consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

```python
# agent/tests/test_crm.py
import time
import httpx
import pytest

from agent.config import Settings
from agent.crm import CrmClient, CrmTokenResolver, CRM_LLM_TOOL_NAMES


def _settings():
    return Settings.from_env(
        {
            "CRM_BASE_URL": "http://crm.test",
            "CRM_TENANT_SLUG": "acme",
            "CRM_AGENT_ID": "agent-1",
            "CRM_AGENT_SECRET": "agent-secret",
            "CRM_LOOKUP_TOKEN": "lookup-token",
            "COO_BASE_URL": "http://coo.test",
        }
    )


def test_crm_llm_tool_names_matches_the_mandate_s_fixed_scopes():
    # read:Contact.* + read:Activity.* + write:Activity.* — see nano-bank-crm's
    # packages/policy/src/tools.ts and packages/policy/src/provisioning.ts.
    assert CRM_LLM_TOOL_NAMES == frozenset(
        {"query_Contact", "get_Contact", "query_Activity", "get_Activity",
         "create_Activity", "update_Activity", "get_approval"}
    )


def test_lookup_mandate_returns_none_when_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agent/mandate"
        assert request.url.params["tenantSlug"] == "acme"
        assert request.url.params["customerId"] == "cust-1"
        assert request.headers["authorization"] == "Bearer lookup-token"
        return httpx.Response(200, json={"mandateId": None})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    assert client.lookup_mandate("cust-1") is None


def test_issue_token_posts_the_standing_agent_secret():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"token": "tok", "expiresAt": "2026-08-30T00:05:00.000Z"})

    client = CrmClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.issue_token("mandate-1")

    assert seen["body"] == {
        "tenantSlug": "acme",
        "agentId": "agent-1",
        "secret": "agent-secret",
        "mandateId": "mandate-1",
    }
    assert result["token"] == "tok"


@pytest.mark.anyio
async def test_resolver_provisions_via_the_coo_on_first_use():
    calls = {"lookup": 0, "ask_coo": 0}
    mandate_created = {"done": False}

    class FakeCrm(CrmClient):
        def __init__(self):
            pass

        def lookup_mandate(self, customer_id: str):
            calls["lookup"] += 1
            return "mandate-1" if mandate_created["done"] else None

        def issue_token(self, mandate_id: str):
            return {"token": f"tok-for-{mandate_id}", "expiresAt": "2099-01-01T00:00:00.000Z"}

    async def fake_ask_coo(message: str) -> None:
        calls["ask_coo"] += 1
        assert "cust-1" in message
        assert "Dana Lin" in message
        mandate_created["done"] = True

    def fake_profile_lookup(customer_id: str) -> str:
        return "Dana Lin"

    resolver = CrmTokenResolver(_settings(), FakeCrm(), fake_ask_coo, fake_profile_lookup)
    token = await resolver.resolve("cust-1")

    assert token == "tok-for-mandate-1"
    assert calls["ask_coo"] == 1
    assert calls["lookup"] == 2  # once before asking (miss), once after (confirm)


@pytest.mark.anyio
async def test_resolver_skips_the_coo_when_a_mandate_already_exists():
    class FakeCrm(CrmClient):
        def __init__(self):
            pass

        def lookup_mandate(self, customer_id: str):
            return "mandate-existing"

        def issue_token(self, mandate_id: str):
            return {"token": f"tok-for-{mandate_id}", "expiresAt": "2099-01-01T00:00:00.000Z"}

    async def fail_if_called(message: str) -> None:
        assert False, "should not have asked the COO"

    resolver = CrmTokenResolver(_settings(), FakeCrm(), fail_if_called, lambda cid: "unused")
    token = await resolver.resolve("cust-2")
    assert token == "tok-for-mandate-existing"


@pytest.mark.anyio
async def test_resolver_caches_and_does_not_re_lookup_within_the_ttl():
    class FakeCrm(CrmClient):
        def __init__(self):
            self.lookups = 0

        def lookup_mandate(self, customer_id: str):
            self.lookups += 1
            return "mandate-1"

        def issue_token(self, mandate_id: str):
            return {"token": "tok", "expiresAt": "2099-01-01T00:00:00.000Z"}

    crm = FakeCrm()
    resolver = CrmTokenResolver(_settings(), crm, None, lambda cid: "unused")
    await resolver.resolve("cust-3")
    await resolver.resolve("cust-3")
    assert crm.lookups == 1
```

Add `anyio` (or reuse whatever async-test plugin `agent/tests/conftest.py` already configures — check it first; if the suite already runs async tests via `pytest-asyncio` with `asyncio_mode = auto` or similar, use `async def test_...` directly without the `@pytest.mark.anyio` decorator and match that file's existing convention instead of introducing a second async test framework).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_crm.py -v`
Expected: FAIL — `agent.crm` does not exist.

- [ ] **Step 3: Implement**

```python
# agent/crm.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_crm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/crm.py agent/tests/test_crm.py
git commit -m "feat(agent): CrmClient and CrmTokenResolver"
```

---

### Task 3: Wire the CRM into `_mcp_session` and the tool allowlist

**Files:**
- Modify: `agent/nano_manager.py`
- Test: `agent/tests/test_nano_manager.py`

**Interfaces:**
- Consumes: `CRM_LLM_TOOL_NAMES` (Task 2).
- Produces: `_mcp_session(settings, customer_id, token, crm_token=None)` — the `crm_token` parameter is new and defaulted, so every existing call site keeps working unchanged (Global Constraints). `agent_tools(all_tools)` now filters against the union of nano and CRM tool names.

- [ ] **Step 1: Write the failing test**

Read `agent/tests/test_nano_manager.py` first to match its existing fixture/assertion style exactly (it was empty of `_mcp_session` assertions as of this plan's writing — confirm that is still true before assuming the shape below is additive rather than conflicting).

```python
# agent/tests/test_nano_manager.py — add these tests
from agent.nano_manager import _mcp_session, agent_tools
from agent.config import Settings


def test_mcp_session_omits_crm_when_no_crm_token_is_given():
    settings = Settings.from_env({"MCP_URL": "http://nano.test/mcp"})
    client = _mcp_session(settings, "cust-1", "nano-tok")
    assert set(client.connections.keys()) == {"nano"}


def test_mcp_session_includes_crm_when_a_crm_token_is_given():
    settings = Settings.from_env({"MCP_URL": "http://nano.test/mcp", "CRM_BASE_URL": "http://crm.test"})
    client = _mcp_session(settings, "cust-1", "nano-tok", crm_token="crm-tok")
    assert set(client.connections.keys()) == {"nano", "crm"}
    crm_conn = client.connections["crm"]
    assert crm_conn["url"] == "http://crm.test/api/agent/mcp"
    assert crm_conn["headers"]["authorization"] == "Bearer crm-tok"


def test_agent_tools_admits_both_nano_and_crm_tool_names():
    class FakeTool:
        def __init__(self, name):
            self.name = name

    tools = [FakeTool("get_accounts"), FakeTool("query_Contact"), FakeTool("not_allowed")]
    names = {t.name for t in agent_tools(tools)}
    assert names == {"get_accounts", "query_Contact"}
```

Check `MultiServerMCPClient`'s actual attribute name for its server map before trusting `client.connections` above — inspect the installed `langchain_mcp_adapters` package (`python -c "from langchain_mcp_adapters.client import MultiServerMCPClient; help(MultiServerMCPClient)"` or read its source under the venv's `site-packages`) and adjust the assertion to the real attribute if it differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_nano_manager.py -v`
Expected: FAIL — `_mcp_session` takes no `crm_token` keyword yet, and `agent_tools` rejects `query_Contact`.

- [ ] **Step 3: Implement**

In `agent/nano_manager.py`:

```python
from .crm import CRM_LLM_TOOL_NAMES

ALL_ALLOWED_TOOL_NAMES = LLM_TOOL_NAMES | CRM_LLM_TOOL_NAMES


def agent_tools(all_tools):
    return [t for t in all_tools if getattr(t, "name", None) in ALL_ALLOWED_TOOL_NAMES]


def _mcp_session(settings: Settings, customer_id: str, token: Optional[str], crm_token: Optional[str] = None):
    """Per-request MCP client bound to a customer via trusted headers. The CRM
    server is included only when a CRM token is actually available — a
    customer with no CRM access yet (or CRM_BASE_URL left unconfigured, per
    Settings.from_env's fail-soft default there) simply gets no CRM tools this
    turn, same shape as nano's own optional X-Nano-Token."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    servers = {
        "nano": {
            "url": settings.mcp_url,
            "transport": "streamable_http",
            "headers": {"X-Nano-Customer": customer_id, **({"X-Nano-Token": token} if token else {})},
        }
    }
    if crm_token:
        servers["crm"] = {
            "url": settings.crm_base_url.rstrip("/") + "/api/agent/mcp",
            "transport": "streamable_http",
            "headers": {"authorization": f"Bearer {crm_token}"},
        }
    return MultiServerMCPClient(servers)
```

Import `LLM_TOOL_NAMES` — check the existing import line at the top of `nano_manager.py` (it is already imported for the current `agent_tools`, since that function already references it; only the new union constant and the `CRM_LLM_TOOL_NAMES` import are additions).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_nano_manager.py -v`
Expected: PASS, all prior tests in the file still green — this confirms the "omits CRM when no token" branch keeps every existing caller's behavior byte-for-byte identical.

- [ ] **Step 5: Commit**

```bash
git add agent/nano_manager.py agent/tests/test_nano_manager.py
git commit -m "feat(agent): wire the CRM MCP server into _mcp_session"
```

---

### Task 4: Thread the CRM token through `assist()` and the API routes

**Files:**
- Modify: `agent/nano_manager.py` (`assist()`)
- Modify: `agent/api.py` (`create_app`, its route handlers, `_default_confirm`)
- Modify: `agent/api_main.py` (wire a real `CrmTokenResolver`)
- Test: `agent/tests/test_api.py`

**Interfaces:**
- Consumes: `CrmTokenResolver` (Task 2), the updated `_mcp_session`/`agent_tools` (Task 3).
- Produces: `assist(settings, customer_id, token, message, thread_id=None, crm_token=None)`; `create_app(..., crm_resolver: Optional[CrmTokenResolver] = None)`. Both additions are optional and defaulted — every existing caller keeps working (Global Constraints).

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_api.py — add this test; adapt the app-construction fixture
# to match this file's existing helper (read the file first — there is already
# a TestClient-building helper near the top, per the create_app(...) call
# found during this plan's research).
def test_message_route_passes_the_resolved_crm_token_to_assist():
    seen = {}

    async def fake_assist(settings, cid, token, message, thread_id=None, crm_token=None):
        seen["crm_token"] = crm_token
        return {"answer": "ok", "thread_id": "t1"}

    class NanoResolver:
        def resolve(self, cid):
            return "nano-tok"

    class CrmResolver:
        async def resolve(self, cid):
            return "crm-tok"

    settings = _settings()  # however this file's existing tests build one
    app = create_app(
        settings,
        assist_fn=fake_assist,
        token_resolver=NanoResolver(),
        crm_resolver=CrmResolver(),
    )
    client = TestClient(app)
    resp = client.post(
        "/branch/clients/cust-1/message",
        json={"message": "hi"},
        headers={"authorization": f"Bearer {settings.branch_service_token}"},
    )
    assert resp.status_code == 200
    assert seen["crm_token"] == "crm-tok"


def test_message_route_works_with_no_crm_resolver_configured():
    # Backward compatibility: omitting crm_resolver entirely must not break the
    # existing nano-only path.
    async def fake_assist(settings, cid, token, message, thread_id=None, crm_token=None):
        assert crm_token is None
        return {"answer": "ok", "thread_id": "t1"}

    settings = _settings()
    app = create_app(settings, assist_fn=fake_assist, token_resolver=None)
    client = TestClient(app)
    resp = client.post(
        "/branch/clients/cust-1/message",
        json={"message": "hi"},
        headers={"authorization": f"Bearer {settings.branch_service_token}"},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest agent/tests/test_api.py -v`
Expected: FAIL — `create_app` has no `crm_resolver` parameter; `fake_assist`'s `crm_token` kwarg is never populated.

- [ ] **Step 3: Implement**

In `agent/nano_manager.py`, `assist()` gains one parameter, threaded straight into `_mcp_session`:

```python
async def assist(settings: Settings, customer_id: str, token: Optional[str],
                 message: str, thread_id: Optional[str] = None,
                 crm_token: Optional[str] = None) -> dict:
    thread_id = thread_id or f"{customer_id}-{uuid.uuid4().hex[:6]}"
    client = _mcp_session(settings, customer_id, token, crm_token)
    # ... unchanged from here ...
```

In `agent/api.py`:

```python
from typing import Optional, Protocol

from .crm import CrmTokenResolver  # new import
```

```python
def create_app(settings: Settings, *, assist_fn=nano_manager.assist,
               confirm_fn=_default_confirm, token_resolver: Optional[TokenResolver] = None,
               crm_resolver: Optional[CrmTokenResolver] = None,
               seed_fn=None) -> FastAPI:
    app = FastAPI(title="nano-bank personal manager")

    def _auth(authorization: Optional[str]):
        expected = f"Bearer {settings.branch_service_token}"
        if not settings.branch_service_token or authorization != expected:
            raise HTTPException(401, "invalid service token")

    def _token(cid: str) -> Optional[str]:
        return token_resolver.resolve(cid) if token_resolver else None

    async def _crm_token(cid: str) -> Optional[str]:
        return await crm_resolver.resolve(cid) if crm_resolver else None

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/branch/clients/{cid}/profile")
    async def profile(cid: str, authorization: str = Header(None)):
        _auth(authorization)
        client = nano_manager._mcp_session(settings, cid, _token(cid), await _crm_token(cid))
        for t in await client.get_tools():
            if t.name == "get_profile":
                return await t.ainvoke({})
        raise HTTPException(500, "profile tool unavailable")

    @app.post("/branch/clients/{cid}/message")
    async def message(cid: str, body: MessageIn, authorization: str = Header(None)):
        _auth(authorization)
        return await assist_fn(settings, cid, _token(cid), body.message, body.thread_id, await _crm_token(cid))

    @app.post("/branch/clients/{cid}/actions/{aid}/confirm")
    async def confirm(cid: str, aid: str, authorization: str = Header(None)):
        _auth(authorization)
        return await confirm_fn(settings, cid, _token(cid), aid, cancel=False)

    @app.post("/branch/clients/{cid}/actions/{aid}/cancel")
    async def cancel(cid: str, aid: str, authorization: str = Header(None)):
        _auth(authorization)
        return await confirm_fn(settings, cid, _token(cid), aid, cancel=True)

    # ... seed route unchanged ...

    return app
```

`_default_confirm` is unchanged deliberately — `execute_action`/`cancel_action` are nano-bank-only tools (money movement), never CRM ones, so the confirm path has no CRM token to thread through.

In `agent/api_main.py`, wire the real resolver:

```python
from .crm import CrmClient, CrmTokenResolver
from . import crm as crmmod


def build() -> "tuple":
    settings = Settings.from_env()
    mf.init_models(settings)
    resolver = SeedTokenResolver(settings, creds={})

    def seed_fn():
        out = seedmod.seed_demo(BankClient(settings.nano_bank_api))
        resolver.creds.update(out["creds"])
        return {"customers": out["customers"]}

    db = ClientContext(settings.db)  # or however this process already gets one — check whether api_main.py already constructs one; if not, this line is new
    crm_client = CrmClient(settings)

    async def ask_coo(message: str) -> None:
        await crmmod._post_ask(settings.coo_base_url, message)

    crm_resolver = CrmTokenResolver(settings, crm_client, ask_coo, lambda cid: _profile_name(db, cid))

    app = create_app(settings, token_resolver=resolver, crm_resolver=crm_resolver, seed_fn=seed_fn)
    return settings, app


def _profile_name(db, customer_id: str) -> str:
    p = db.profile(customer_id) or {}
    first = p.get("first_name", "")
    last = p.get("last_name", "")
    name = f"{first} {last}".strip()
    return name or customer_id
```

Check whether `api_main.py` already imports and constructs a `ClientContext` anywhere (`agent/db.py`'s class, used by `agent/mcp_server.py`'s `build_deps`) — if the API process does not currently hold a DB connection at all (`api_main.py`'s existing `build()` only shows `resolver`/`seed_fn`/`app`), adding one here is new plumbing this task introduces; read `agent/db.py`'s `ClientContext.__init__` signature first to construct it correctly (it takes `settings.db`, per `agent/mcp_server.py`'s `build_deps`: `ClientContext(settings.db)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest agent/tests/test_api.py -v`
Expected: PASS, all prior tests in the file still green.

- [ ] **Step 5: Run the full offline suite**

Run: `python -m pytest agent -q`
Expected: PASS. This is the checkpoint that proves Task 3's backward-compatibility claim and Task 1's fail-soft claim actually hold across the whole module, not just the files touched directly.

- [ ] **Step 6: Commit**

```bash
git add agent/nano_manager.py agent/api.py agent/api_main.py agent/tests/test_api.py
git commit -m "feat(agent): thread the CRM token through assist() and the branch API"
```

---

### Task 5: Document the new environment variables

**Files:**
- Modify: `agent/.env.example`

- [ ] **Step 1: Add the six new variables**

```
# nano-bank-crm's own agent identity for this personal manager — see
# nano-bank-crm's packages/db/src/provision-personal-manager-agent.ts, run once
# per deployment. CRM_AGENT_SECRET is printed exactly once by that script.
CRM_BASE_URL=http://localhost:3000
CRM_TENANT_SLUG=acme
CRM_AGENT_ID=
CRM_AGENT_SECRET=
# Matches nano-bank-crm's PERSONAL_MANAGER_LOOKUP_TOKEN.
CRM_LOOKUP_TOKEN=dev-only-not-a-real-secret
# The COO's /ask endpoint — directed to provision CRM access on a cache miss.
COO_BASE_URL=http://localhost:8093
```

- [ ] **Step 2: Commit**

```bash
git add agent/.env.example
git commit -m "docs(agent): document the CRM and COO env vars"
```

## Self-Review Notes

- **Spec coverage:** §5 (lazy trigger, resolve-and-cache shape) → Task 2's `CrmTokenResolver`. §7 (new tool surface) is **superseded**, not implemented as spec'd — see the correction below. §8's exclusions hold: no Setup UI work, no notification channel, no ML.
- **Correction to the spec, found during planning:** §7 sketched three hand-written tools (`crm_get_contact`, `crm_log_interaction`, `crm_recent_interactions`) added to `agent/mcp_server.py`. Reading `packages/policy/src/tools.ts` in the CRM repo during this plan's research showed the CRM already generates `query_Contact`/`get_Contact`/`query_Activity`/`get_Activity`/`create_Activity`/`update_Activity` from the mandate's scopes — hand-writing equivalents would duplicate them under different names for no benefit, and would drift the moment the mandate's scopes changed. This plan reaches those tools directly, as a second MCP server, and leaves `agent/mcp_server.py` completely untouched.
- **Type consistency:** `_mcp_session`'s new `crm_token` parameter, `assist()`'s new `crm_token` parameter, and `create_app`'s new `crm_resolver` parameter are all optional with `None`/default-omitted semantics, checked explicitly in Task 3 Step 1 and Task 4 Step 1's "backward compatibility" tests — not assumed.
- **Open risk flagged, not silently resolved:** `CRM_LLM_TOOL_NAMES` (Task 2) is a hardcoded mirror of what `provisioning.ts` grants in the CRM repo. If a future change to that repo's fixed scope list isn't mirrored here, new CRM tools silently fail to reach the personal manager's LLM (filtered out, not erroring) rather than breaking loudly. Worth a follow-up (a startup-time `tools/list` cross-check logged as a warning on mismatch) but out of scope for this plan — flagging it here rather than either fixing it unasked or letting it pass unmentioned.
