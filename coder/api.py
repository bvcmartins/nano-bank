"""FastAPI surface for the coder service (:8096).

TRUST BOUNDARY: these endpoints are UNAUTHENTICATED, by design and consistent with
the other in-cluster services — but this one has more teeth than a read-only status
API. POST /code-task triggers an LLM-backed coding run and a branch push; GET /runs/*
returns a run's full agent transcript and diff. The compensating controls are network,
not application: the coder is reachable only in-cluster (a ClusterIP Service, no
Ingress) and the egress firewall denies pod->host/LAN, so nothing outside the
namespace can call it. If this service is ever exposed beyond the cluster, put an
auth gate in front of /code-task and /runs FIRST."""
from __future__ import annotations
import logging
from typing import Callable, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Settings
from .git_ops import code_task_result
from . import service as svc
from .service import run_code_task as default_run

log = logging.getLogger("coder.api")


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

    @app.get("/runs")
    def runs():
        # The review branches of recent runs whose transcript the console can fetch.
        return {"branches": svc.list_runs()}

    @app.get("/runs/latest")
    def run_latest():
        return svc.latest_run() or {}

    @app.get("/runs/{branch:path}")
    def run_by_branch(branch: str):
        # {branch:path} so a slashful review branch (cto/…-<ts>) matches as one arg.
        return svc.get_run(branch) or {}

    @app.post("/code-task")
    def code_task(req: CodeTaskRequest):
        # Never surface a raw 500 to the lever: any unexpected coder failure becomes
        # a structured `failed` result so the CTO records a clean audited outcome
        # (not "coder unreachable") and the demo keeps moving.
        try:
            return run_fn(req.kind, req.task, settings)
        except Exception as exc:  # noqa: BLE001
            log.exception("coder run failed")
            return code_task_result(
                "failed", summary=f"{req.kind}: {req.task[:120]}",
                reason=f"coder error: {type(exc).__name__}: {str(exc)[:200]}")

    return app
