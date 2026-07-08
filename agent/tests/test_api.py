from fastapi.testclient import TestClient
from agent.config import Settings
from agent.api import create_app


def _app():
    settings = Settings.from_env({"BRANCH_SERVICE_TOKEN": "svc"})

    async def fake_assist(settings, cid, token, message, thread_id=None):
        return {"answer": f"hi {cid}", "thread_id": "th1",
                "pending_action": {"id": "act-1", "summary": "Transfer 50"}}

    async def fake_confirm(settings, cid, token, action_id, cancel=False):
        return {"status": "cancelled"} if cancel else {"transaction_id": "t1"}

    class R:
        def resolve(self, cid): return "jwt-" + cid

    return TestClient(create_app(settings, assist_fn=fake_assist,
                                 confirm_fn=fake_confirm, token_resolver=R()))


def test_message_requires_service_token():
    c = _app()
    r = c.post("/branch/clients/cust-1/message", json={"message": "hi"})
    assert r.status_code == 401


def test_message_returns_pending_action():
    c = _app()
    r = c.post("/branch/clients/cust-1/message", json={"message": "transfer 50"},
               headers={"Authorization": "Bearer svc"})
    assert r.status_code == 200
    assert r.json()["pending_action"]["id"] == "act-1"


def test_confirm_executes():
    c = _app()
    r = c.post("/branch/clients/cust-1/actions/act-1/confirm",
               headers={"Authorization": "Bearer svc"})
    assert r.status_code == 200 and r.json()["transaction_id"] == "t1"


def test_health_ok():
    assert _app().get("/health").status_code == 200
