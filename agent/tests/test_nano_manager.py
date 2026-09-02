from agent import nano_manager as NM
from agent.config import Settings


class _T:
    def __init__(self, name): self.name = name


def test_agent_tools_excludes_execute_and_cancel():
    tools = [_T("get_accounts"), _T("propose_transfer"), _T("execute_action"),
             _T("cancel_action"), _T("recall")]
    kept = {t.name for t in NM.agent_tools(tools)}
    assert "execute_action" not in kept and "cancel_action" not in kept
    assert {"get_accounts", "propose_transfer", "recall"} <= kept


def test_manager_prompt_mentions_read_and_confirm():
    p = NM.MANAGER_PROMPT.lower()
    assert "confirm" in p and ("never fabricate" in p or "do not fabricate" in p)


def test_mcp_session_omits_crm_when_no_crm_token_is_given():
    settings = Settings.from_env({"MCP_URL": "http://nano.test/mcp"})
    client = NM._mcp_session(settings, "cust-1", "nano-tok")
    assert set(client.connections.keys()) == {"nano"}


def test_mcp_session_includes_crm_when_a_crm_token_is_given():
    settings = Settings.from_env({"MCP_URL": "http://nano.test/mcp", "CRM_BASE_URL": "http://crm.test"})
    client = NM._mcp_session(settings, "cust-1", "nano-tok", crm_token="crm-tok")
    assert set(client.connections.keys()) == {"nano", "crm"}
    crm_conn = client.connections["crm"]
    assert crm_conn["url"] == "http://crm.test/api/agent/mcp"
    assert crm_conn["headers"]["authorization"] == "Bearer crm-tok"


def test_agent_tools_admits_both_nano_and_crm_tool_names():
    tools = [_T("get_accounts"), _T("query_Contact"), _T("not_allowed")]
    names = {t.name for t in NM.agent_tools(tools)}
    assert names == {"get_accounts", "query_Contact"}
