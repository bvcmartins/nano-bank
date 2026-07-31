from decimal import Decimal as D
from cfo import verifier


def test_grounded_values_parses_numbers_from_tool_outputs():
    trace = [
        {"kind": "tool", "name": "raroc",
         "output": "{'raroc': '0.151', 'expected_loss': '9100.00', "
                   "'credit_exposure': '510000'}"},
        {"kind": "model", "name": "model", "output": None},
        {"kind": "tool", "name": "key_ratios",
         "output": "{'roe': '0.2131', 'roa': '0.0209'}"},
    ]
    vals = verifier.grounded_values(trace)
    assert D("0.151") in vals
    assert D("9100.00") in vals
    assert D("510000") in vals
    assert D("0.2131") in vals


def test_grounded_values_ignores_risk_weights_and_prose_noise():
    """The #3 defect: every literal — risk weights, units/basis prose integers —
    was flattened into one pool, so an unrelated claim ('NPL is 3%') grounded
    against a 0.03 loss weight. Numbers are now harvested from value positions
    only, skipping the weight table and prose fields."""
    trace = [{"kind": "tool", "name": "raroc", "output": (
        "{'net_income': '1448.08', "
        "'risk_weights': {'CardReceivable': '0.75', 'Bank': '0.20'}, "
        "'assumed_weight_roles': ['Receivable'], "
        "'period_days': 31, "
        "'units': {'net_income': 'CAD, 31-day period'}, "
        "'basis': 'expected loss over 365 days'}")}]
    vals = verifier.grounded_values(trace)
    assert D("1448.08") in vals          # a real figure grounds
    assert D("0.75") not in vals         # a risk weight does not
    assert D("0.20") not in vals
    assert D("31") not in vals           # period_days / prose integers do not
    assert D("365") not in vals
    # concretely: a claim matching only a risk weight is now flagged
    assert verifier.ungrounded("Recovery runs at 75%.",
                               [{"kind": "tool", "name": "raroc",
                                 "output": "{'risk_weights': {'x': '0.75'}}"}]) \
        == ["75%"]


def test_grounded_values_ignores_model_events_and_empty_output():
    trace = [
        {"kind": "model", "name": "model", "output": None},
        {"kind": "tool", "name": "t", "output": ""},
    ]
    assert verifier.grounded_values(trace) == []


def test_claimed_figures_extracts_money_percent_and_formatted_decimals():
    ans = ("ROE was 21.31% on net income of $1,448.08; total assets "
           "$815,636.08. Efficiency 59.7%. A loss of -$2,551.92.")
    figs = {f.text: f for f in verifier.claimed_figures(ans)}
    assert "21.31%" in figs and figs["21.31%"].is_percent
    assert figs["21.31%"].value == D("21.31")
    assert figs["21.31%"].decimals == 2
    assert "$1,448.08" in figs and figs["$1,448.08"].value == D("1448.08")
    assert not figs["$1,448.08"].is_percent
    assert "$815,636.08" in figs
    assert "59.7%" in figs and figs["59.7%"].decimals == 1
    assert figs["-$2,551.92"].value == D("-2551.92")


def test_claimed_figures_exempts_bare_integers():
    ans = ("For 2026-07 the snapshot captured 16 roles across a 31-day "
           "period; 365 days in a year. No dollar or percent here.")
    assert verifier.claimed_figures(ans) == []


def test_claimed_figures_handles_unicode_minus():
    ans = "ROA swung to −3.70% this month."
    figs = verifier.claimed_figures(ans)
    assert len(figs) == 1
    assert figs[0].is_percent
    assert figs[0].value == D("-3.70")


def _trace(*outputs):
    return [{"kind": "tool", "name": "t", "output": o} for o in outputs]


def test_ungrounded_flags_a_fabricated_figure():
    """The $7,652 'monthly loss' the CFO invented appears in no tool output."""
    trace = _trace("{'net_income': '1448.08', 'roe': '0.2131'}")
    ans = "Net income was $1,448.08, but after the loss it is -$7,652.00."
    assert verifier.ungrounded(ans, trace) == ["-$7,652.00"]


def test_percent_matches_ratio_form_within_rounding():
    """Tools store ratios (0.213108); prose states 21.31% or 21.3%."""
    trace = _trace("{'roe': '0.213108'}")
    assert verifier.ungrounded("ROE is 21.31%.", trace) == []
    assert verifier.ungrounded("ROE is 21.3%.", trace) == []


def test_percent_ratio_tolerance_is_scaled_not_percentage_point_loose():
    """A wrong percent must NOT pass against the ratio-form tool value. The tool
    returned 0.1290 (12.90%); a claimed 12.50% is off by 0.40 percentage points
    and has to be flagged. Regression: the half-last-digit tolerance was derived
    from the percent text but applied to the ratio target unscaled (~100x too
    loose), so |0.1290 - 0.125| = 0.004 slipped under a 0.005 tolerance."""
    trace = _trace("{'roe': '0.1290'}")
    assert verifier.ungrounded("ROE is 12.50%.", trace) == ["12.50%"]
    # the correct figure still grounds
    assert verifier.ungrounded("ROE is 12.90%.", trace) == []


def test_currency_matches_after_separator_strip():
    trace = _trace("{'total_assets': '815636.08'}")
    assert verifier.ungrounded("Total assets $815,636.08.", trace) == []


def test_grounded_and_ungrounded_together():
    trace = _trace("{'roe': '0.2131', 'net_income': '1448.08'}")
    ans = "ROE 21.31% on $1,448.08, and an invented 42.0% efficiency."
    assert verifier.ungrounded(ans, trace) == ["42.0%"]


def test_report_splits_grounded_and_ungrounded():
    trace = _trace("{'roe': '0.2131'}")
    rep = verifier.report("ROE 21.31% vs made-up $9,999.00.", trace,
                          revised=False)
    assert rep["grounded"] == ["21.31%"]
    assert rep["ungrounded"] == ["$9,999.00"]
    assert rep["revised"] is False


def test_revise_prompt_names_every_offending_figure():
    msg = verifier.revise_prompt(["$7,652.00", "42.0%"])
    assert "$7,652.00" in msg and "42.0%" in msg
    assert "tool" in msg.lower()
    assert "estimate" in msg.lower()


def test_badge_reflects_state():
    clean = verifier.badge({"grounded": ["21.31%"], "ungrounded": [],
                            "revised": False})
    assert "✓" in clean  # check mark
    warn = verifier.badge({"grounded": [], "ungrounded": ["$7,652.00"],
                           "revised": True})
    assert "⚠" in warn and "$7,652.00" in warn


def test_report_includes_unsupported_claims():
    trace = [{"kind": "tool", "name": "list_periods", "input": "{}",
              "output": "['2026-07']"}]
    rep = verifier.report("Our LCR is weak.", trace, revised=False)
    assert rep["unsupported_claims"] == ["LCR — no tool provides this"]
    assert rep["ungrounded"] == []


def test_revise_prompt_includes_claims_when_present():
    msg = verifier.revise_prompt(["$7,652.00"], ["LCR — no tool provides this"])
    assert "$7,652.00" in msg
    assert "LCR — no tool provides this" in msg


def test_revise_prompt_without_claims_is_unchanged():
    msg = verifier.revise_prompt(["$7,652.00"])
    assert "$7,652.00" in msg


def test_badge_warns_when_only_a_claim_is_present():
    warn = verifier.badge({"grounded": [], "ungrounded": [],
                           "unsupported_claims": ["LCR — no tool provides this"],
                           "revised": True})
    assert "⚠" in warn and "LCR" in warn
