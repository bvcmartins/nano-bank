import uuid
from langchain_core.messages import HumanMessage
from csuite.harness import assemble
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_ops_tools


def test_assembled_agent_runs_plan_then_tool_then_answers():
    # script: write a plan -> call float_position -> final answer
    model = FakeChatModel([
        {"tool": "write_plan", "args": {"steps": ["float", "answer"]}},
        {"tool": "float_position", "args": {}},
        {"text": "Total operational float is 700.00 CAD."},
    ])
    agent, log = assemble(model, fake_ops_tools(), "You are a test COO.",
                          SafeMemory(None))
    cfg = {"configurable": {"thread_id": f"t-{uuid.uuid4().hex[:6]}"},
           "recursion_limit": 20}
    out = agent.invoke({"messages": [HumanMessage("float?")], "plan": [],
                        "todos": [], "running_summary": "", "depth": 0}, config=cfg)
    assert out["plan"] == ["float", "answer"]          # plan tool wrote state
    assert "700.00" in out["messages"][-1].content     # tool figure surfaced


def test_memory_tools_present_and_safe_without_qdrant():
    agent, _ = assemble(FakeChatModel([{"text": "ok"}]), [], "p", SafeMemory(None))
    names = set(agent.harness_tool_names)
    assert {"write_plan", "write_todos", "recall_memory", "record_memory",
            "spawn_subagent"} <= names
