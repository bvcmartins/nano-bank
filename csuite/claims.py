"""Named-claim grounding for the Agent COO.

The number verifier grounds figures; this grounds *claims* — about windows the
tools did not actually cover, and about phantom concepts outside the COO's scope
(fraud / AML / suspicious-activity data, which the operations tools deliberately
cannot see). Deterministic, cue-based, disclaimer-aware — no LLM. Retarget of the
CFO's claim verifier (period grounding -> window grounding; finance phantoms ->
fraud/AML phantoms)."""
from __future__ import annotations
import re

# The windows the operations tools support. Prose variants normalise to these.
_CANON = {"24h": "24h", "7d": "7d", "30d": "30d",
          "24 hours": "24h", "7 days": "7d", "30 days": "30d"}
_WINDOW = re.compile(r"\b(24h|7d|30d|24 hours|7 days|30 days)\b", re.I)


def _windows(text: str) -> set[str]:
    return {_CANON[m.lower()] for m in _WINDOW.findall(text or "")}


def grounded_windows(trace: list[dict]) -> set[str]:
    """Windows a tool actually operated on this turn: those any tool was called
    with (input) plus those a tool echoed back (output — the ops MCP returns a
    `window` field in every windowed summary)."""
    out: set[str] = set()
    for ev in trace:
        if ev.get("kind") != "tool":
            continue
        for key in ("input", "output"):
            v = ev.get(key)
            if v:
                out |= _windows(v if isinstance(v, str) else str(v))
    return out


# Break on sentence enders, newlines, and table-row pipes so a cue and a token
# in the same clause stay together but separate clauses don't bleed.
_SPLIT = re.compile(r"[.!?\n|]+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text) if s.strip()]


# A negation / inability cue: the COO honestly declining data it can't see.
_DISCLAIMER = re.compile(
    r"\b(can ?not|can'?t|do not|don'?t|does not|doesn'?t|unable|outside"
    r"|out of (?:my )?scope|not available|no\b[^.]*\btool"
    r"|not\b[^.]*\b(?:see|track|produce|capture|have|show|cover))\b",
    re.I)

# Concepts no operations tool provides — fraud/AML are out of the COO's scope by
# design. Grouping lets a disclaimer on any label cover every spelling.
_PHANTOM_CONCEPTS = {
    "fraud": (["fraud rate", "fraudulent", "fraud"], "fraud data"),
    "aml": (["anti-money-laundering", "anti money laundering", "money laundering",
             "money-laundering", "aml"], "AML data"),
    "sar": (["suspicious activity", "suspicious activities",
             "suspicious-activity", "sar"], "suspicious-activity data"),
}


def _concept_present(low: str, labels: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(lab)}\b", low) for lab in labels)


def unsupported_claims(answer: str, trace: list[dict]) -> list[str]:
    """Membership guards (phantom concepts) are scoped to the WHOLE answer, not
    one sentence: an honest decline discloses inability in one sentence and may
    name the concept in others, so a sentence-local guard would flag the
    explanatory mentions. Window claims are per-sentence (a window cited next to
    an unavailability cue is acknowledged, not fabricated)."""
    sents = [(s, s.lower(), bool(_DISCLAIMER.search(s))) for s in _sentences(answer)]

    # A phantom concept disclosed as un-seeable anywhere is declined everywhere.
    disclaimed: set[str] = set()
    for _s, low, disc in sents:
        if disc:
            for cid, (labels, _name) in _PHANTOM_CONCEPTS.items():
                if _concept_present(low, labels):
                    disclaimed.add(cid)

    issues: list[str] = []
    low_all = answer.lower()
    for cid, (labels, name) in _PHANTOM_CONCEPTS.items():
        if cid not in disclaimed and _concept_present(low_all, labels):
            issues.append(f"{name} — out of the COO's scope; no tool provides this")

    grounded = grounded_windows(trace)
    for s, _low, disc in sents:
        for w in _windows(s):
            if w not in grounded and not disc:
                issues.append(f"{w} — no tool covered this window")

    # de-duplicate, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    return deduped
