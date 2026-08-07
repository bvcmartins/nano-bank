from decimal import Decimal as D
from csuite import verifier


def test_grounded_values_parses_numbers_from_tool_outputs():
    trace = [
        {"kind": "tool", "name": "float_position",
         "output": "{'total_float': '700.00', 'by_system': {'interac': '150.00'}}"},
        {"kind": "model", "name": "model", "output": None},
        {"kind": "tool", "name": "rails",
         "output": "{'by_rail': {'interac': {'total_amount': '9000.00'}}}"},
    ]
    vals = verifier.grounded_values(trace)
    assert D("700.00") in vals
    assert D("150.00") in vals
    assert D("9000.00") in vals


def test_grounded_values_ignores_model_events_and_empty_output():
    trace = [
        {"kind": "model", "name": "model", "output": None},
        {"kind": "tool", "name": "t", "output": ""},
    ]
    assert verifier.grounded_values(trace) == []


def test_claimed_figures_extracts_money_percent_and_formatted_decimals():
    ans = ("Settlement success was 21.31% on float of $1,448.08; total float "
           "$815,636.08. Backlog 59.7%. A swing of -$2,551.92.")
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
    ans = ("Over 7d the sweep captured 16 items across a 31-day window; "
           "365 days in a year. No dollar or percent here.")
    assert verifier.claimed_figures(ans) == []


def test_claimed_figures_handles_unicode_minus():
    ans = "Float swung to −3.70% this window."
    figs = verifier.claimed_figures(ans)
    assert len(figs) == 1
    assert figs[0].is_percent
    assert figs[0].value == D("-3.70")


def _trace(*outputs):
    return [{"kind": "tool", "name": "t", "output": o} for o in outputs]


def test_ungrounded_flags_a_fabricated_figure():
    trace = _trace("{'total_float': '1448.08', 'by_system': {'interac': '0.2131'}}")
    ans = "Float was $1,448.08, but after the drain it is -$7,652.00."
    assert verifier.ungrounded(ans, trace) == ["-$7,652.00"]


def test_percent_matches_ratio_form_within_rounding():
    trace = _trace("{'success_rate': '0.213108'}")
    assert verifier.ungrounded("Success rate is 21.31%.", trace) == []
    assert verifier.ungrounded("Success rate is 21.3%.", trace) == []


def test_percent_ratio_tolerance_is_scaled_not_percentage_point_loose():
    trace = _trace("{'success_rate': '0.1290'}")
    assert verifier.ungrounded("Success rate is 12.50%.", trace) == ["12.50%"]
    assert verifier.ungrounded("Success rate is 12.90%.", trace) == []


def test_currency_matches_after_separator_strip():
    trace = _trace("{'total_float': '815636.08'}")
    assert verifier.ungrounded("Total float $815,636.08.", trace) == []


def test_grounded_and_ungrounded_together():
    trace = _trace("{'success_rate': '0.2131', 'total_float': '1448.08'}")
    ans = "Success 21.31% on $1,448.08, and an invented 42.0% backlog."
    assert verifier.ungrounded(ans, trace) == ["42.0%"]


def test_round_threshold_in_a_cue_clause_is_not_flagged():
    # The real-world false positive: a proposed policy threshold no tool returns.
    trace = _trace("{'avg_wire': '949191.61'}")
    assert verifier.ungrounded(
        "Add a cooling-off checkpoint for wires exceeding $1,000,000.", trace) == []
    assert verifier.ungrounded(
        "Target a recall rate below 5% by count.", trace) == []
    assert verifier.ungrounded(
        "Dual-verify wires above ~$500,000.", trace) == []


def test_fabricated_precise_metric_is_still_flagged_even_near_a_cue():
    # Roundness is the guard: a precise, non-round figure stays strict.
    trace = _trace("{'avg_wire': '949191.61'}")
    assert verifier.ungrounded(
        "The average recalled wire, above trend, was $1,099,939.06.", trace) \
        == ["$1,099,939.06"]


def test_round_number_without_a_cue_is_still_flagged():
    # Exemption needs the threshold/approximation context, not just roundness.
    trace = _trace("{'total': '123.00'}")
    assert verifier.ungrounded("We moved $2,000,000 last week.", trace) == ["$2,000,000"]


def test_report_drops_exempt_threshold_from_both_lists():
    trace = _trace("{'success_rate': '0.2131'}")
    rep = verifier.report("Success 21.31%; cap new wires at $1,000,000.",
                          trace, revised=False)
    assert rep["grounded"] == ["21.31%"]
    assert rep["ungrounded"] == []


def test_report_splits_grounded_and_ungrounded():
    trace = _trace("{'success_rate': '0.2131'}")
    rep = verifier.report("Success 21.31% vs made-up $9,999.00.", trace,
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
    trace = []
    rep = verifier.report("The fraud rate is rising.", trace, revised=False)
    assert rep["unsupported_claims"] and "fraud" in rep["unsupported_claims"][0].lower()
    assert rep["ungrounded"] == []


def test_revise_prompt_includes_claims_when_present():
    claim = "fraud data — out of the COO's scope; no tool provides this"
    msg = verifier.revise_prompt(["$7,652.00"], [claim])
    assert "$7,652.00" in msg
    assert claim in msg


def test_revise_prompt_without_claims_is_unchanged():
    msg = verifier.revise_prompt(["$7,652.00"])
    assert "$7,652.00" in msg


def test_badge_warns_when_only_a_claim_is_present():
    warn = verifier.badge({"grounded": [], "ungrounded": [],
                           "unsupported_claims": ["fraud data — out of the COO's scope"],
                           "revised": True})
    assert "⚠" in warn and "fraud" in warn
