from cto.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.cto_model == "kimi-k2.6"
    assert s.platform_mcp_url == "http://localhost:8094/mcp"
    assert s.memory_namespace == "cto"
    assert s.memory_collection == "cto_memory"
    assert s.api_port == 8095
    assert s.console_port == 8509
    assert s.subagent_max_depth == 2


def test_env_override():
    s = Settings.from_env({"CTO_MODEL": "kimi-k3", "API_PORT": "9999",
                           "PLATFORM_MCP_URL": "http://plat:1/mcp"})
    assert s.cto_model == "kimi-k3"
    assert s.api_port == 9999
    assert s.platform_mcp_url == "http://plat:1/mcp"
