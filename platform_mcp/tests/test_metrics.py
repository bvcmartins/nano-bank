from platform_mcp import metrics


def _dep(name, desired, ready, available=None, updated=None, unavailable=0,
         images=("app:1",), conditions=(), cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name, "desired": desired,
            "ready": ready, "available": available if available is not None else ready,
            "updated": updated if updated is not None else desired,
            "unavailable": unavailable, "images": list(images),
            "conditions": [dict(c) for c in conditions]}


def test_estate_health_flags_degraded():
    deps = [_dep("coo", 1, 1), _dep("bank-api", 2, 1, unavailable=1)]
    out = metrics.estate_health(deps)
    assert out["rollup"] == {"total": 2, "healthy": 1, "degraded": 1}
    by = {d["name"]: d for d in out["deployments"]}
    assert by["coo"]["healthy"] is True
    assert by["bank-api"]["healthy"] is False


def test_restarts_flags_crashloop_and_threshold():
    pods = [
        {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo-1",
         "phase": "Running", "containers": [
             {"name": "coo", "ready": True, "restart_count": 0, "waiting_reason": None}]},
        {"cluster": "nano-bank", "namespace": "nano-bank", "name": "cfo-1",
         "phase": "Running", "containers": [
             {"name": "cfo", "ready": False, "restart_count": 9,
              "waiting_reason": "CrashLoopBackOff"}]},
    ]
    out = metrics.restarts(pods, threshold=5)
    assert out["total_restarts"] == 9
    assert len(out["crashlooping"]) == 1
    cl = out["crashlooping"][0]
    assert cl["name"] == "cfo-1" and cl["reason"] == "CrashLoopBackOff"
    by = {p["name"]: p for p in out["pods"]}
    assert by["coo-1"]["crashlooping"] is False
    assert by["cfo-1"]["crashlooping"] is True


def test_service_health_splits_healthy_and_failing_checks():
    probes = [
        {"service": "bank-api", "ok": True, "status": "ok",
         "checks": {"db": True, "core": True}},
        {"service": "coo", "ok": False, "status": "degraded",
         "checks": {"ollama": True, "operations_mcp": False, "qdrant": True}},
    ]
    out = metrics.service_health(probes)
    assert out["healthy"] == ["bank-api"]
    assert out["unhealthy"] == ["coo"]
    assert {"service": "coo", "check": "operations_mcp"} in out["failing_checks"]


def test_compute_ratio_and_guard():
    from decimal import Decimal
    assert metrics.compute("ratio", [9, 3])["result"] == Decimal("3.0000")
    assert "error" in metrics.compute("ratio", [5])
