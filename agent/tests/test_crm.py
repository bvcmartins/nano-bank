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
