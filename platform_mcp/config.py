from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


_DEFAULT_CONTEXTS = [("kind-nano-bank", "nano-bank"),
                     ("kind-modern-core", "modern-core")]
_DEFAULT_HEALTH = [
    ("bank-api", "http://bank-api:8081/health"),
    ("coo", "http://coo:8093/health"),
    ("cfo", "http://cfo:8089/health"),
    ("operations-mcp", "http://operations-mcp:8092/health"),
    ("finance-mcp", "http://finance-mcp:8088/health"),
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

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env
        ctx_raw = e.get("PLATFORM_CONTEXTS")
        ht_raw = e.get("HEALTH_TARGETS")
        return cls(
            mcp_port=int(e.get("MCP_PORT", "8094")),
            kubeconfig_path=e.get("KUBECONFIG_PATH", "/etc/platform/kubeconfig"),
            contexts=_pairs(ctx_raw, "=") if ctx_raw else list(_DEFAULT_CONTEXTS),
            health_targets=_pairs(ht_raw, "=") if ht_raw else list(_DEFAULT_HEALTH),
            timeout=float(e.get("REQUEST_TIMEOUT", "10.0")),
        )
