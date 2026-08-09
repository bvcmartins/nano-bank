"""Container entrypoint for the CTO A2A API: resolve the model at startup, serve."""
from __future__ import annotations
import uvicorn

from .config import Settings
from . import model_factory as mf
from .api import create_app


def build():
    settings = Settings.from_env()
    mf.init_models(settings)
    return settings, create_app(settings)


if __name__ == "__main__":
    settings, app = build()
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)
