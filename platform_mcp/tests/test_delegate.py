import anyio

from platform_mcp.config import Settings
from platform_mcp import mcp_server as srv


class FakeAudit:
    def __init__(self):
        self.rows = []

    def post_action(self, action, params, effect):
        self.rows.append((action, params, effect))
        return {"id": len(self.rows)}


class FakeCoder:
    def __init__(self, ret=None, boom=False):
        self.ret, self.boom, self.calls = ret, boom, []

    def code_task(self, kind, task):
        self.calls.append((kind, task))
        if self.boom:
            raise RuntimeError("connect refused")
        return self.ret


class FakeK8s:
    def __init__(self, deps):
        self._d = deps

    def deployments(self):
        return self._d

    def pods(self):
        return []


class FakeHealth:
    def probe(self):
        return []


def _settings():
    return Settings.from_env({"SERVICE_CLIENT_SECRET": "x"})


_DEGRADED = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 1, "conditions": []}]
_HEALTHY = [{"cluster": "nano-bank", "name": "cfo", "desired": 2, "ready": 2, "conditions": []}]


def test_delivery_executes_and_audits():
    audit = FakeAudit()
    coder = FakeCoder(ret={"outcome": "executed", "pr_url": "https://x/pull/1",
                           "branch": "cto/x-T", "tests": "3p/0f", "summary": "s"})
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "do X")
    assert out["outcome"] == "executed"
    assert coder.calls == [("delivery", "do X")]
    assert audit.rows[0][0] == "delegate_coding_task"
    assert audit.rows[0][2]["pr_url"] == "https://x/pull/1"


def test_remediation_refused_without_signal():
    audit = FakeAudit()
    coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "remediation", "fix cfo")
    assert out["outcome"] == "refused"
    assert coder.calls == []                       # never called the coder
    assert audit.rows[0][2]["outcome"] == "refused"


def test_remediation_executes_with_signal():
    audit = FakeAudit()
    coder = FakeCoder(ret={"outcome": "executed", "pr_url": "https://x/pull/2"})
    out = srv._do_delegate(FakeK8s(_DEGRADED), coder, audit, _settings(), "remediation", "fix cfo")
    assert out["outcome"] == "executed"
    assert coder.calls == [("remediation", "fix cfo")]


def test_unknown_kind_refused():
    audit = FakeAudit()
    coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "yolo", "x")
    assert out["outcome"] == "refused" and coder.calls == []


def test_empty_task_refused():
    audit = FakeAudit()
    coder = FakeCoder()
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "   ")
    assert out["outcome"] == "refused" and coder.calls == []


def test_coder_unreachable_is_failed_not_crash():
    audit = FakeAudit()
    coder = FakeCoder(boom=True)
    out = srv._do_delegate(FakeK8s(_HEALTHY), coder, audit, _settings(), "delivery", "do X")
    assert out["outcome"] == "failed"
    assert audit.rows[0][2]["outcome"] == "failed"


def test_delegate_tool_registered_when_coder_present():
    mcp = srv.build_mcp(FakeK8s([]), FakeHealth(),
                        coder=FakeCoder(), audit=FakeAudit(), settings=_settings())
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert "delegate_coding_task" in names
