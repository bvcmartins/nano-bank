from cfo.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.cfo_model == "glm-5.2"
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.finance_mcp_url == "http://localhost:8088/mcp"
    assert s.api_port == 8089
    assert s.console_port == 8506


def test_env_overrides():
    s = Settings.from_env({"CFO_MODEL": "glm-5.2-air", "API_PORT": "9000",
                           "FINANCE_MCP_URL": "http://finance-mcp:8088/mcp"})
    assert s.cfo_model == "glm-5.2-air"
    assert s.api_port == 9000
    assert s.finance_mcp_url == "http://finance-mcp:8088/mcp"
