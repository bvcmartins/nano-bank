from fastapi.testclient import TestClient
from cfo.config import Settings
from cfo.api import create_app


def _client(ask_fn):
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    return TestClient(create_app(s, ask_fn=ask_fn))


def test_ask_endpoint_returns_answer():
    async def fake_ask(settings, message, thread_id=None):
        return {"answer": f"echo:{message}", "thread_id": thread_id or "t",
                "trace": []}
    r = _client(fake_ask).post("/ask", json={"message": "hi", "thread_id": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "echo:hi"
    assert body["thread_id"] == "t1"


def test_ask_endpoint_previews_long_trace_outputs():
    """/ask is unauthenticated (any in-cluster pod can reach it); the verifier has
    already consumed the full tool outputs server-side, so the response previews
    them rather than shipping the whole bank's financials in every trace event."""
    big = "x" * 5000

    async def fake_ask(settings, message, thread_id=None):
        return {"answer": "ok", "thread_id": "t",
                "trace": [{"kind": "tool", "name": "raroc", "output": big}]}

    r = _client(fake_ask).post("/ask", json={"message": "hi"})
    out = r.json()["trace"][0]["output"]
    assert len(out) < len(big)
    assert out.startswith("x" * 100)
    assert "chars" in out          # the truncation marker


def test_health_endpoint():
    async def fake_ask(*a, **k):
        return {"answer": "", "thread_id": "t", "trace": []}
    r = _client(fake_ask).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
