from fastapi.testclient import TestClient

from coder.config import Settings
from coder.api import create_app


def _client(run_fn):
    return TestClient(create_app(Settings.from_env({}), run_fn=run_fn,
                                 probes={"ollama": lambda: True}))


def test_livez():
    c = _client(lambda kind, task, settings: {})
    assert c.get("/livez").json()["status"] == "ok"


def test_health_reports_probes():
    c = _client(lambda kind, task, settings: {})
    body = c.get("/health").json()
    assert body["service"] == "coder"
    assert body["checks"]["ollama"] is True


def test_code_task_delegates_to_run_fn():
    seen = {}

    def run_fn(kind, task, settings):
        seen["kind"], seen["task"] = kind, task
        return {"outcome": "executed", "pr_url": "https://x/pull/1"}

    c = _client(run_fn)
    r = c.post("/code-task", json={"kind": "delivery", "task": "do X"})
    assert r.json()["outcome"] == "executed"
    assert seen == {"kind": "delivery", "task": "do X"}
