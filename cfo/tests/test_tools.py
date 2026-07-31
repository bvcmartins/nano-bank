from cfo.config import Settings
from cfo import tools


def test_mcp_client_targets_finance_mcp():
    s = Settings.from_env({"FINANCE_MCP_URL": "http://finance-mcp:8088/mcp"})
    client = tools.mcp_client(s)
    conns = client.connections
    assert conns["finance"]["url"] == "http://finance-mcp:8088/mcp"
    assert conns["finance"]["transport"] == "streamable_http"
    # bank-wide: no per-customer headers
    assert "headers" not in conns["finance"] or conns["finance"]["headers"] == {}


def test_get_tools_filters_the_mutating_close_period_tool():
    """The CFO is read-only. The finance MCP server exposes close_period for
    operator/cron use, but an LLM that holds a tool can call it — an irreversible
    snapshot overwrite. get_tools drops it at the agent boundary, so the agent
    never sees it, rather than relying on a line in the prompt."""
    import asyncio

    class _T:
        def __init__(self, name):
            self.name = name

    class _FakeClient:
        async def get_tools(self):
            return [_T("balance_sheet"), _T("close_period"), _T("raroc")]

    s = Settings.from_env({"FINANCE_MCP_URL": "http://finance-mcp:8088/mcp"})
    orig = tools.mcp_client
    tools.mcp_client = lambda _s: _FakeClient()
    try:
        names = {t.name for t in asyncio.run(tools.get_tools(s))}
    finally:
        tools.mcp_client = orig
    assert "close_period" not in names
    assert {"balance_sheet", "raroc"} <= names


def test_trace_recorder_records_tool_events():
    from cfo.trace import TraceRecorder
    rec = TraceRecorder()
    rec.on_tool_start({"name": "raroc"}, "2026-07", run_id="r1")
    rec.on_tool_end("{...}", run_id="r1")
    ev = rec.events()
    assert ev and ev[0]["name"] == "raroc" and ev[0]["ok"] is True
