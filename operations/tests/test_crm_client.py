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
