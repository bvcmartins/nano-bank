import httpx
import pytest
from platform_mcp.config import Settings
from platform_mcp.audit import LedgerAudit


def _settings():
    return Settings.from_env({"NANO_BANK_API": "http://bank",
                              "SERVICE_CLIENT_SECRET": "sekret"})


def _handler(record):
    def h(request):
        if request.url.path == "/api/v1/auth/service-token":
            record.append(("token", request.url.path))
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 900})
        if request.url.path == "/api/v1/agent-ledger/actions":
            record.append(("action", request.read().decode()))
            return httpx.Response(200, json={"seq": 7, "entry_hash": "abc"})
        return httpx.Response(404)
    return h


def test_post_action_mints_token_then_records():
    rec = []
    a = LedgerAudit(_settings(), transport=httpx.MockTransport(_handler(rec)))
    out = a.post_action("rollout_restart", {"deployment": "coo"}, {"outcome": "executed"})
    assert out == {"seq": 7, "entry_hash": "abc"}
    kinds = [k for k, _ in rec]
    assert kinds == ["token", "action"]


def test_post_action_raises_on_failure():
    def h(request):
        if request.url.path.endswith("service-token"):
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 900})
        return httpx.Response(500, json={"error": "boom"})
    a = LedgerAudit(_settings(), transport=httpx.MockTransport(h))
    with pytest.raises(Exception):
        a.post_action("x", {}, {})
