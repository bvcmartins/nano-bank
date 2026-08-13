"""Deterministic precondition + allow-list logic for the CTO's infra levers.
Pure: dict-in/bool-out, no IO. `platform_mcp` re-runs these against LIVE reads at
execute time, so the agent cannot argue past a false precondition."""
from __future__ import annotations


def is_allowed(allow_list, cluster: str, name: str) -> bool:
    return (cluster, name) in set(allow_list)


def restart_warranted(deployment: dict, pods: list[dict], threshold: int = 5) -> bool:
    """A restart is a valid recovery iff the deployment is crashlooping OR not
    fully ready right now."""
    name = deployment.get("name")
    cluster = deployment.get("cluster")
    for p in pods:
        if p.get("name", "").startswith(f"{name}-") and p.get("cluster") == cluster:
            for c in p.get("containers", []):
                if c.get("waiting_reason") == "CrashLoopBackOff":
                    return True
                if int(c.get("restart_count", 0)) > threshold:
                    return True
    return int(deployment.get("ready", 0)) < int(deployment.get("desired", 0))


def _is_stalled(deployment: dict) -> bool:
    for c in deployment.get("conditions", []):
        if c.get("type") == "Progressing" and c.get("reason") == "ProgressDeadlineExceeded":
            return True
    return False


def remediation_signal_present(deployments: list, pods: list, allow_list,
                               threshold: int = 5) -> bool:
    """True iff some allow-listed deployment is unhealthy right now (degraded,
    crashlooping, or stalled) — the observed failing platform signal that a
    kind='remediation' delegation requires. Same reads Phase B self-verifies on."""
    allowed = set(allow_list)
    for d in deployments:
        if (d.get("cluster"), d.get("name")) not in allowed:
            continue
        if _is_stalled(d):
            return True
        if restart_warranted(d, pods, threshold):
            return True
    return False


def rollback_warranted(deployment: dict, replicasets: list[dict]) -> tuple[bool, int | None]:
    """Roll back iff the rollout is stalled AND a prior ReplicaSet revision exists.
    Target = the second-highest revision owned by this deployment (the previous
    good one). Returns (True, target_revision) or (False, None)."""
    if not _is_stalled(deployment):
        return False, None
    name = deployment.get("name")
    cluster = deployment.get("cluster")
    revs = sorted(
        {int(rs["revision"]) for rs in replicasets
         if rs.get("owner_deployment") == name and rs.get("cluster") == cluster
         and rs.get("revision") is not None},
        reverse=True,
    )
    if len(revs) < 2:
        return False, None
    return True, revs[1]
