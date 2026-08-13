import pytest

from coder.config import Settings
from coder import model_factory as mf


def test_resolve_role_prefers_primary():
    s = Settings.from_env({"CODER_REASONING_MODEL": "kimi-k3",
                           "CODER_MODEL_FALLBACK": "kimi-k2.6"})
    seen = []

    def probe(model, settings):
        seen.append(model)
        return True

    assert mf.resolve_role(s, "reasoning", probe) == "kimi-k3"
    assert seen == ["kimi-k3"]


def test_resolve_role_falls_back_when_primary_down():
    s = Settings.from_env({"CODER_REASONING_MODEL": "kimi-k3",
                           "CODER_MODEL_FALLBACK": "kimi-k2.6"})

    def probe(model, settings):
        return model == "kimi-k2.6"

    assert mf.resolve_role(s, "reasoning", probe) == "kimi-k2.6"


def test_resolve_role_raises_when_none_answer():
    s = Settings.from_env({})
    with pytest.raises(RuntimeError):
        mf.resolve_role(s, "fast", lambda m, st: False)


def test_init_models_resolves_both_roles():
    s = Settings.from_env({})
    resolved = mf.init_models(s, probe=lambda m, st: True)
    assert set(resolved) == {"reasoning", "fast"}
