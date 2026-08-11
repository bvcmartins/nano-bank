import asyncio

from cto import agent as agent_mod
from cto.config import Settings
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_platform_tools


def _settings():
    return Settings.from_env({})


def _patch(monkeypatch, model):
    monkeypatch.setattr(agent_mod.mf, "llm", lambda **k: model)

    async def _tools(settings):
        return fake_platform_tools()

    monkeypatch.setattr(agent_mod, "get_tools", _tools)


def test_grounded_estate_review_reports_clean(monkeypatch):
    model = FakeChatModel([
        {"tool": "write_plan", "args": {"steps": ["estate", "answer"]}},
        {"tool": "estate_health", "args": {}},
        {"text": "1 of 1 deployments healthy; 0 degraded."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "estate review?",
                                    memory=SafeMemory(None)))
    assert "0 degraded" in out["answer"]
    assert out["verification"]["ungrounded"] == []
    assert any(e.get("name") == "write_plan" for e in out["trace"])


def test_books_question_is_flagged_by_claims_and_revised(monkeypatch):
    # If the model wanders into the CFO's lane, the cto claims_fn catches it and
    # triggers one revise pass; the revised answer defers.
    model = FakeChatModel([
        {"text": "Our net interest margin is 3.2%."},          # out of lane
        {"text": "NIM is the CFO's domain; I cannot speak to it."},  # revised
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "how's NIM?",
                                    memory=SafeMemory(None)))
    assert out["verification"]["revised"] is True
    assert out["verification"]["unsupported_claims"] == []


def test_cto_pulls_a_lever_when_warranted(monkeypatch):
    model = FakeChatModel([
        {"tool": "estate_health", "args": {}},
        {"tool": "execute_rollout_restart",
         "args": {"cluster": "nano-bank", "deployment": "coo"}},
        {"text": "Restarted coo; the bank returned executed (restarted_at t)."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(), "coo is crashlooping, handle it",
                                    memory=SafeMemory(None)))
    assert any(e.get("name") == "execute_rollout_restart" for e in out["trace"])
    assert "executed" in out["answer"]


def test_cto_rolls_back_a_bad_revision(monkeypatch):
    # A stalled rollout on a bad revision must drive execute_rollback (not
    # restart — a restart re-runs the same broken spec).
    model = FakeChatModel([
        {"tool": "estate_health", "args": {}},
        {"tool": "execute_rollback",
         "args": {"cluster": "nano-bank", "deployment": "cfo"}},
        {"text": "Rolled cfo back to the last good revision (executed)."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(),
                                    "cfo is crashlooping on a bad rollout — fix it",
                                    memory=SafeMemory(None)))
    assert any(e.get("name") == "execute_rollback" for e in out["trace"])
    assert "executed" in out["answer"]
