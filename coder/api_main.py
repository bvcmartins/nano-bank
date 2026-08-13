from __future__ import annotations

import uvicorn

from .config import Settings
from .api import create_app
from . import model_factory as mf


def main():
    settings = Settings.from_env()
    mf.init_models(settings)          # resolve kimi models once at boot
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)


if __name__ == "__main__":
    main()
