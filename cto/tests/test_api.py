from fastapi.testclient import TestClient

from cto.api import create_app
from cto.config import Settings


def _settings():
    return Settings.from_env({})


def test_ask_endpoint_delegates_to_ask_fn():
    async def fake_ask(settings, message, thread_id):
        return {"answer": f"echo:{message}", "thread_id": thread_id or "t1",
                "trace": [], "verification": {"ungrounded": []}}

    app = create_app(_settings(), ask_fn=fake_ask, probes={})
    client = TestClient(app)
    r = client.post("/ask", json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "echo:hi"
    assert body["thread_id"] == "t1"


def test_health_reports_each_probe_and_never_500s():
    probes = {"ollama": lambda: True,
              "platform_mcp": lambda: False,
              "qdrant": lambda: (_ for _ in ()).throw(RuntimeError("down"))}
    app = create_app(_settings(), ask_fn=None, probes=probes)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["ollama"] is True
    assert checks["platform_mcp"] is False
    assert checks["qdrant"] is False
