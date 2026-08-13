from __future__ import annotations
from typing import Callable, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Settings
from .service import run_code_task as default_run


class CodeTaskRequest(BaseModel):
    kind: str
    task: str


def _default_probes(settings: Settings) -> dict:
    def ollama() -> bool:
        from . import model_factory as mf
        return mf.backend_healthcheck(settings)

    return {"ollama": ollama}


def create_app(settings: Settings, run_fn: Optional[Callable] = None,
               probes: Optional[dict] = None) -> FastAPI:
    run_fn = run_fn or (lambda kind, task, settings: default_run(kind, task, settings=settings))
    probes = probes if probes is not None else _default_probes(settings)
    app = FastAPI(title="nano-bank coder")

    @app.get("/livez")
    def livez():
        # Liveness only: is the process up? No dependency probes / model round-trip.
        return {"status": "ok", "service": "coder"}

    @app.get("/health")
    def health():
        checks = {}
        for name, probe in probes.items():
            try:
                checks[name] = bool(probe())
            except Exception:  # noqa: BLE001
                checks[name] = False
        return {"status": "ok", "service": "coder", "checks": checks}

    @app.post("/code-task")
    def code_task(req: CodeTaskRequest):
        return run_fn(req.kind, req.task, settings)

    return app
