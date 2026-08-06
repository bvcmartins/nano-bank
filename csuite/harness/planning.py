"""Planning tools: the agent lays out and revises the steps of a review. The
plan is graph state (HarnessState.plan), surfaced in the trace and preserved
across context compaction. Agent-agnostic."""
from __future__ import annotations
from typing import Annotated
from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command


def _set_plan(steps: list[str], tool_call_id: str, verb: str) -> Command:
    return Command(update={
        "plan": list(steps),
        "messages": [ToolMessage(f"Plan {verb} ({len(steps)} steps).",
                                 tool_call_id=tool_call_id)],
    })


def planning_tools() -> list:
    @tool
    def write_plan(steps: list[str],
                   tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Record an ordered plan for a multi-step review. Call this first on any
        non-trivial question; each step is a short phrase."""
        return _set_plan(steps, tool_call_id, "recorded")

    @tool
    def update_plan(steps: list[str],
                    tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Replace the current plan with a revised list of steps."""
        return _set_plan(steps, tool_call_id, "revised")

    return [write_plan, update_plan]
