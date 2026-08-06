import asyncio

from langchain_core.tools import tool

from cfo.config import Settings
from cfo import agent as cfo_agent
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel


def _settings():
    return Settings.from_env({"OLLAMA_API_KEY": "x"})


@tool
def income_statement(period: str = "2026-07") -> dict:
    """Canned income statement for the fake CFO tests."""
    return {"period": period, "net_income": "1448.08"}


def _patch(monkeypatch, model):
    monkeypatch.setattr(cfo_agent.mf, "llm", lambda **k: model)

    async def _tools(settings):
        return [income_statement]

    monkeypatch.setattr(cfo_agent, "get_tools", _tools)


# --- prompt discipline (the CFO's guardrails live in the prompt) -------------

def test_prompt_pins_discipline():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "chief financial officer" in p
    assert "never" in p and "tool" in p


def test_prompt_refuses_unverified_premises():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "unverified claim" in p
    assert "cannot see it" in p
    assert "list_periods does not cover" in p


def test_prompt_pins_units_discipline():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "expected_loss_period" in p
    assert "annual figure" in p


def test_prompt_requires_naming_the_period_and_its_limits():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "name the period" in p
    assert "monthly" in p


def test_prompt_routes_hypotheticals_to_a_tool():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "provision_scenario" in p
    assert "do not hand-roll" in p


def test_prompt_is_honest_about_close_period():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "close_period" in p
    assert "no financial actions" in p


def test_prompt_distinguishes_direct_from_converted_values():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "verbatim" in p
    assert "convert" in p
    assert "ratio" in p and "percent" in p


def test_prompt_uses_the_harness_and_compute():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "write_plan" in p and "spawn a subagent" in p
    assert "compute" in p


# --- the shared runtime, driven with a fake model + fake finance tool --------

def test_grounded_run_calls_tool_and_reports_clean(monkeypatch):
    model = FakeChatModel([
        {"tool": "write_plan", "args": {"steps": ["income", "answer"]}},
        {"tool": "income_statement", "args": {}},
        {"text": "Net income was $1,448.08 for the period."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(cfo_agent.ask(_settings(), "How did we do?",
                                    memory=SafeMemory(None)))
    assert "1,448.08" in out["answer"]
    assert out["verification"]["ungrounded"] == []
    assert any(e.get("name") == "write_plan" for e in out["trace"])


def test_ungrounded_figure_triggers_one_revise_pass(monkeypatch):
    model = FakeChatModel([
        {"tool": "income_statement", "args": {}},
        {"text": "Net income $1,448.08 and an invented loss of $7,652.00."},
        {"text": "Correction: net income $1,448.08; I cannot see the other figure."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(cfo_agent.ask(_settings(), "How did we do?",
                                    memory=SafeMemory(None)))
    assert out["verification"]["revised"] is True
    assert "1,448.08" in out["answer"]
    assert out["verification"]["ungrounded"] == []
