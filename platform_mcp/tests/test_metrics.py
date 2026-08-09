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


def _rs(name, owner, revision, desired=1, ready=1, cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name,
            "owner_deployment": owner, "revision": revision,
            "desired": desired, "ready": ready}


def test_rollouts_complete_progressing_stalled():
    deps = [
        _dep("coo", 1, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "True",
                          "reason": "NewReplicaSetAvailable"}]),
        _dep("cfo", 2, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "True",
                          "reason": "ReplicaSetUpdated"}]),
        _dep("bank-api", 2, 1, available=1, updated=1,
             conditions=[{"type": "Progressing", "status": "False",
                          "reason": "ProgressDeadlineExceeded"}]),
    ]
    rss = [_rs("coo-abc", "coo", 3), _rs("cfo-new", "cfo", 5), _rs("cfo-old", "cfo", 4)]
    out = metrics.rollouts(deps, rss)
    by = {d["name"]: d for d in out["deployments"]}
    assert by["coo"]["state"] == "complete"
    assert by["cfo"]["state"] == "progressing"
    assert by["cfo"]["active_replicasets"] == 2
    assert by["bank-api"]["state"] == "stalled"
    assert out["rollup"] == {"complete": 1, "progressing": 1, "stalled": 1}


def test_versions_flags_drift():
    deps = [
        _dep("coo", 1, 1, images=["nano-coo:dev"], cluster="nano-bank"),
        _dep("coo", 1, 1, images=["nano-coo:v2"], cluster="modern-core"),
        _dep("bank-api", 1, 1, images=["nano-bank:dev"]),
    ]
    out = metrics.versions(deps)
    assert out["by_app"]["nano-coo"]["drift"] is True
    assert out["by_app"]["nano-coo"]["tags"] == ["dev", "v2"]
    assert out["by_app"]["nano-bank"]["drift"] is False
    assert "nano-coo" in out["drift"]
    assert "nano-bank" not in out["drift"]


def test_platform_health_bundles_all_five():
    out = metrics.platform_health([], [], [], [])
    assert set(out) == {"estate_health", "restarts", "rollouts", "versions",
                        "service_health"}
