import pytest
from langchain_core.messages import ToolMessage
from csuite.harness.planning import planning_tools
from csuite.harness.todos import todo_tools


def _by_name(tools):
    return {t.name: t for t in tools}


def _call(tool, args, call_id):
    # A tool with an InjectedToolCallId arg must be invoked with a full ToolCall.
    return tool.invoke({"name": tool.name, "args": args, "id": call_id,
                        "type": "tool_call"})


def test_write_plan_updates_state_and_confirms():
    write_plan = _by_name(planning_tools())["write_plan"]
    cmd = _call(write_plan, {"steps": ["read float", "read rails", "summarize"]}, "c1")
    assert cmd.update["plan"] == ["read float", "read rails", "summarize"]
    msgs = cmd.update["messages"]
    assert isinstance(msgs[0], ToolMessage) and msgs[0].tool_call_id == "c1"


def test_update_plan_revises():
    update_plan = _by_name(planning_tools())["update_plan"]
    cmd = _call(update_plan, {"steps": ["a", "b"]}, "c9")
    assert cmd.update["plan"] == ["a", "b"]


def test_write_todos_validates_status():
    write_todos = _by_name(todo_tools())["write_todos"]
    cmd = _call(write_todos, {"todos": [{"content": "check interac", "status": "pending"}]}, "c2")
    assert cmd.update["todos"][0]["status"] == "pending"
    with pytest.raises(ValueError):
        _call(write_todos, {"todos": [{"content": "x", "status": "bogus"}]}, "c3")
