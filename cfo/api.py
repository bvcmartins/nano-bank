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


def create_app(settings: Settings, ask_fn: Optional[Callable] = None,
               ask_stream_fn: Optional[Callable] = None) -> FastAPI:
    ask_fn = ask_fn or default_ask
    ask_stream_fn = ask_stream_fn or default_ask_stream
    app = FastAPI(title="nano-bank CFO")

    @app.get("/livez")
    def livez():
        return {"status": "ok", "service": "cfo"}

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "cfo"}

    @app.post("/ask")
    async def ask_endpoint(req: AskRequest):
        return await ask_fn(settings, req.message, req.thread_id)

    @app.post("/ask/stream")
    async def ask_stream_endpoint(req: AskRequest):
        # NDJSON: one `{"event": …}` per step as it closes, then a `{"final": …}`.
        async def gen():
            async for chunk in ask_stream_fn(settings, req.message, req.thread_id):
                yield json.dumps(chunk) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app
