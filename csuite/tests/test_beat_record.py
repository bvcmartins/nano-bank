import json
from datetime import datetime, timezone
from csuite.trace_view import beat_record


def test_record_is_json_serialisable_and_shaped():
    beat = {"title": "Recovery", "shows": "it acts", "message": "fix cfo"}
    resp = {"answer": "I rolled it back.",
            "trace": [{"kind": "tool", "name": "execute_rollback",
                       "output": "{'outcome':'executed','effect':{'rolled_back_to':28}}"}]}
    rec = beat_record(7, beat, resp, now=datetime(2026, 8, 12, tzinfo=timezone.utc))
    json.dumps(rec)  # must not raise
    assert rec["beat"] == 7
    assert rec["title"] == "Recovery"
    assert rec["question"] == "fix cfo"
    assert rec["answer"] == "I rolled it back."
    assert rec["outcome"]["kind"] == "executed"
    assert rec["ts"].startswith("2026-08-12")


def test_harness_counts_are_summarised():
    beat = {"title": "Review", "shows": "grounded", "message": "review"}
    resp = {"answer": "…", "trace": [
        {"kind": "tool", "name": "write_plan", "input": "x"},
        {"kind": "tool", "name": "estate_health", "input": None},
        {"kind": "tool", "name": "estate_health", "input": None},
        {"kind": "subagent", "task": "deep-dive cfo", "tools": ["rollouts"], "depth": 1, "chars": 20},
    ]}
    rec = beat_record(1, beat, resp)
    assert rec["harness"]["planned"] == 1
    assert rec["harness"]["subagents"] == 1
    assert "estate_health" in rec["harness"]["tools"]
    assert rec["outcome"]["kind"] == "read_only"


def test_outcome_hint_flows_through():
    beat = {"title": "Scope", "shows": "defers", "message": "P&L?", "outcome_hint": "deferred"}
    rec = beat_record(5, beat, {"answer": "ask the CFO", "trace": []})
    assert rec["outcome"]["kind"] == "deferred"
