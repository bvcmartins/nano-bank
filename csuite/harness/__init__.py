"""assemble(): compose a harnessed create_react_agent from a model, the agent's
domain tools, its prompt, and its (Safe) memory. Nothing agent-specific here — the
COO, and later the CFO, both call this. Returns (agent, HarnessLog)."""
from __future__ import annotations
from typing import Annotated, Optional

from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

from .state import HarnessState
from .events import HarnessLog
from .planning import planning_tools
from .todos import todo_tools
from .context import make_context_hook
from .subagents import make_spawn_tool


def memory_tools(memory, log, *, thread_id: Optional[str] = None) -> list:
    @tool
    def recall_memory(query: str) -> list:
        """Recall durable operational notes relevant to a query (semantic search).
        Best-effort: returns [] if memory is unavailable."""
        return memory.recall(query, k=3)

    @tool
    def record_memory(note: str,
                      tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Persist a durable operational observation for future reviews."""
        memory.record(note, kind="observation", thread_id=thread_id)
        log.add("memory_write", chars=len(note))
        return Command(update={"messages": [
            ToolMessage("Recorded.", tool_call_id=tool_call_id)]})

    return [recall_memory, record_memory]


def assemble(model, domain_tools, prompt, memory, *, log=None, checkpointer=None,
             context_token_threshold: int = 60000, subagent_max_depth: int = 2,
             depth: int = 0, thread_id: Optional[str] = None):
    log = log or HarnessLog()
    tools = (list(domain_tools) + planning_tools() + todo_tools()
             + memory_tools(memory, log, thread_id=thread_id))
    tools_by_name = {t.name: t for t in tools}

    def build_agent(tool_subset, child_depth):
        sub, _ = assemble(model, tool_subset, prompt, memory, log=log,
                          checkpointer=InMemorySaver(),
                          context_token_threshold=context_token_threshold,
                          subagent_max_depth=subagent_max_depth, depth=child_depth)
        return sub

    if depth < subagent_max_depth:
        tools = tools + [make_spawn_tool(build_agent=build_agent,
                                         tools_by_name=tools_by_name, log=log,
                                         max_depth=subagent_max_depth)]

    hook = make_context_hook(threshold=context_token_threshold, summarizer=model,
                             memory=memory, log=log, thread_id=thread_id)
    agent = create_react_agent(model, tools, prompt=prompt,
                               state_schema=HarnessState, pre_model_hook=hook,
                               checkpointer=checkpointer or InMemorySaver())
    # Expose the assembled tool set on the agent instance (not a module global —
    # concurrent /ask calls and subagent spawns each build their own agent and
    # would race a shared list).
    agent.harness_tool_names = [t.name for t in tools]
    return agent, log
