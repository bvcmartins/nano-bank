"""Named-claim grounding for the Agent CFO.

The number verifier grounds figures; this grounds *claims* about which periods
are available and about phantom metrics no tool provides. Deterministic,
cue-based, disclaimer-aware — no LLM. See
docs/superpowers/specs/2026-07-23-cfo-entity-claim-verifier-design.md.
"""
from __future__ import annotations
import re

# A YYYY-MM period token (full match, no capturing group so findall returns it).
_PERIOD = re.compile(r"\b20\d{2}-(?:0[1-9]|1[0-2])\b")


def grounded_periods(trace: list[dict]) -> set[str]:
    """Periods a tool actually surfaced this turn: the YYYY-MM tokens in any
    list_periods output, plus any period a tool was called with."""
    out: set[str] = set()
    for ev in trace:
        if ev.get("kind") != "tool":
            continue
        inp = ev.get("input") or ""
        out.update(_PERIOD.findall(inp if isinstance(inp, str) else str(inp)))
        if ev.get("name") == "list_periods":
            res = ev.get("output") or ""
            out.update(_PERIOD.findall(res if isinstance(res, str) else str(res)))
    return out


# Break on sentence enders, newlines, and table-row pipes so a cue and a token
# in the same clause stay together but separate clauses don't bleed.
_SPLIT = re.compile(r"[.!?\n|]+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text) if s.strip()]


# A negation / inability cue: the CFO honestly declining an entity it can't see.
_DISCLAIMER = re.compile(
    r"\b(can ?not|can'?t|do not|don'?t|does not|doesn'?t|unable|outside"
    r"|not available|no\b[^.]*\btool"
    r"|not\b[^.]*\b(?:see|track|produce|capture|have|show))\b",
    re.I)

# A period being called unavailable / unclosed.
_UNAVAIL = re.compile(
    r"\b(not closed|un-?closed|need(?:s)? to (?:be )?closed?"
    r"|may need to (?:be )?closed?|no snapshot|not available|isn'?t closed"
    r"|unavailable)\b",
    re.I)

# A legitimate offer to act on a period — must not be read as a fabrication.
_OFFER = re.compile(
    r"\b(would you like|if you'?d like|want me to|shall i|let me know"
    r"|i can (?:close|run|capture))\b",
    re.I)

# Metrics no tool provides, grouped by concept: id -> (labels, shown name).
# Grouping lets a disclaimer on any label (e.g. "non-performing loan") cover
# every spelling ("NPL", "NPL ratio") and report the concept once.
#
# This is a SAMPLE of the phantom metrics, not the full boundary: the prompt
# also tells the agent it cannot see concentration or maturities, which are not
# checked here. Anything genuinely un-seeable that the agent asserts affirmatively
# and this list misses will simply pass the claim channel — add it here to catch it.
_PHANTOM_CONCEPTS = {
    "lcr": (["liquidity coverage ratio", "lcr"], "LCR"),
    "nsfr": (["net stable funding ratio", "nsfr"], "NSFR"),
    "npl": (["npl ratio", "non-performing loan", "non performing loan", "npl"],
            "NPL"),
}


def _concept_present(low: str, labels: list[str]) -> bool:
    # `s?` so a plural spelling ("NPLs", "ratios") still matches the label.
    return any(re.search(rf"\b{re.escape(lab)}s?\b", low) for lab in labels)


def unsupported_claims(answer: str, trace: list[dict]) -> list[str]:
    """Membership guards (phantom metrics, fabricated periods) are scoped to the
    WHOLE answer, not one sentence: an honest decline discloses inability in one
    sentence and discusses the concept in others, so a sentence-local guard
    would flag the explanatory mentions. See the design's false-positive note."""
    grounded = grounded_periods(trace)
    sents = [(s, s.lower(), bool(_DISCLAIMER.search(s)),
              bool(_UNAVAIL.search(s)), bool(_OFFER.search(s)))
             for s in _sentences(answer)]

    # A phantom concept disclosed as un-seeable anywhere is declined everywhere.
    disclaimed: set[str] = set()
    for _s, low, disc, _u, _o in sents:
        if disc:
            for cid, (labels, _name) in _PHANTOM_CONCEPTS.items():
                if _concept_present(low, labels):
                    disclaimed.add(cid)
    # A non-grounded period the answer anywhere calls unavailable / offers to
    # close is acknowledged, not fabricated.
    acked: set[str] = set()
    for s, _low, disc, unavail, offer in sents:
        if disc or unavail or offer:
            acked.update(_PERIOD.findall(s))

    issues: list[str] = []
    # (a) phantom-metric membership — once per concept, answer-level
    low_all = answer.lower()
    for cid, (labels, name) in _PHANTOM_CONCEPTS.items():
        if cid not in disclaimed and _concept_present(low_all, labels):
            issues.append(f"{name} — no tool provides this")
    # (b) + (c) periods
    for s, _low, _disc, unavail, _offer in sents:
        periods = _PERIOD.findall(s)
        # (b) a grounded period called unavailable — only when every period in
        # the sentence is grounded, so the cue can't be about a different,
        # genuinely-unavailable period sharing the sentence.
        if unavail and periods and all(p in grounded for p in periods):
            for p in periods:
                issues.append(
                    f"{p} described as unavailable, but a tool returned it")
        # (c) a non-grounded period asserted as real, not acknowledged anywhere
        for p in periods:
            if p not in grounded and p not in acked:
                issues.append(f"{p} — no tool has data for this period")
    # de-duplicate, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    return deduped
