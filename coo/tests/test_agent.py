import asyncio

from coo import agent as agent_mod
from coo.config import Settings
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_ops_tools


def _settings():
    return Settings.from_env({})


def _patch(monkeypatch, model):
    monkeypatch.setattr(agent_mod.mf, "llm", lambda **k: model)

    async def _tools(settings):
        return fake_ops_tools()

    monkeypatch.setattr(agent_mod, "get_tools", _tools)


def test_grounded_run_plans_calls_tool_and_reports_clean(monkeypatch):
    model = FakeChatModel([
        {"tool": "write_plan", "args": {"steps": ["float", "answer"]}},
        {"tool": "float_position", "args": {}},
        {"text": "Operational float is $700.00 CAD."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "what's our float?",
                                    memory=SafeMemory(None)))
    assert "700.00" in out["answer"]
    assert out["verification"]["ungrounded"] == []
    # the plan tool call is in the merged trace
    assert any(e.get("name") == "write_plan" for e in out["trace"])


def test_ungrounded_figure_triggers_one_revise_pass(monkeypatch):
    model = FakeChatModel([
        {"tool": "float_position", "args": {}},
        {"text": "Float is $999.00 CAD."},               # ungrounded (tool says 700.00)
        {"text": "Correction: float is $700.00 CAD."},   # after nudge, grounded
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "float?", memory=SafeMemory(None)))
    assert out["verification"]["revised"] is True
    assert "700.00" in out["answer"]
    assert out["verification"]["ungrounded"] == []
