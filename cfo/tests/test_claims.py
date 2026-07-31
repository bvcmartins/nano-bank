from cfo import claims


def test_grounded_periods_from_list_periods_output_and_tool_inputs():
    trace = [
        {"kind": "tool", "name": "list_periods", "input": "{}",
         "output": "['2026-06', '2026-07']"},
        {"kind": "tool", "name": "nim", "input": "{'period': '2026-07'}",
         "output": "{'nim': '0.0628'}"},
        {"kind": "model", "name": "model", "input": None, "output": None},
    ]
    assert claims.grounded_periods(trace) == {"2026-06", "2026-07"}


def test_grounded_periods_ignores_non_period_numbers():
    trace = [{"kind": "tool", "name": "raroc", "input": "{'period': '2026-07'}",
              "output": "{'raroc': '0.151', 'total_rwa': '2026000'}"}]
    assert claims.grounded_periods(trace) == {"2026-07"}


def test_sentences_split_on_enders_newlines_and_pipes():
    text = "First point. Second one!\nThird | fourth"
    assert claims._sentences(text) == ["First point", "Second one",
                                       "Third", "fourth"]


def test_cue_regexes_match_expected_phrases():
    assert claims._DISCLAIMER.search("I cannot see an LCR")
    assert claims._DISCLAIMER.search("my tools don't produce that")
    assert not claims._DISCLAIMER.search("our LCR is weak")
    assert claims._UNAVAIL.search("2026-07 may need to be closed first")
    assert not claims._UNAVAIL.search("2026-07 NIM is 6.28%")
    assert claims._OFFER.search("I can close 2026-08 for you")
    assert claims._OFFER.search("would you like me to close it")
    assert not claims._OFFER.search("2026-08 is closed")


def test_phantoms_cover_lcr_nsfr_npl():
    keys = set(claims._PHANTOM_CONCEPTS)
    assert "lcr" in keys and "npl" in keys and "nsfr" in keys


def _trace_periods(*periods):
    inp = "".join(f"{{'period': '{p}'}}" for p in periods)
    return [{"kind": "tool", "name": "list_periods", "input": "{}",
             "output": str(list(periods))},
            {"kind": "tool", "name": "nim", "input": inp, "output": "{}"}]


def test_phantom_metric_affirmative_is_flagged_but_disclaimer_is_not():
    trace = _trace_periods("2026-07")
    assert claims.unsupported_claims("Our LCR looks weak.", trace) == \
        ["LCR — no tool provides this"]
    assert claims.unsupported_claims("I cannot see an LCR.", trace) == []
    assert claims.unsupported_claims(
        "My tools don't produce an NPL ratio.", trace) == []


def test_grounded_period_called_unavailable_is_flagged():
    trace = _trace_periods("2026-06", "2026-07")
    ans = "The nim tool returned 2026-07, but that period may need to be closed first."
    assert claims.unsupported_claims(ans, trace) == \
        ["2026-07 described as unavailable, but a tool returned it"]


def test_grounded_period_stated_plainly_is_not_flagged():
    trace = _trace_periods("2026-07")
    assert claims.unsupported_claims("NIM for 2026-07 is 6.28%.", trace) == []


def test_fabricated_period_is_flagged_but_offer_and_unavail_are_not():
    trace = _trace_periods("2026-07")
    assert claims.unsupported_claims("In 2026-05 our NIM was 5%.", trace) == \
        ["2026-05 — no tool has data for this period"]
    assert claims.unsupported_claims("I can close 2026-08 for you.", trace) == []
    assert claims.unsupported_claims("2026-08 is not closed yet.", trace) == []


def test_issues_are_deduplicated():
    trace = _trace_periods("2026-07")
    ans = "Our LCR is weak. Again, the LCR is weak."
    assert claims.unsupported_claims(ans, trace) == ["LCR — no tool provides this"]


def test_phantom_disclaimed_anywhere_is_not_flagged_in_other_sentences():
    """The live failure: an honest NPL decline discloses inability in one
    sentence and explains the concept in others; those explanatory mentions
    must not be flagged. Disclaimer scope is the whole answer, per concept."""
    trace = _trace_periods("2026-07")
    ans = ("I have no tool that reports a non-performing loan (NPL) ratio. "
           "NPL is a count of loans past a delinquency threshold. "
           "These are not substitutes for an NPL ratio.")
    assert claims.unsupported_claims(ans, trace) == []


def test_phantom_grouped_so_npl_reported_once_not_as_two_labels():
    trace = _trace_periods("2026-07")
    ans = "Our NPL is high and the NPL ratio is climbing."
    assert claims.unsupported_claims(ans, trace) == ["NPL — no tool provides this"]


def test_phantom_plural_spelling_is_flagged():
    """`\\bnpl\\b` didn't match 'NPLs' — the plural slipped through the guard."""
    trace = _trace_periods("2026-07")
    assert claims.unsupported_claims("Our NPLs are rising.", trace) == \
        ["NPL — no tool provides this"]


def test_fabricated_period_acknowledged_in_another_sentence_is_not_flagged():
    """Same root cause on the period side: a non-grounded period the answer
    calls unavailable anywhere is not a fabrication."""
    trace = _trace_periods("2026-07")
    ans = "In 2026-05 our NIM was 5%. But 2026-05 is not closed."
    assert claims.unsupported_claims(ans, trace) == []
