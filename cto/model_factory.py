from __future__ import annotations
import logging
from functools import lru_cache
from typing import Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from .config import Settings

log = logging.getLogger("cto.llm")

_RESOLVED: Optional[str] = None
_SETTINGS: Optional[Settings] = None


def build_model(model: str, settings: Settings, *, temperature: float = 0.1,
                max_tokens: Optional[int] = None) -> ChatOpenAI:
    kw = dict(model=model, temperature=temperature, base_url=settings.ollama_base_url,
              api_key=settings.ollama_api_key or "ollama", timeout=600)
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return ChatOpenAI(**kw)


def _default_probe(model: str, settings: Settings) -> bool:
    try:
        m = build_model(model, settings, temperature=0.0, max_tokens=8)
        m.invoke([HumanMessage("reply with the single word: ok")])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("probe failed for %s: %s", model, e)
        return False


def _candidates(settings: Settings) -> list[str]:
    """Primary model then fallback, de-duplicated, empties dropped.

    kimi-k3 is a metered add-on on ollama.com; when its credit is empty (or the
    network is down) its probe fails and we degrade to kimi-k2.6 rather than
    refuse to start.
    """
    out: list[str] = []
    for m in (settings.cto_model, settings.cto_model_fallback):
        if m and m not in out:
            out.append(m)
    return out


def resolve_model(settings: Settings,
                  probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    probe = probe or _default_probe
    candidates = _candidates(settings)
    for i, model in enumerate(candidates):
        if probe(model, settings):
            if i > 0:
                log.warning("primary model unavailable; fell back to %s", model)
            log.info("resolved model: %s", model)
            return model
    raise RuntimeError(
        f"no model answered at {settings.ollama_base_url}: "
        f"tried {', '.join(candidates)}")


def init_models(settings: Settings,
                probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    global _RESOLVED, _SETTINGS
    _SETTINGS = settings
    _RESOLVED = resolve_model(settings, probe)
    return _RESOLVED


@lru_cache(maxsize=8)
def _client(model: str, temperature: float, max_tokens: Optional[int]) -> ChatOpenAI:
    return build_model(model, _SETTINGS, temperature=temperature,
                       max_tokens=max_tokens)


def llm(*, temperature: float = 0.1, max_tokens: Optional[int] = None) -> ChatOpenAI:
    if _RESOLVED is None or _SETTINGS is None:
        raise RuntimeError("call init_models(settings) before llm()")
    return _client(_RESOLVED, temperature, max_tokens)


def backend_healthcheck(settings: Settings) -> bool:
    """Healthy if any candidate (primary or fallback) answers."""
    try:
        return any(_default_probe(m, settings) for m in _candidates(settings))
    except Exception:  # noqa: BLE001
        return False
