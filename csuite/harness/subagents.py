"""Subagent spawning: run a fresh harnessed agent, with its OWN thread/context
and a scoped subset of tools, to completion — returning only a summary to the
parent. This is both the parallel-work mechanism and a context-control mechanism
(the child's tool chatter never enters the parent's context). A depth guard stops
runaway nesting. Agent-agnostic: `build_agent(tool_subset, depth)` is injected."""
from __future__ import annotations
import uuid
from typing import Annotated

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.prebuilt import InjectedState


def _last_text(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            return m.content
    return "(subagent produced no answer)"


def make_spawn_tool(*, build_agent, tools_by_name: dict, log, max_depth: int):
    @tool
    async def spawn_subagent(task: str, tools: list[str],
                             state: Annotated[dict, InjectedState]) -> str:
        """Delegate a focused deep-dive to a subagent with its own context and a
        subset of your tools (by name). Returns only the subagent's summary. Use
        it to keep the main thread focused (e.g. one rail at a time)."""
        depth = int(state.get("depth", 0))
        if depth >= max_depth:
            return (f"Refused: subagent depth limit ({max_depth}) reached; do "
                    "this inline instead.")
        subset = [tools_by_name[n] for n in tools if n in tools_by_name]
        agent = build_agent(subset, depth + 1)
        thread = f"sub-{uuid.uuid4().hex[:6]}"
        cfg = {"configurable": {"thread_id": thread}, "recursion_limit": 30}
        init = {"messages": [HumanMessage(task)], "plan": [], "todos": [],
                "running_summary": "", "depth": depth + 1}
        # await, not asyncio.run: the parent agent runs inside a live event loop
        # (the FastAPI /ask path is async), and asyncio.run() would raise
        # "cannot be called from a running event loop".
        out = await agent.ainvoke(init, config=cfg)
        summary = _last_text(out)
        log.add("subagent", task=task[:200], tools=list(tools),
                depth=depth + 1, thread=thread, chars=len(summary))
        return summary

    return spawn_subagent
