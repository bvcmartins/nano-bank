"""The ONE backend-specific seam of the coder. Ports cto/model_factory.py's
kimi/ollama conventions (ChatOpenAI @ ollama.com/v1, primary->fallback probing)
and adds a role split (reasoning|fast) so the ported coding_agent's llm(role, ...)
calls read identically. `reasoning` is accepted for call-site compatibility with
the Gemini port but is a no-op here (kimi has no thinking-budget knob over the
OpenAI-compatible endpoint)."""
from __future__ import annotations
import logging
from functools import lru_cache
from typing import Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from .config import Settings

log = logging.getLogger("coder.llm")

_RESOLVED: dict = {}
_SETTINGS: Optional[Settings] = None


def build_model(model: str, settings: Settings, *, temperature: float = 0.2,
                max_tokens: Optional[int] = None) -> ChatOpenAI:
    kw = dict(model=model, temperature=temperature,
              base_url=settings.ollama_base_url,
              api_key=settings.ollama_api_key or "ollama",
              timeout=settings.request_timeout)
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


def _candidates(settings: Settings, role: str) -> list[str]:
    primary = settings.models.get(role) or settings.models["fast"]
    out: list[str] = []
    for m in (primary, settings.model_fallback):
        if m and m not in out:
            out.append(m)
    return out


def resolve_role(settings: Settings, role: str,
                 probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    probe = probe or _default_probe
    cands = _candidates(settings, role)
    for i, model in enumerate(cands):
        if probe(model, settings):
            if i > 0:
                log.warning("primary %s model unavailable; fell back to %s", role, model)
            return model
    raise RuntimeError(
        f"no {role} model answered at {settings.ollama_base_url}: "
        f"tried {', '.join(cands)}")


def init_models(settings: Settings,
                probe: Optional[Callable[[str, Settings], bool]] = None) -> dict:
    global _RESOLVED, _SETTINGS
    _SETTINGS = settings
    _RESOLVED = {role: resolve_role(settings, role, probe)
                 for role in ("reasoning", "fast")}
    return _RESOLVED


@lru_cache(maxsize=32)
def _client(model: str, temperature: float, max_tokens: Optional[int]) -> ChatOpenAI:
    return build_model(model, _SETTINGS, temperature=temperature, max_tokens=max_tokens)


def llm(role: str = "fast", *, reasoning: Optional[bool] = True,
        temperature: float = 0.2, max_tokens: Optional[int] = None) -> ChatOpenAI:
    if not _RESOLVED or _SETTINGS is None:
        raise RuntimeError("call init_models(settings) before llm()")
    model = _RESOLVED.get(role) or _RESOLVED["fast"]
    return _client(model, temperature, max_tokens)


def backend_healthcheck(settings: Settings) -> bool:
    try:
        return any(_default_probe(m, settings) for m in _candidates(settings, "fast"))
    except Exception:  # noqa: BLE001
        return False
