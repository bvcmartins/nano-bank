import asyncio

from csuite import runtime, verifier
from csuite.harness.memory import SafeMemory
from csuite.tests.fakes import FakeChatModel, fake_ops_tools


def _settings():
    from coo.config import Settings
    return Settings.from_env({})


def test_report_uses_injected_claims_fn():
    calls = {}

    def fake_claims(answer, trace):
        calls["hit"] = (answer, trace)
        return ["INJECTED-CLAIM"]

    rep = verifier.report("some answer", [], revised=False, claims_fn=fake_claims)
    assert rep["unsupported_claims"] == ["INJECTED-CLAIM"]
    assert calls["hit"][0] == "some answer"


def test_report_default_claims_fn_unchanged():
    # No claims_fn -> shared behavior: a clean platform-style answer has none.
    rep = verifier.report("All deployments are ready.", [], revised=False)
    assert rep["unsupported_claims"] == []


def test_runtime_ask_threads_claims_fn(monkeypatch):
    seen = {}

    def fake_claims(answer, trace):
        seen["answer"] = answer
        return []  # no claims -> no revise loop

    model = FakeChatModel([{"text": "Nothing derived here."}])
    out = asyncio.run(runtime.ask(
        settings=_settings(), message="hi", prompt="p", model=model,
        tools=fake_ops_tools(), agent="cto", memory=SafeMemory(None),
        claims_fn=fake_claims))
    assert seen["answer"] == "Nothing derived here."
    assert out["verification"]["unsupported_claims"] == []
