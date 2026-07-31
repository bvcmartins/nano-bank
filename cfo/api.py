from __future__ import annotations
from typing import Callable, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Settings
from .agent import ask as default_ask


class AskRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


_TRACE_PREVIEW = 200


def _redact_trace(trace: list) -> list:
    """Preview tool outputs in the /ask response rather than shipping them whole.

    The verifier consumes the full outputs server-side before we respond, so the
    caller doesn't need them — and /ask is unauthenticated (ClusterIP-only, but
    any in-cluster pod can reach it), so returning every tool's untruncated
    output hands the whole bank's financials to anything that can call it. The
    trace shape is kept for a debug UI; the payloads are previewed.
    """
    redacted = []
    for ev in trace:
        e = dict(ev)
        out = e.get("output")
        if isinstance(out, str) and len(out) > _TRACE_PREVIEW:
            e["output"] = f"{out[:_TRACE_PREVIEW]}… (+{len(out) - _TRACE_PREVIEW} chars)"
        redacted.append(e)
    return redacted


def create_app(settings: Settings, ask_fn: Optional[Callable] = None) -> FastAPI:
    ask_fn = ask_fn or default_ask
    app = FastAPI(title="nano-bank CFO")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "cfo"}

    @app.post("/ask")
    async def ask_endpoint(req: AskRequest):
        result = await ask_fn(settings, req.message, req.thread_id)
        if isinstance(result, dict) and isinstance(result.get("trace"), list):
            result = {**result, "trace": _redact_trace(result["trace"])}
        return result

    return app
