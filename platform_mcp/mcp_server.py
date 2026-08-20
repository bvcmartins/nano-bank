"""The platform MCP: the CTO's technical perception surface. Each tool reads the
kube estate (both clusters) and/or the services' /health, and returns a pure
metrics rollup. Decimals stringified for JSON transport. READ-ONLY — no tool
mutates anything (Phase A is analyst-only)."""
from __future__ import annotations
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .k8s_client import K8sClient
from .health_client import HealthClient
from . import metrics
from . import levers


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


# --- Autonomous infra levers (Phase B) ---------------------------------------
# Module-level so they are unit-testable without the FastMCP wrapper. Each is
# verify -> act -> audit: allow-list, then a LIVE re-read of k8s to re-check a
# deterministic precondition, then the write, then a LOUD audit (an audit failure
# after a successful act raises — the operator sees an un-audited action and
# reconciles). A refusal (not allow-listed, not found, precondition false) is
# also audited: the attempt is a fact.
def _find(deployments, cluster, name):
    for d in deployments:
        if d.get("cluster") == cluster and d.get("name") == name:
            return d
    return None


def _refused(reason):
    return {"outcome": "refused", "reason": reason}


def _executed(effect):
    return {"outcome": "executed", "effect": effect}


def _failed(reason):
    return {"outcome": "failed", "reason": reason}


_VALID_KINDS = ("remediation", "delivery")


def _do_delegate(k8s, coder, audit, settings, kind, task):
    """Delegate a coding task to the coder (opens a PR-gated PR against the sandbox).
    Structurally allow-listed: the coder service is pinned to the sandbox repo, so
    there is no repo to choose. remediation requires an observed failing signal.
    Every path is audited — the attempt is a fact."""
    params = {"kind": kind, "task": task}
    if kind not in _VALID_KINDS:
        outcome = _refused(f"unknown task kind {kind!r} (expected remediation|delivery)")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    if not (task or "").strip():
        outcome = _refused("empty task")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    if kind == "remediation" and not levers.remediation_signal_present(
            k8s.deployments(), k8s.pods(), settings.allow_list):
        outcome = _refused("no failing/degraded platform signal observed; "
                           "remediation is unwarranted")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    try:
        outcome = coder.code_task(kind, task)      # HTTP to the coder service
    except Exception as e:  # noqa: BLE001
        outcome = _failed(f"coder unreachable: {e}")
        audit.post_action("delegate_coding_task", params, outcome)
        return outcome
    audit.post_action("delegate_coding_task", params, outcome)
    return outcome


def _do_restart(k8s, writer, audit, settings, cluster, deployment):
    params = {"cluster": cluster, "deployment": deployment}
    if not levers.is_allowed(settings.allow_list, cluster, deployment):
        outcome = _refused(f"{cluster}/{deployment} is not in the CTO action allow-list")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    dep = _find(k8s.deployments(), cluster, deployment)   # LIVE re-read
    if dep is None:
        outcome = _refused(f"{cluster}/{deployment} not found")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    if not levers.restart_warranted(dep, k8s.pods(), settings.restart_threshold):
        outcome = _refused(f"{deployment} is not crashlooping or unready; restart unwarranted")
        audit.post_action("rollout_restart", params, outcome)
        return outcome
    effect = writer.rollout_restart(cluster, deployment)   # act
    outcome = _executed(effect)
    audit.post_action("rollout_restart", params, outcome)  # loud audit
    return outcome


def _do_rollback(k8s, writer, audit, settings, cluster, deployment):
    params = {"cluster": cluster, "deployment": deployment}
    if not levers.is_allowed(settings.allow_list, cluster, deployment):
        outcome = _refused(f"{cluster}/{deployment} is not in the CTO action allow-list")
        audit.post_action("rollback", params, outcome)
        return outcome
    dep = _find(k8s.deployments(), cluster, deployment)
    if dep is None:
        outcome = _refused(f"{cluster}/{deployment} not found")
        audit.post_action("rollback", params, outcome)
        return outcome
    ok, target = levers.rollback_warranted(dep, k8s.replicasets())
    if not ok:
        outcome = _refused(f"{deployment} rollout is not stalled with a prior revision")
        audit.post_action("rollback", params, outcome)
        return outcome
    effect = writer.rollback(cluster, deployment, target)
    outcome = _executed(effect)
    audit.post_action("rollback", params, outcome)
    return outcome


