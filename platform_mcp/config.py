from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


_DEFAULT_CONTEXTS = [("kind-nano-bank", "nano-bank"),
                     ("kind-modern-core", "modern-core")]
# Only services that actually expose an HTTP /health endpoint. The MCP servers
# (operations-mcp, finance-mcp) are FastMCP apps with no /health route — probing
# them would always 404, so they are NOT targets; their liveness is covered by
# estate_health's k8s read instead.
_DEFAULT_HEALTH = [
    ("bank-api", "http://bank-api:8081/health"),
    ("coo", "http://coo:8093/health"),
    ("cfo", "http://cfo:8089/health"),
]

# Phase B — infra levers. The write-scoped actor context per cluster (distinct
# from the read-only `platform-reader` contexts), and the (cluster, deployment)
# allow-list the CTO may act on: stateless app deployments only. Excludes all
# stateful (postgres, *-db, agent-qdrant), system (coredns, provisioners), and
# own-stack (platform-mcp, cto). RBAC resourceNames enforce the same set.
_DEFAULT_ACTOR_CONTEXTS = [("kind-nano-bank-actor", "nano-bank"),
                           ("kind-modern-core-actor", "modern-core")]
_DEFAULT_ALLOW_LIST = [
    ("nano-bank", "bank-api"), ("nano-bank", "coo"), ("nano-bank", "cfo"),
    ("nano-bank", "operations-mcp"), ("nano-bank", "finance-mcp"),
    ("modern-core", "modern-core"),
]


def _pairs(raw: str, sep: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        k, _, v = item.partition(sep)
        out.append((k.strip(), v.strip()))
    return out


@dataclass
class Settings:
    mcp_port: int
    kubeconfig_path: str
    contexts: list[tuple[str, str]]
    health_targets: list[tuple[str, str]]
    timeout: float
    actor_contexts: list[tuple[str, str]]
    allow_list: list[tuple[str, str]]
    bank_api: str
    service_client_secret: str
    restart_threshold: int
    coder_url: str
    coder_timeout: float
    coder_sandbox_repo: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env
        ctx_raw = e.get("PLATFORM_CONTEXTS")
        ht_raw = e.get("HEALTH_TARGETS")
        ac_raw = e.get("PLATFORM_ACTOR_CONTEXTS")
        al_raw = e.get("ALLOW_LIST")
        return cls(
            mcp_port=int(e.get("MCP_PORT", "8094")),
            kubeconfig_path=e.get("KUBECONFIG_PATH", "/etc/platform/kubeconfig"),
            contexts=_pairs(ctx_raw, "=") if ctx_raw else list(_DEFAULT_CONTEXTS),
            health_targets=_pairs(ht_raw, "=") if ht_raw else list(_DEFAULT_HEALTH),
            timeout=float(e.get("REQUEST_TIMEOUT", "10.0")),
            actor_contexts=_pairs(ac_raw, "=") if ac_raw else list(_DEFAULT_ACTOR_CONTEXTS),
            allow_list=_pairs(al_raw, "/") if al_raw else list(_DEFAULT_ALLOW_LIST),
            bank_api=e.get("NANO_BANK_API", "http://bank-api:8081"),
            service_client_secret=e.get("SERVICE_CLIENT_SECRET", ""),
            restart_threshold=int(e.get("RESTART_THRESHOLD", "5")),
            coder_url=e.get("CODER_URL", "http://coder:8096"),
            coder_timeout=float(e.get("CODER_TIMEOUT", "900")),
            coder_sandbox_repo=e.get("CODER_SANDBOX_REPO", "bvcmartins/cto-sandbox"),
        )
