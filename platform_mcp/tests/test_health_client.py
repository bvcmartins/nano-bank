import httpx
from platform_mcp.config import Settings
from platform_mcp.health_client import HealthClient


def _settings():
    return Settings.from_env({
        "HEALTH_TARGETS": "bank-api=http://bank-api/health,coo=http://coo/health"})


def _handler(request):
    if request.url.host == "bank-api":
        return httpx.Response(200, json={"status": "ok",
                                         "checks": {"db": True, "core": True}})
    raise httpx.ConnectError("refused")


def test_probe_reports_ok_and_unreachable():
    transport = httpx.MockTransport(_handler)
    out = HealthClient(_settings(), transport=transport).probe()
    by = {p["service"]: p for p in out}
    assert by["bank-api"]["ok"] is True
    assert by["bank-api"]["checks"] == {"db": True, "core": True}
    assert by["coo"]["ok"] is False
    assert by["coo"]["status"] == "unreachable"
    assert "refused" in by["coo"]["error"]


def test_non_ok_status_is_not_ok():
    def handler(request):
        return httpx.Response(200, json={"status": "degraded",
                                         "checks": {"ollama": False}})
    out = HealthClient(_settings(), transport=httpx.MockTransport(handler)).probe()
    assert all(p["ok"] is False for p in out)
    assert out[0]["checks"] == {"ollama": False}


def test_healthy_status_and_string_service_checks():
    # bank-api style: status "healthy" (not "ok"), sub-probes under `services`
    # with STRING values — normalize to booleans and treat "healthy" as ok.
    def handler(request):
        return httpx.Response(200, json={
            "status": "healthy",
            "services": {"api": "healthy", "database": "error"}})
    out = HealthClient(_settings(), transport=httpx.MockTransport(handler)).probe()
    assert out[0]["ok"] is True
    assert out[0]["checks"] == {"api": True, "database": False}
