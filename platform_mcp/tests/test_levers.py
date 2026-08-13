from platform_mcp import levers


def _dep(name, desired, ready, conditions=(), cluster="nano-bank", ns="nano-bank"):
    return {"cluster": cluster, "namespace": ns, "name": name, "desired": desired,
            "ready": ready, "available": ready, "updated": desired, "unavailable": 0,
            "images": ["x:1"], "conditions": [dict(c) for c in conditions]}


def _pod(name, restart_count=0, waiting=None, cluster="nano-bank"):
    return {"cluster": cluster, "namespace": "nano-bank", "name": name,
            "phase": "Running", "containers": [
                {"name": "c", "ready": waiting is None, "restart_count": restart_count,
                 "waiting_reason": waiting}]}


def _rs(name, owner, revision, cluster="nano-bank"):
    return {"cluster": cluster, "namespace": "nano-bank", "name": name,
            "owner_deployment": owner, "revision": revision, "desired": 1, "ready": 1}


def test_is_allowed():
    al = [("nano-bank", "coo"), ("modern-core", "modern-core")]
    assert levers.is_allowed(al, "nano-bank", "coo") is True
    assert levers.is_allowed(al, "nano-bank", "postgres") is False
    assert levers.is_allowed(al, "modern-core", "coo") is False


def test_restart_warranted_on_crashloop():
    dep = _dep("coo", 1, 1)
    pods = [_pod("coo-1", restart_count=9, waiting="CrashLoopBackOff")]
    assert levers.restart_warranted(dep, pods) is True


def test_restart_warranted_on_unready():
    assert levers.restart_warranted(_dep("coo", 2, 1), []) is True


def test_restart_not_warranted_when_healthy():
    assert levers.restart_warranted(_dep("coo", 1, 1), [_pod("coo-1")]) is False


def test_rollback_warranted_when_stalled_with_prior_revision():
    dep = _dep("cfo", 2, 1, conditions=[
        {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}])
    rss = [_rs("cfo-a", "cfo", 4), _rs("cfo-b", "cfo", 5), _rs("other-x", "bank-api", 2)]
    ok, target = levers.rollback_warranted(dep, rss)
    assert ok is True and target == 4          # second-highest revision for cfo


def test_rollback_not_warranted_without_a_prior_revision():
    dep = _dep("cfo", 2, 1, conditions=[
        {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}])
    ok, target = levers.rollback_warranted(dep, [_rs("cfo-b", "cfo", 5)])
    assert ok is False and target is None


def test_rollback_not_warranted_when_progressing_normally():
    dep = _dep("cfo", 1, 1, conditions=[
        {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"}])
    ok, target = levers.rollback_warranted(dep, [_rs("a", "cfo", 4), _rs("b", "cfo", 5)])
    assert ok is False and target is None


_ALLOW = [("nano-bank", "cfo")]


def test_remediation_signal_true_when_degraded():
    deps = [_dep("cfo", 2, 1)]
    assert levers.remediation_signal_present(deps, [], _ALLOW) is True


def test_remediation_signal_false_when_all_healthy():
    deps = [_dep("cfo", 2, 2)]
    assert levers.remediation_signal_present(deps, [], _ALLOW) is False


def test_remediation_signal_ignores_non_allowlisted():
    deps = [_dep("postgres", 2, 0)]        # degraded but NOT allow-listed
    assert levers.remediation_signal_present(deps, [], _ALLOW) is False


def test_remediation_signal_true_when_stalled():
    deps = [_dep("cfo", 1, 1, conditions=[
        {"type": "Progressing", "reason": "ProgressDeadlineExceeded"}])]
    assert levers.remediation_signal_present(deps, [], _ALLOW) is True
