import anyio
import pytest
from platform_mcp.config import Settings
from platform_mcp.mcp_server import build_mcp


def _settings():
    return Settings.from_env({"ALLOW_LIST": "nano-bank/coo"})


class _K8s:
    def __init__(self, deployments, pods=None, replicasets=None):
        self._d, self._p, self._r = deployments, pods or [], replicasets or []
    def deployments(self): return self._d
    def pods(self): return self._p
    def replicasets(self): return self._r


class _Writer:
    def __init__(self): self.calls = []
    def rollout_restart(self, cluster, name):
        self.calls.append(("restart", cluster, name)); return {"restarted_at": "t"}
    def rollback(self, cluster, name, target):
        self.calls.append(("rollback", cluster, name, target)); return {"rolled_back_to": target}


class _Audit:
    def __init__(self, fail=False): self.posts = []; self._fail = fail
    def post_action(self, action, params, effect):
        self.posts.append((action, params, effect))
        if self._fail: raise RuntimeError("ledger down")
        return {"seq": 1, "entry_hash": "h"}


def _crashloop_dep():
    return {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo",
            "desired": 1, "ready": 0, "available": 0, "updated": 1, "unavailable": 1,
            "images": ["x:1"], "conditions": []}


def _crashloop_pod():
    return {"cluster": "nano-bank", "namespace": "nano-bank", "name": "coo-1",
            "phase": "Running", "containers": [
                {"name": "c", "ready": False, "restart_count": 9,
                 "waiting_reason": "CrashLoopBackOff"}]}


def test_lever_tools_registered_only_with_acting_deps():
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    mcp = build_mcp(k8s, None, writer=_Writer(), audit=_Audit(), settings=_settings())
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert {"execute_rollout_restart", "execute_rollback"} <= names
    # Phase A shape (no acting deps) registers only the read tools.
    read_only = build_mcp(k8s, None)
    ro_names = {t.name for t in anyio.run(read_only.list_tools)}
    assert "execute_rollout_restart" not in ro_names


def test_restart_executes_when_warranted_and_audits():
    from platform_mcp import mcp_server
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit()
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert out["outcome"] == "executed"
    assert ("restart", "nano-bank", "coo") in w.calls
    assert a.posts and a.posts[0][0] == "rollout_restart"
    assert a.posts[0][2]["outcome"] == "executed"


def test_restart_refuses_when_not_allowed_and_still_audits():
    from platform_mcp import mcp_server
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit()
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "postgres")
    assert out["outcome"] == "refused"
    assert w.calls == []                       # never acted
    assert a.posts and a.posts[0][2]["outcome"] == "refused"


def test_restart_refuses_when_precondition_false():
    from platform_mcp import mcp_server
    healthy = dict(_crashloop_dep(), ready=1, available=1, unavailable=0)
    k8s = _K8s([healthy], pods=[])
    w, a = _Writer(), _Audit()
    out = mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert out["outcome"] == "refused"
    assert w.calls == []


def test_audit_failure_after_acting_raises_loud():
    from platform_mcp import mcp_server
    k8s = _K8s([_crashloop_dep()], pods=[_crashloop_pod()])
    w, a = _Writer(), _Audit(fail=True)
    with pytest.raises(RuntimeError):
        mcp_server._do_restart(k8s, w, a, _settings(), "nano-bank", "coo")
    assert w.calls, "acted before the audit failed"
