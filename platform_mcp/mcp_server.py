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


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def build_mcp(k8s, health) -> FastMCP:
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

    return mcp


def main():
    settings = Settings.from_env()
    k8s = K8sClient(settings)
    health = HealthClient(settings)
    mcp = build_mcp(k8s, health)
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
