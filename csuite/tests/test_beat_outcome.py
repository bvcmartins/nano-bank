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
