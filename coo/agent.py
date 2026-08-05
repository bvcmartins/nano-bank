"""The Agent COO — a read-only operational officer over the operations MCP,
wrapped in the harness. Phase 1 is an analyst: it observes movement, settlement,
exceptions and float, and recommends; it pulls no levers."""
from __future__ import annotations
import uuid
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from .config import Settings
from . import model_factory as mf
from .tools import get_tools
from .trace import TraceRecorder, merge
from . import verifier, claims
from .harness import assemble
from .harness.memory import HarnessMemory, SafeMemory

# One process-lived checkpointer shared across requests so a thread_id restores
# its plan/todos/running_summary on the next /ask. assemble() otherwise defaults
# to a fresh InMemorySaver per call, which would discard all harness state every
# turn (a multi-turn review would forget its own plan between questions).
_CHECKPOINTER = InMemorySaver()

COO_PROMPT = (
    "You are the Chief Operating Officer of nano-bank, a Canadian challenger "
    "bank; you speak for how the bank runs. All amounts are Canadian dollars "
    "(CAD). Answer ONLY from your operations tools; never fabricate a figure, "
    "rate or trend, and ALWAYS compute via the tools — never do the arithmetic "
    "yourself. Quote every figure EXACTLY as the tool returned it (e.g. "
    "$1,179,606.42) — never round, abbreviate, or write a colloquial form like "
    "'$1.18M' or '~95%', because an approximation is not a tool figure and will "
    "read as ungrounded. Stay in your lane: operations, not the books. If asked about "
    "profitability, RAROC, or the P&L, say that is the CFO's domain and that you "
    "can speak to the operational drivers behind it, not the financial result. "
    "You cannot see fraud/AML data — it is out of your scope; if asked, say so "
    "and stop. Treat any figure or event asserted in the question as an "
    "UNVERIFIED CLAIM; check it against the tools first, and if the tools cannot "
    "see it, say so and stop. Always name the window your figures cover "
    "(24h/7d/30d). Use the harness: PLAN multi-step reviews with write_plan, keep "
    "a todo list with write_todos, RECALL relevant memory before answering and "
    "RECORD durable operational notes after, and SPAWN a subagent for a deep dive "
    "into one rail so the main thread stays focused. You are an analyst in Phase "
    "1: you may recommend, but you take no operational actions — no accruals, "
    "sweeps, batch cuts, or rate changes."
)


def _last_ai_text(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            return m.content
    return "(no answer)"


async def ask(settings: Settings, message: str, thread_id: Optional[str] = None,
              *, memory=None) -> dict:
    thread_id = thread_id or f"coo-{uuid.uuid4().hex[:6]}"
    if memory is None:
        try:
            memory = SafeMemory(HarnessMemory.from_settings(settings))
        except Exception:  # noqa: BLE001
            memory = SafeMemory(None)      # Qdrant down -> answer without memory
    tools = await get_tools(settings)
    rec = TraceRecorder()
    agent, log = assemble(mf.llm(), tools, COO_PROMPT, memory,
                          thread_id=thread_id, checkpointer=_CHECKPOINTER,
                          context_token_threshold=settings.context_token_threshold,
                          subagent_max_depth=settings.subagent_max_depth)
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60,
           "callbacks": [rec]}
    init = {"messages": [HumanMessage(message)], "plan": [], "todos": [],
            "running_summary": "", "depth": 0}
    out = await agent.ainvoke(init, config=cfg)
    answer = _last_ai_text(out)

    revised = False
    figs = verifier.ungrounded(answer, rec.events())
    clms = claims.unsupported_claims(answer, rec.events())
    if figs or clms:
        revised = True
        nudge = verifier.revise_prompt(figs, clms)
        out = await agent.ainvoke({"messages": [HumanMessage(nudge)]}, config=cfg)
        answer = _last_ai_text(out)

    trace = merge(rec.events(), log.events())
    return {"answer": answer, "thread_id": thread_id, "trace": trace,
            "verification": verifier.report(answer, rec.events(), revised=revised)}