def build_mcp(k8s, health, writer=None, audit=None, settings=None, coder=None) -> FastMCP:
    mcp = FastMCP(
        "nano-platform",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def estate_health() -> dict:
        """Per-deployment desired/ready/available across BOTH clusters + a rollup
        (total/healthy/degraded, where degraded = ready < desired). Reliability."""
        return _stringify(metrics.estate_health(k8s.deployments()))

    @mcp.tool()
    def restarts() -> dict:
        """Per-pod restart totals + a crashlooping list (CrashLoopBackOff or
        restarts over threshold) + a total, across both clusters. Reliability."""
        return _stringify(metrics.restarts(k8s.pods()))

    @mcp.tool()
    def rollouts() -> dict:
        """Per-deployment rollout state (complete/progressing/stalled, updated vs
        desired) + a rollup. Delivery."""
        return _stringify(metrics.rollouts(k8s.deployments(), k8s.replicasets()))

    @mcp.tool()
    def versions() -> dict:
        """Per-app container image tag(s) across the estate; flags drift where the
        same app runs different tags in different places. Delivery."""
        return _stringify(metrics.versions(k8s.deployments()))

    @mcp.tool()
    def service_health() -> dict:
        """Each service's /health self-report (dependency probes) split into
        healthy/unhealthy + the failing dependency checks. Reliability."""
        return _stringify(metrics.service_health(health.probe()))

    @mcp.tool()
    def platform_health() -> dict:
        """One-shot bundle: estate_health, restarts, rollouts, versions and
        service_health — the whole technical picture in one call."""
        return _stringify(metrics.platform_health(
            k8s.deployments(), k8s.pods(), k8s.replicasets(), health.probe()))

    @mcp.tool()
    def compute(operation: str, values: list[float]) -> dict:
        """Deterministic arithmetic on numbers you already got from other tools,
        so a derived figure stays tool-grounded — use this instead of doing the
        math yourself. operation: mean|sum|ratio|percent|difference|product.
        values: the exact tool-returned numbers, in order (e.g. degraded share =
        percent, values=[degraded, total])."""
        return _stringify(metrics.compute(operation, values))

    # Acting tools are registered only when the write path is wired (writer +
    # audit + settings). Phase A's build_mcp(k8s, health) stays read-only.
    if writer is not None and audit is not None and settings is not None:
        @mcp.tool()
        def execute_rollout_restart(cluster: str, deployment: str) -> dict:
            """Restart a stateless app deployment's pods (rolling). REFUSED unless
            it is actually crashlooping/unready right now and on the CTO action
            allow-list. Autonomous + audited; report the outcome verbatim."""
            return _stringify(_do_restart(k8s, writer, audit, settings, cluster, deployment))

        @mcp.tool()
        def execute_rollback(cluster: str, deployment: str) -> dict:
            """Roll a stateless app deployment back to its prior revision. REFUSED
            unless its rollout is actually stalled with a prior revision and it is
            on the allow-list. Autonomous + audited; report the outcome verbatim."""
            return _stringify(_do_rollback(k8s, writer, audit, settings, cluster, deployment))

    # The delegation lever needs only coder + audit + settings (plus the k8s reads
    # build_mcp already has for the remediation precondition) — independent of the
    # k8s writer, so it registers on its own condition.
    if coder is not None and audit is not None and settings is not None:
        @mcp.tool()
        def delegate_coding_task(kind: str, task: str) -> dict:
            """Delegate a scoped coding task to the engineering coder, which opens a
            PR-gated pull request against the SANDBOX service repo (not the live
            platform). kind='remediation' — REFUSED unless a real failing/degraded
            platform signal is observed; the signal gates WHETHER you may delegate, but
            the coder still works only in the sandbox, so treat it as a sandbox change
            warranted by the incident, not a hot-fix to the failing system. Or
            kind='delivery' — a handed-down backlog task, no signal required. You do NOT
            write code yourself; you delegate it. A human reviews and MERGES the PR —
            never you. Autonomous + audited; report the outcome and the PR link verbatim."""
            return _stringify(_do_delegate(k8s, coder, audit, settings, kind, task))

    return mcp


def main():
    settings = Settings.from_env()
    k8s = K8sClient(settings)
    health = HealthClient(settings)
    from .k8s_writer import K8sWriter
    from .audit import LedgerAudit
    writer = K8sWriter(settings)
    audit = LedgerAudit(settings)
    from .coder_client import CoderClient
    coder = CoderClient(settings)
    mcp = build_mcp(k8s, health, writer=writer, audit=audit, settings=settings, coder=coder)
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
