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


def test_trace_recorder_records_tool_events():
    from csuite.trace import TraceRecorder
    rec = TraceRecorder()
    rec.on_tool_start({"name": "raroc"}, "2026-07", run_id="r1")
    rec.on_tool_end("{...}", run_id="r1")
    ev = rec.events()
    assert ev and ev[0]["name"] == "raroc" and ev[0]["ok"] is True
