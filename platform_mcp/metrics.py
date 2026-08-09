"""Pure platform-metric aggregations over the k8s reads and /health probes. No
IO — every function is dict-in/dict-out and unit-testable. Point-in-time (no
windows): the platform reads are snapshots."""
from __future__ import annotations
from decimal import Decimal


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def compute(operation: str, values) -> dict:
    """Deterministic arithmetic over numbers other tools already returned, so a
    derived figure stays tool-grounded. operation: mean|sum|ratio|percent|
    difference|product. Returns {operation, inputs, result} or {error, …}."""
    op = (operation or "").strip().lower()
    nums = [_dec(v) for v in (values or [])]
    two_ok = len(nums) >= 2 and nums[1] != 0
    if op in ("mean", "average", "avg"):
        result = (sum(nums) / len(nums)) if nums else None
    elif op == "sum":
        result = sum(nums) if nums else Decimal(0)
    elif op in ("ratio", "divide"):
        result = (nums[0] / nums[1]) if two_ok else None
    elif op in ("percent", "percentage", "share"):
        result = (nums[0] / nums[1] * 100) if two_ok else None
    elif op in ("difference", "subtract"):
        result = (nums[0] - sum(nums[1:])) if nums else None
    elif op in ("product", "multiply"):
        result = Decimal(1)
        for n in nums:
            result *= n
        if not nums:
            result = None
    else:
        return {"error": f"unknown operation '{operation}' "
                "(use mean|sum|ratio|percent|difference|product)"}
    if result is None:
        return {"error": "need valid operands — ratio/percent want two numbers "
                "with a non-zero denominator", "operation": op, "inputs": nums}
    places = Decimal("0.0001") if op in ("ratio", "divide") else Decimal("0.01")
    return {"operation": op, "inputs": nums, "result": result.quantize(places)}


def estate_health(deployments: list[dict]) -> dict:
    rows = []
    healthy = 0
    for d in deployments:
        ok = int(d.get("ready", 0)) >= int(d.get("desired", 0))
        healthy += 1 if ok else 0
        rows.append({
            "cluster": d.get("cluster"), "namespace": d.get("namespace"),
            "name": d.get("name"), "desired": int(d.get("desired", 0)),
            "ready": int(d.get("ready", 0)), "available": int(d.get("available", 0)),
            "updated": int(d.get("updated", 0)),
            "unavailable": int(d.get("unavailable", 0)), "healthy": ok,
        })
    total = len(rows)
    return {"deployments": rows,
            "rollup": {"total": total, "healthy": healthy,
                       "degraded": total - healthy}}


def restarts(pods: list[dict], threshold: int = 5) -> dict:
    rows = []
    crashlooping = []
    total = 0
    for p in pods:
        pod_restarts = 0
        pod_loop = False
        for c in p.get("containers", []):
            rc = int(c.get("restart_count", 0))
            pod_restarts += rc
            reason = c.get("waiting_reason")
            looping = reason == "CrashLoopBackOff" or rc > threshold
            if looping:
                pod_loop = True
                crashlooping.append({
                    "cluster": p.get("cluster"), "namespace": p.get("namespace"),
                    "name": p.get("name"), "container": c.get("name"),
                    "reason": reason or f"restarts>{threshold}", "restarts": rc,
                })
        total += pod_restarts
        rows.append({"cluster": p.get("cluster"), "namespace": p.get("namespace"),
                     "name": p.get("name"), "restarts": pod_restarts,
                     "crashlooping": pod_loop})
    return {"pods": rows, "crashlooping": crashlooping, "total_restarts": total}


def service_health(probes: list[dict]) -> dict:
    healthy, unhealthy, failing = [], [], []
    for pr in probes:
        label = pr.get("service")
        if pr.get("ok"):
            healthy.append(label)
        else:
            unhealthy.append(label)
        for name, ok in (pr.get("checks") or {}).items():
            if not ok:
                failing.append({"service": label, "check": name})
    return {"services": list(probes), "healthy": healthy, "unhealthy": unhealthy,
            "failing_checks": failing}
