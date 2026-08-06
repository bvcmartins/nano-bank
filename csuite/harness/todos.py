"""Todo tool (TodoWrite-shaped): an ordered checklist with statuses, held in
HarnessState.todos and preserved across compaction. Agent-agnostic."""
from __future__ import annotations
from typing import Annotated
from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command

_STATUSES = {"pending", "in_progress", "done"}


def todo_tools() -> list:
    @tool
    def write_todos(todos: list[dict],
                    tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Record/replace the working checklist. Each item is
        {"content": str, "status": "pending"|"in_progress"|"done"}."""
        cleaned = []
        for t in todos:
            status = t.get("status", "pending")
            if status not in _STATUSES:
                raise ValueError(f"bad todo status: {status!r}")
            cleaned.append({"content": t["content"], "status": status})
        done = sum(1 for t in cleaned if t["status"] == "done")
        return Command(update={
            "todos": cleaned,
            "messages": [ToolMessage(f"Todos updated ({done}/{len(cleaned)} done).",
                                     tool_call_id=tool_call_id)],
        })

    return [write_todos]
