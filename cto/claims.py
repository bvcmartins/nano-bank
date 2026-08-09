"""Named-claim grounding for the Agent CTO. The number verifier grounds figures;
this grounds *claims* about phantom concepts outside the CTO's lane: the books
(the CFO's), money-movement operations detail (the COO's), and fraud/AML. The
platform reads are point-in-time snapshots, so there is NO window grounding here
(unlike the COO). Deterministic, cue-based, disclaimer-aware — no LLM."""
from __future__ import annotations
import re

_SPLIT = re.compile(r"[.!?\n|]+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s.strip()]


# A negation / inability / deferral cue: the CTO honestly staying in its lane.
_DISCLAIMER = re.compile(
    r"\b(can ?not|can'?t|do not|don'?t|does not|doesn'?t|unable|outside"
    r"|out of (?:my )?scope|not available|CFO|COO"
    r"|not\b[^.]*\b(?:see|track|produce|capture|have|show|cover))\b",
    re.I)

# Concepts no platform tool provides. Grouping lets a disclaimer on any label
# cover every spelling. The offered redirect names the right officer.
_PHANTOM_CONCEPTS = {
    "books": (["net interest margin", "nim", "raroc", "profitability", "p&l",
               "p and l", "return on assets", "margin"],
              "the books (P&L / NIM / RAROC) — that's the CFO's domain"),
    "money_ops": (["settlement volume", "settlement float", "rail throughput",
                   "float position", "clearing float", "money movement"],
                  "money-movement operations detail — that's the COO's domain"),
    "fraud": (["fraud rate", "fraudulent", "fraud"], "fraud data — out of scope"),
    "aml": (["anti-money-laundering", "anti money laundering", "money laundering",
             "money-laundering", "aml"], "AML data — out of scope"),
}


def _concept_present(low: str, labels: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(lab)}\b", low) for lab in labels)


def unsupported_claims(answer: str, trace: list[dict]) -> list[str]:
    """Phantom-concept membership guard scoped to the WHOLE answer: an honest
    deferral discloses in one sentence and may name the concept in others, so a
    sentence-local guard would flag the explanatory mentions."""
    sents = [(s.lower(), bool(_DISCLAIMER.search(s))) for s in _sentences(answer)]

    disclaimed: set[str] = set()
    for low, disc in sents:
        if disc:
            for cid, (labels, _name) in _PHANTOM_CONCEPTS.items():
                if _concept_present(low, labels):
                    disclaimed.add(cid)

    issues: list[str] = []
    low_all = (answer or "").lower()
    for cid, (labels, name) in _PHANTOM_CONCEPTS.items():
        if cid not in disclaimed and _concept_present(low_all, labels):
            issues.append(name)

    seen: set[str] = set()
    out: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
