"""Behaviour tests for the CFO ask() loop.

These exercise what ask() *does* — the revise-once loop and its two trigger
channels (ungrounded figures, unsupported claims). The prompt's discipline (push
back on a fabricated NPL, name the period, route hypotheticals to a tool) is a
behaviour no unit test can assert without a live model, so it is covered by
`cfo/verify-cfo.sh` against a running stack rather than by asserting substrings
of CFO_PROMPT — which only pin the wording and fail on any rewording.
"""
import asyncio
from unittest.mock import patch
from langchain_core.messages import AIMessage

from cfo.config import Settings
from cfo import agent as cfo_agent


class _FakeAgent:
    async def ainvoke(self, state, config=None):
        return {"messages": state["messages"] +
                [AIMessage("RAROC is 18.3%, which is healthy.")]}


def test_ask_returns_answer_and_thread():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})

    async def _fake_get_tools(settings):
        return []

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=_FakeAgent()), \
         patch.object(cfo_agent.mf, "llm", return_value=object()):
        out = asyncio.run(cfo_agent.ask(s, "How healthy are we?", thread_id="t1"))
    assert out["thread_id"] == "t1"
    assert "RAROC" in out["answer"]
    assert isinstance(out["trace"], list)


class _TwoPassAgent:
    """Pass 1 returns an ungrounded figure; pass 2 (after the revise message)
    returns a clean, grounded answer. Records how many times it was invoked."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.calls == 1:
            text = "Net income $1,448.08, and an invented loss of -$7,652.00."
        else:
            text = "Corrected: net income $1,448.08 (my estimate: none)."
        return {"messages": state["messages"] + [AIMessage(text)]}


def test_ask_revises_once_when_a_figure_is_ungrounded():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _TwoPassAgent()

    async def _fake_get_tools(settings):
        return []

    # The grounded set comes from the trace; stub it so 1448.08 is grounded and
    # 7652 is not, regardless of what the fake agent "called".
    trace = [{"kind": "tool", "name": "income_statement",
              "output": "{'net_income': '1448.08'}"}]

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How did we do?", thread_id="t"))

    assert fake.calls == 2                       # revised exactly once
    assert out["verification"]["revised"] is True
    assert "$1,448.08" in out["answer"]


def test_ask_does_not_revise_when_all_grounded():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _TwoPassAgent()

    async def _fake_get_tools(settings):
        return []

    trace = [{"kind": "tool", "name": "income_statement",
              "output": "{'net_income': '1448.08'}"}]

    # Pass-1 answer here contains only grounded figures.
    async def _one_pass(state, config=None):
        fake.calls += 1
        return {"messages": state["messages"] +
                [AIMessage("Net income was $1,448.08.")]}
    fake.ainvoke = _one_pass

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How did we do?", thread_id="t"))

    assert fake.calls == 1                       # no revision
    assert out["verification"]["revised"] is False
    assert out["verification"]["ungrounded"] == []


class _BadPeriodThenClean:
    """Pass 1 makes a false period-availability claim (no bad number); pass 2
    is clean. Exercises revision driven by the claim channel alone."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.calls == 1:
            text = "NIM for 2026-07 is fine, but 2026-07 may need to be closed first."
        else:
            text = "NIM for 2026-07 is fine; the period is closed and available."
        return {"messages": state["messages"] + [AIMessage(text)]}


def test_ask_revises_on_a_claim_with_no_bad_number():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _BadPeriodThenClean()

    async def _fake_get_tools(settings):
        return []

    # 2026-07 is grounded (list_periods returned it), so calling it
    # "may need to be closed" is a false claim.
    trace = [{"kind": "tool", "name": "list_periods", "input": "{}",
              "output": "['2026-06', '2026-07']"}]

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How's July?", thread_id="t"))

    assert fake.calls == 2                                # revised once
    assert out["verification"]["revised"] is True
    assert out["verification"]["unsupported_claims"] == []   # clean after
