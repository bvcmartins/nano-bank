from csuite.trace_view import beat_outcome


def _tool(name, output):
    return {"kind": "tool", "name": name, "output": output}


def test_executed_rollback_pulls_revision_detail():
    trace = [_tool("platform_health", "ok"),
             _tool("execute_rollback", "{'outcome': 'executed', 'effect': {'rolled_back_to': 28}}")]
    out = beat_outcome(trace)
    assert out["kind"] == "executed"
    assert "28" in out["detail"]


def test_refused_restart_pulls_reason():
    trace = [_tool("execute_rollout_restart",
                   '{"outcome": "refused", "reason": "coo is not crashlooping or unready"}')]
    out = beat_outcome(trace)
    assert out["kind"] == "refused"
    assert "crashlooping" in out["detail"]


def test_no_lever_is_read_only():
    trace = [_tool("estate_health", "..."), _tool("compute", "12.5")]
    assert beat_outcome(trace)["kind"] == "read_only"


def test_hint_used_only_without_a_lever():
    assert beat_outcome([], outcome_hint="deferred")["kind"] == "deferred"
    # a real lever result wins over the hint
    trace = [_tool("execute_rollback", "{'outcome': 'executed'}")]
    assert beat_outcome(trace, outcome_hint="deferred")["kind"] == "executed"


def test_last_lever_event_wins():
    trace = [_tool("execute_rollout_restart", '{"outcome":"refused","reason":"healthy"}'),
             _tool("execute_rollback", '{"outcome":"executed","effect":{"rolled_back_to":16}}')]
    assert beat_outcome(trace)["kind"] == "executed"


def _delegate_ev(output):
    return [{"kind": "tool", "name": "delegate_coding_task", "output": output}]


def test_beat_outcome_delegated_executed_with_pr():
    ev = _delegate_ev({"outcome": "executed", "pr_url": "https://github.com/o/r/pull/7"})
    got = beat_outcome(ev)
    assert got["kind"] == "delegated"
    assert "pull/7" in got["detail"]


def test_beat_outcome_delegate_failed():
    ev = _delegate_ev({"outcome": "failed", "reason": "coder unreachable"})
    assert beat_outcome(ev)["kind"] == "failed"


def test_beat_outcome_delegate_refused():
    ev = _delegate_ev({"outcome": "refused", "reason": "no signal"})
    assert beat_outcome(ev)["kind"] == "refused"


def test_beat_outcome_delegated_local_branch_ref():
    ev = _delegate_ev({"outcome": "executed",
                       "pr_url": "cto/fix-rounding-T @ file:///sandbox"})
    got = beat_outcome(ev)
    assert got["kind"] == "delegated"
    assert "cto/fix-rounding-T" in got["detail"]


def test_beat_outcome_failed_task_mentioning_executed_is_not_delegated():
    # Finding 11: the failure result echoes the model-authored task into `summary`, so
    # a task whose words include "executed" must NOT be misread as a delegated beat
    # claiming a PR. Anchoring on the structured `outcome` field is what fixes it.
    ev = _delegate_ev({"outcome": "failed",
                       "summary": "delivery: make the executed-order test pass",
                       "reason": "repo tests still red after coder rounds"})
    got = beat_outcome(ev)
    assert got["kind"] == "failed"
    assert "PR opened" not in got.get("detail", "")
