from __future__ import annotations
from langgraph.prebuilt.chat_agent_executor import AgentState


class HarnessState(AgentState):
    """create_react_agent state + harness fields. `messages`/`remaining_steps`
    come from AgentState; the rest are harness working memory that survives
    across turns (checkpointed) and across context compaction."""
    plan: list[str]
    todos: list[dict]          # {"content": str, "status": "pending|in_progress|done"}
    running_summary: str
    depth: int                 # subagent nesting depth; 0 at the top level
