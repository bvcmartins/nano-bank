"""resolve_model tries the primary model, then falls back to cto_model_fallback.

The demo/presentation runs the CTO on kimi-k3, which is a metered add-on on
ollama.com; if its credit is empty (or the network is down) the probe fails and
the agent must degrade to kimi-k2.6 rather than refuse to start.
"""
from cto.config import Settings
from cto.model_factory import resolve_model


def _settings(primary="kimi-k3", fallback="kimi-k2.6"):
    return Settings.from_env({"CTO_MODEL": primary, "CTO_MODEL_FALLBACK": fallback})


def test_uses_primary_when_it_answers():
    s = _settings()
    picked = resolve_model(s, probe=lambda m, _s: m == "kimi-k3")
    assert picked == "kimi-k3"


def test_falls_back_when_primary_is_unavailable():
    s = _settings()
    # primary (kimi-k3) never answers — empty extra-usage balance — k2.6 does.
    picked = resolve_model(s, probe=lambda m, _s: m == "kimi-k2.6")
    assert picked == "kimi-k2.6"


def test_raises_only_when_no_candidate_answers():
    s = _settings()
    try:
        resolve_model(s, probe=lambda m, _s: False)
    except RuntimeError as e:
        assert "kimi-k3" in str(e) and "kimi-k2.6" in str(e)
    else:
        raise AssertionError("expected RuntimeError when nothing answers")


def test_no_duplicate_probe_when_primary_equals_fallback():
    s = _settings(primary="kimi-k2.6", fallback="kimi-k2.6")
    seen = []

    def probe(m, _s):
        seen.append(m)
        return True

    assert resolve_model(s, probe=probe) == "kimi-k2.6"
    assert seen == ["kimi-k2.6"]  # collapsed to a single candidate
