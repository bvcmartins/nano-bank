import asyncio
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver
from csuite.harness.state import HarnessState
from csuite.harness.events import HarnessLog
from csuite.harness.subagents import make_spawn_tool
from csuite.tests.fakes import FakeChatModel, fake_ops_tools


def _build_agent(tool_subset, depth):
    model = FakeChatModel([{"text": "interac float is 700.00 (deep dive done)"}])
    return create_react_agent(model, tool_subset, state_schema=HarnessState,
                              checkpointer=InMemorySaver())


def test_spawn_runs_child_and_returns_summary():
    log = HarnessLog()
    tools = {t.name: t for t in fake_ops_tools()}
    spawn = make_spawn_tool(build_agent=_build_agent, tools_by_name=tools,
                            log=log, max_depth=2)
    out = asyncio.run(spawn.ainvoke({"task": "deep dive interac", "tools": list(tools),
                                     "state": {"depth": 0}}))
    assert "deep dive done" in out
    assert any(e["kind"] == "subagent" for e in log.events())


def test_depth_guard_refuses_at_max():
    log = HarnessLog()
    spawn = make_spawn_tool(build_agent=_build_agent, tools_by_name={},
                            log=log, max_depth=2)
    out = asyncio.run(spawn.ainvoke({"task": "x", "tools": [], "state": {"depth": 2}}))
    assert "depth" in out.lower()
    assert not any(e["kind"] == "subagent" for e in log.events())
