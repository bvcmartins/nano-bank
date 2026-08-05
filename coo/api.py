from __future__ import annotations
import json
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import Settings
from .agent import ask as default_ask
from .agent import ask_stream as default_ask_stream


class AskRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


def _default_probes(settings: Settings) -> dict:
    """Best-effort dependency probes for /health. Each returns a bool and never
    raises; a down dependency degrades the report, it does not 500 the endpoint."""
    def ollama() -> bool:
        from . import model_factory as mf
        return mf.backend_healthcheck(settings)

    def operations_mcp() -> bool:
        import anyio
        from .tools import get_tools
        try:
            return len(anyio.run(get_tools, settings)) > 0
        except Exception:  # noqa: BLE001
            return False

    def qdrant() -> bool:
        try:
            from qdrant_client import QdrantClient
            QdrantClient(url=settings.qdrant_url).get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    return {"ollama": ollama, "operations_mcp": operations_mcp, "qdrant": qdrant}


def create_app(settings: Settings, ask_fn: Optional[Callable] = None,
               probes: Optional[dict] = None,
               ask_stream_fn: Optional[Callable] = None) -> FastAPI:
    ask_fn = ask_fn or default_ask
    ask_stream_fn = ask_stream_fn or default_ask_stream
    probes = probes if probes is not None else _default_probes(settings)
    app = FastAPI(title="nano-bank COO")

    @app.get("/livez")
    def livez():
        # Liveness only: is the process up and serving? No dependency probes, no
        # model inference — a kubelet livenessProbe hitting /health would invoke a
        # real GLM round-trip (the ollama probe) every few seconds and could kill
        # a healthy pod on a slow model. Readiness/health lives at /health.
        return {"status": "ok", "service": "coo"}

    @app.get("/health")
    def health():
        checks = {}
        for name, probe in probes.items():
            try:
                checks[name] = bool(probe())
            except Exception:  # noqa: BLE001
                checks[name] = False
        # Qdrant is best-effort (memory degrades gracefully); the service is "ok"
        # as long as it is serving. Individual checks tell the fuller story.
        return {"status": "ok", "service": "coo", "checks": checks}

    @app.post("/ask")
    async def ask_endpoint(req: AskRequest):
        return await ask_fn(settings, req.message, req.thread_id)

    @app.post("/ask/stream")
    async def ask_stream_endpoint(req: AskRequest):
        # NDJSON: one JSON object per line — `{"event": …}` per step as it closes,
        # then a final `{"final": …}`. Lets the console render the run live instead
        # of blocking on the whole turn.
        async def gen():
            async for chunk in ask_stream_fn(settings, req.message, req.thread_id):
                yield json.dumps(chunk) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app
