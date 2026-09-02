from fastapi.testclient import TestClient
from agent.config import Settings
from agent.api import create_app


def _app():
    settings = Settings.from_env({"BRANCH_SERVICE_TOKEN": "svc"})

    async def fake_assist(settings, cid, token, message, thread_id=None, crm_token=None):
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


def test_seed_route_requires_auth_and_returns_customers():
    settings = Settings.from_env({"BRANCH_SERVICE_TOKEN": "svc"})
    app = create_app(settings, seed_fn=lambda: {"customers": [{"customer_id": "c1"}]})
    c = TestClient(app)
    assert c.post("/branch/seed").status_code == 401
    r = c.post("/branch/seed", headers={"Authorization": "Bearer svc"})
    assert r.status_code == 200 and r.json()["customers"][0]["customer_id"] == "c1"


def test_no_seed_route_when_seed_fn_absent():
    c = _app()
    assert c.post("/branch/seed", headers={"Authorization": "Bearer svc"}).status_code == 404


def test_message_route_passes_the_resolved_crm_token_to_assist():
    seen = {}
    settings = Settings.from_env({"BRANCH_SERVICE_TOKEN": "svc"})

    async def fake_assist(settings, cid, token, message, thread_id=None, crm_token=None):
        seen["crm_token"] = crm_token
        return {"answer": "ok", "thread_id": "t1"}

    class NanoResolver:
        def resolve(self, cid):
            return "nano-tok"

    class CrmResolver:
        async def resolve(self, cid):
            return "crm-tok"

    app = create_app(
        settings,
        assist_fn=fake_assist,
        token_resolver=NanoResolver(),
        crm_resolver=CrmResolver(),
    )
    client = TestClient(app)
    resp = client.post(
        "/branch/clients/cust-1/message",
        json={"message": "hi"},
        headers={"Authorization": "Bearer svc"},
    )
    assert resp.status_code == 200
    assert seen["crm_token"] == "crm-tok"


def test_message_route_works_with_no_crm_resolver_configured():
    # Backward compatibility: omitting crm_resolver entirely must not break the
    # existing nano-only path.
    settings = Settings.from_env({"BRANCH_SERVICE_TOKEN": "svc"})

    async def fake_assist(settings, cid, token, message, thread_id=None, crm_token=None):
        assert crm_token is None
        return {"answer": "ok", "thread_id": "t1"}

    app = create_app(settings, assist_fn=fake_assist, token_resolver=None)
    client = TestClient(app)
    resp = client.post(
        "/branch/clients/cust-1/message",
        json={"message": "hi"},
        headers={"Authorization": "Bearer svc"},
    )
    assert resp.status_code == 200
