import json
import httpx
from operations.config import Settings
from operations.bank_client import BankClient


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


def test_mints_token_once_then_reuses():
    calls = {"token": 0, "float": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/service-token":
            calls["token"] += 1
            assert json.loads(request.content)["client_secret"] == "secret"
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 900})
        if request.url.path == "/api/v1/back-office/ops/float":
            calls["float"] += 1
            assert request.headers["authorization"] == "Bearer tok-123"
            return httpx.Response(200, json={"accounts": [], "total_float": "0"})
        return httpx.Response(404)

    client = BankClient(_settings(), transport=httpx.MockTransport(handler))
    client.float_()
    client.float_()
    assert calls["token"] == 1  # token minted once, cached
    assert calls["float"] == 2


def test_passes_window_query():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/service-token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 900})
        seen["path"] = request.url.path
        seen["window"] = request.url.params.get("window")
        return httpx.Response(200, json={"ok": True})

    client = BankClient(_settings(), transport=httpx.MockTransport(handler))
    client.rails("7d")
    assert seen["path"] == "/api/v1/back-office/ops/rails"
    assert seen["window"] == "7d"
