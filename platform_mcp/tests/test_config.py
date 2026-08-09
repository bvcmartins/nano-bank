from platform_mcp.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.mcp_port == 8094
    assert s.kubeconfig_path == "/etc/platform/kubeconfig"
    assert ("kind-nano-bank", "nano-bank") in s.contexts
    assert ("kind-modern-core", "modern-core") in s.contexts
    labels = {lbl for lbl, _ in s.health_targets}
    assert {"bank-api", "coo", "cfo", "operations-mcp", "finance-mcp"} <= labels
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
