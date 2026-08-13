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


def test_lever_settings_defaults():
    s = Settings.from_env({})
    assert ("kind-nano-bank-actor", "nano-bank") in s.actor_contexts
    assert ("kind-modern-core-actor", "modern-core") in s.actor_contexts
    assert ("nano-bank", "bank-api") in s.allow_list
    assert ("nano-bank", "coo") in s.allow_list
    assert ("modern-core", "modern-core") in s.allow_list
    # never stateful / own-stack in the default allow-list
    denied = {"postgres", "modern-core-db", "agent-qdrant", "platform-mcp", "cto"}
    assert not (denied & {d for _, d in s.allow_list})
    assert s.bank_api == "http://bank-api:8081"
    assert s.restart_threshold == 5


def test_lever_settings_override():
    s = Settings.from_env({
        "ALLOW_LIST": "nano-bank/coo,modern-core/modern-core",
        "PLATFORM_ACTOR_CONTEXTS": "ctxA-actor=a",
        "NANO_BANK_API": "http://x:1",
        "RESTART_THRESHOLD": "9",
    })
    assert s.allow_list == [("nano-bank", "coo"), ("modern-core", "modern-core")]
    assert s.actor_contexts == [("ctxA-actor", "a")]
    assert s.bank_api == "http://x:1"
    assert s.restart_threshold == 9


def test_coder_defaults():
    s = Settings.from_env({})
    assert s.coder_url == "http://coder:8096"
    assert s.coder_sandbox_repo == "bvcmartins/cto-sandbox"
