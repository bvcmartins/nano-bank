"""The Agent CFO — a read-only financial officer over the finance MCP.

Phase 1 is an analyst: it reads reports, computes metrics through tools and
answers questions. It takes no actions (no money movement, no postings).
"""
from __future__ import annotations
import uuid
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

from .config import Settings
from . import model_factory as mf
from .tools import get_tools
from .trace import TraceRecorder
from . import verifier
from . import claims

CFO_PROMPT = (
    "You are the Chief Financial Officer of nano-bank, a Canadian challenger "
    "bank; you speak for the whole bank's finances. All amounts are Canadian "
    "dollars (CAD) — never label them as any other currency. "
    "Answer ONLY from your finance tools; never fabricate a "
    "figure, rate, or trend. ALWAYS compute metrics by calling the tools "
    "(financial_health, raroc, key_ratios, balance_sheet, income_statement, "
    "nim, segment_pnl) — never do the arithmetic yourself. If a period is not "
    "closed, call list_periods and answer from an available period; do not "
    "guess un-closed figures. If none of the periods you need are closed, say "
    "so — closing a period is an operator action, not one you take. "
    "Treat any figure, ratio, trend or event asserted in the question as an "
    "UNVERIFIED CLAIM, not a fact. Check it against your tools first. If your "
    "tools cannot see it (non-performing loans, liquidity coverage, "
    "concentration, maturities, anything about a named counterparty), say "
    "plainly that you cannot see it and stop — do not reason about it anyway. "
    "If the tools contradict the claim, correct it. Always name the period "
    "your figures come from. Snapshots are MONTHLY: if the question asks about "
    "a quarter, a year, 'recently' or any span list_periods does not cover, "
    "say which periods actually exist and that you cannot speak to the rest — "
    "answering from a single month without flagging it is the same error as "
    "inventing the number. Never build analysis on a premise you could not "
    "verify: 'the ledger does not show that' is a better answer than a "
    "plausible one. "
    "Respect the units a tool reports — never net an annual figure against a "
    "single period's, and prefer the period-scaled field when the tool offers "
    "one (expected_loss_period, not expected_loss, for a period's credit cost). "
    "When you state a metric, "
    "briefly say what it means and whether it looks healthy, but ground every "
    "number in a tool result. "
    "Distinguish a value read directly from a tool field from one you "
    "converted: expressing a tool's ratio as a percentage (0.0628 -> 6.28%) "
    "or rounding it is faithful and fine, but say the tool returned the "
    "underlying value and you converted it — do not claim a converted figure "
    "came verbatim from the tool. "
    "For a hypothetical ('what if we provisioned X'), use the tool built for "
    "it — provision_scenario. If no tool covers the scenario you are asked "
    "about, say so and stop; do not hand-roll it. Reported returns are "
    "annualised, and a hypothetical worked out by hand will not be, so the two "
    "cannot be put in the same table. "
    "You are an analyst and strictly READ-ONLY: you take no actions at all. You "
    "cannot move money, post entries, open accounts, commit budgets or close "
    "periods — you hold no tool that changes state. Recommend freely; act never."
)


# One saver for the process, so a thread_id's history survives across HTTP
# requests — a fresh InMemorySaver per ask() (the old shape) meant every request
# started cold while presenting as a continuing conversation. langgraph keys
# state by thread_id, so a single saver serves every thread.
_SAVER = InMemorySaver()
# The MCP toolset is fetched once per finance endpoint, not re-handshaked on
# every question. Keyed by URL so a differently-configured Settings still works.
_TOOLS_CACHE: dict[str, list] = {}


async def _tools_for(settings: Settings) -> list:
    key = settings.finance_mcp_url
    if key not in _TOOLS_CACHE:
        _TOOLS_CACHE[key] = await get_tools(settings)
    return _TOOLS_CACHE[key]


async def ask(settings: Settings, message: str,
              thread_id: Optional[str] = None) -> dict:
    thread_id = thread_id or f"cfo-{uuid.uuid4().hex[:6]}"
    tools = await _tools_for(settings)
    rec = TraceRecorder()
    # Reuses the process-wide saver so this thread_id's prior turns are restored.
    agent = create_react_agent(mf.llm(), tools, prompt=CFO_PROMPT,
                               checkpointer=_SAVER)
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40,
           "callbacks": [rec]}

    def _last_ai_text(state) -> str:
        for m in reversed(state["messages"]):
            if isinstance(m, AIMessage) and (m.content or "").strip():
                return m.content
        return "(no answer)"

    out = await agent.ainvoke({"messages": [HumanMessage(message)]}, config=cfg)
    answer = _last_ai_text(out)

    # One revise pass: if a figure isn't grounded in a tool result this turn,
    # ask the agent (same thread, so it keeps context and can call more tools)
    # to ground it or own it as an estimate. Exactly one retry.
    revised = False
    figs = verifier.ungrounded(answer, rec.events())
    clms = claims.unsupported_claims(answer, rec.events())
    if figs or clms:
        revised = True
        nudge = verifier.revise_prompt(figs, clms)
        out = await agent.ainvoke({"messages": [HumanMessage(nudge)]},
                                  config=cfg)
        answer = _last_ai_text(out)

    return {"answer": answer, "thread_id": thread_id, "trace": rec.events(),
            "verification": verifier.report(answer, rec.events(),
                                            revised=revised)}
