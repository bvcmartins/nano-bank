import json

import httpx

from platform_mcp.config import Settings
from platform_mcp.coder_client import CoderClient


def _settings():
    return Settings.from_env({"SERVICE_CLIENT_SECRET": "x"})


def test_code_task_posts_and_returns_body():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"outcome": "executed", "pr_url": "https://x/pull/1"})

    tr = httpx.MockTransport(handler)
    out = CoderClient(_settings(), transport=tr).code_task("delivery", "do X")
    assert out["outcome"] == "executed"
    assert seen["url"].endswith("/code-task")
    assert seen["json"] == {"kind": "delivery", "task": "do X"}
