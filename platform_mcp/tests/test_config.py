from platform_mcp.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.mcp_port == 8094
    assert s.kubeconfig_path == "/etc/platform/kubeconfig"
    assert ("kind-nano-bank", "nano-bank") in s.contexts
    assert ("kind-modern-core", "modern-core") in s.contexts
    # Only services that actually expose a /health endpoint are probed. The MCP
    # servers (operations-mcp, finance-mcp) are FastMCP apps with no /health
    # route (their up/down is covered by estate_health's k8s read), so they are
    # NOT health targets.
    labels = {lbl for lbl, _ in s.health_targets}
    assert labels == {"bank-api", "coo", "cfo"}
    assert s.timeout == 10.0


def test_env_override():
    s = Settings.from_env({
        "MCP_PORT": "9",
        "PLATFORM_CONTEXTS": "ctxA=a,ctxB=b",
        "HEALTH_TARGETS": "svc=http://svc:1/health",
        "REQUEST_TIMEOUT": "3.5",
    })
    assert s.mcp_port == 9
    assert s.contexts == [("ctxA", "a"), ("ctxB", "b")]
    assert s.health_targets == [("svc", "http://svc:1/health")]
    assert s.timeout == 3.5
