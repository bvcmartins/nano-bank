from agent import nano_manager as NM


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
