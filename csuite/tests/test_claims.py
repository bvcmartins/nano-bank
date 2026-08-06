from csuite.claims import unsupported_claims, grounded_windows


def _tool(name, inp, out):
    return {"kind": "tool", "name": name, "input": inp, "output": out}


def test_fraud_metric_mentioned_is_flagged():
    trace = [_tool("exceptions", "{'window': '7d'}", "{'window': '7d', 'total': 3}")]
    issues = unsupported_claims("The fraud rate looks elevated this week.", trace)
    assert any("fraud" in i.lower() for i in issues)


def test_fraud_disclaimed_is_not_flagged():
    trace = []
    ans = ("I cannot see fraud data — it is out of my scope as COO, so I will "
           "not speak to fraud rates.")
    assert unsupported_claims(ans, trace) == []


def test_window_used_by_a_tool_is_grounded():
    trace = [_tool("transactions", "{'window': '7d'}",
                   "{'window': '7d', 'total_count': 6}")]
    assert "7d" in grounded_windows(trace)
    assert unsupported_claims("Over the last 7d, volume was 6.", trace) == []


def test_window_not_covered_is_flagged():
    trace = [_tool("transactions", "{'window': '7d'}",
                   "{'window': '7d', 'total_count': 6}")]
    issues = unsupported_claims("Over the last 30d the trend rose.", trace)
    assert any("30d" in i for i in issues)


def test_window_flagged_but_acknowledged_is_exempt():
    trace = []
    ans = "I do not have a tool covering 30d, so I cannot speak to that window."
    assert unsupported_claims(ans, trace) == []
