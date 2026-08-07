"""Deterministic answer verifier for the Agent COO.

Every money/ratio figure the COO states must trace to a number some tool
returned this turn. The trace of tool outputs is the oracle; there is no LLM
here. (Numeric grounding is domain-agnostic — this is the CFO's verifier.)
"""
from __future__ import annotations
import math
import re
from decimal import Decimal, InvalidOperation

from . import claims as _claims

# Memory tools replay prose the COO wrote on *past* turns; a number recalled from
# an earlier answer is not a fresh tool computation and must not ground a figure
# stated this turn (otherwise the COO can "remember" a stale total and the
# verifier waves it through). Grounding harvests only this turn's domain tools.
_MEMORY_TOOLS = {"recall_memory", "record_memory"}

# A signed number literal: comma-grouped (1,448.08) or plain (9100.00 / 510000),
# with an optional decimal part. Unicode minus is normalised before matching.
_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _to_decimal(raw: str) -> "Decimal | None":
    try:
        return Decimal(raw.replace("−", "-").replace(",", ""))
    except InvalidOperation:
        return None


def grounded_values(trace: list[dict]) -> list[Decimal]:
    """Every numeric literal appearing in any tool output in the trace."""
    out: list[Decimal] = []
    for ev in trace:
        if ev.get("kind") != "tool":
            continue
        if ev.get("name") in _MEMORY_TOOLS:
            continue
        raw = ev.get("output")
        if not raw:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        for m in _NUM.findall(text.replace("−", "-")):
            d = _to_decimal(m)
            if d is not None:
                out.append(d)
    return out


# One pass, non-overlapping, left to right. Alternation order matters: money and
# percent win over the bare-decimal branch so "$1,448.08" is money, not a plain
# decimal. `_DEC` only matches comma-grouped OR >=2-decimal numbers, so bare
# integers (years, counts) never match any branch and stay exempt.
_FIG = re.compile(
    r"(?P<money>[-−]?\$\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?P<pct>[-−]?\d[\d,]*(?:\.\d+)?\s?%)"
    r"|(?P<dec>[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+\.\d{2,})"
)


class Figure:
    __slots__ = ("text", "value", "is_percent", "decimals")

    def __init__(self, text: str, value: Decimal, is_percent: bool,
                 decimals: int):
        self.text = text
        self.value = value
        self.is_percent = is_percent
        self.decimals = decimals

    def __repr__(self) -> str:  # aids test failure messages
        return f"Figure({self.text!r}, {self.value}, pct={self.is_percent})"


def _decimals_of(text: str) -> int:
    if "." not in text:
        return 0
    return len(text.rsplit(".", 1)[1].rstrip("%").strip())


def claimed_figures(answer: str) -> list[Figure]:
    figs: list[Figure] = []
    for m in _FIG.finditer(answer):
        text = m.group(0)
        is_percent = m.lastgroup == "pct"
        cleaned = text.replace("−", "-").replace("$", "").replace("%", "")
        cleaned = cleaned.replace(",", "").strip()
        value = _to_decimal(cleaned)
        if value is None:
            continue
        figs.append(Figure(text, value, is_percent, _decimals_of(text)))
    return figs


def _close(grounded: Decimal, target: Decimal, decimals: int) -> bool:
    """True if a grounded value equals the target at the figure's displayed
    precision. Tolerance is half of the last shown decimal place, OR 0.1% of
    the target for large rounded figures — whichever is larger."""
    place = Decimal(5) * (Decimal(10) ** -(decimals + 1))   # half last digit
    rel = abs(target) * Decimal("0.001")                    # 0.1% presentation
    tol = place if place > rel else rel
    return abs(grounded - target) <= tol


def _is_grounded(fig: Figure, grounded: list[Decimal]) -> bool:
    # (target, displayed-precision) pairs. A percent in prose ("12.50%", 2 dp) is
    # matched two ways: against a tool value already in percent units at the shown
    # precision, and against the ratio form tools actually store (0.1250). The
    # ratio form is two decimal places finer (0.001250), so its tolerance must
    # scale with it — otherwise the half-last-digit tolerance stays in
    # percentage-point units and is ~100x too loose, accepting "12.50%" against a
    # tool ratio of 0.1290 (12.90%).
    targets = [(fig.value, fig.decimals)]
    if fig.is_percent:
        targets.append((fig.value / Decimal(100), fig.decimals + 2))
    for t, dp in targets:
        for g in grounded:
            if _close(g, t, dp):
                return True
    return False


# Round-number policy thresholds ("wires above $1,000,000", "target <5% recalls")
# are proposals, not claimed measurements — no tool can ground a hypothetical, so
# flagging them as ungrounded is a false positive. A figure is exempt only when it
# is BOTH round (<=2 significant figures, no fractional cents) AND sits in a
# threshold / approximation clause. Requiring both keeps a fabricated *precise*
# metric ("average wire was $1,234,567") strict — only hedged round numbers pass.
_THRESHOLD_CUE = re.compile(
    r"(?:\b(?:above|over|under|below|beyond|exceed(?:s|ing)?|approach(?:es|ing)?|"
    r"greater than|less than|more than|fewer than|at least|at most|up to|"
    r"no more than|no less than|in excess of|north of|south of|threshold|"
    r"thresholds|cap(?:ped)?|ceiling|floor|limit|minimum|maximum|target(?:ing|s)?|"
    r"around|roughly|about|approximately|approx\.?|nearly|almost|order of|"
    r"upwards? of)\b|[~≥≤><])",
    re.I)


def _is_round(value: Decimal) -> bool:
    """A round magnitude: a whole number equal to itself at 2 significant figures
    (1,000,000 / 1,500,000 / 500,000 / 5 / 90 — yes; 1,234,567 / 949,191.61 — no).
    Fractional cents disqualify it: a measurement, not a threshold."""
    v = abs(value)
    if v == 0 or v != v.to_integral_value():
        return False
    exp = int(math.floor(math.log10(float(v))))
    quant = Decimal(10) ** max(exp - 1, 0)          # step that keeps 2 sig figs
    return (v / quant) == (v / quant).to_integral_value()


def _threshold_exempt(answer: str) -> set[str]:
    """Figure *texts* to exempt: round numbers appearing in a threshold or
    approximation clause. Scoped per sentence so a cue only covers figures in the
    same clause, not the whole answer."""
    exempt: set[str] = set()
    for sent in _claims._sentences(answer):
        if not _THRESHOLD_CUE.search(sent):
            continue
        for f in claimed_figures(sent):
            if _is_round(f.value):
                exempt.add(f.text)
    return exempt


def ungrounded(answer: str, trace: list[dict]) -> list[str]:
    """The prose figures that match no number any tool returned this turn,
    excluding round policy thresholds (which no tool can ground)."""
    grounded = grounded_values(trace)
    exempt = _threshold_exempt(answer)
    return [f.text for f in claimed_figures(answer)
            if f.text not in exempt and not _is_grounded(f, grounded)]


def report(answer: str, trace: list[dict], *, revised: bool) -> dict:
    grounded = grounded_values(trace)
    exempt = _threshold_exempt(answer)
    g: list[str] = []
    u: list[str] = []
    for f in claimed_figures(answer):
        if f.text in exempt:
            continue        # a round policy threshold — neither grounded nor a claim
        (g if _is_grounded(f, grounded) else u).append(f.text)
    return {"grounded": g, "ungrounded": u,
            "unsupported_claims": _claims.unsupported_claims(answer, trace),
            "revised": revised}


def revise_prompt(figures: list[str], claims: list[str] = ()) -> str:
    parts = []
    if figures:
        parts.append(
            "Verification found figures in your answer that are not grounded "
            f"in any tool result from this turn: {', '.join(figures)}. For each "
            "one, either recompute it by calling the appropriate tool, or state "
            "plainly that it is your own estimate rather than a tool figure.")
    if claims:
        parts.append(
            "You also made claims not supported by your tools this turn: "
            f"{', '.join(claims)}. Correct each — call the tool that settles "
            "it, or state plainly you cannot see it — and never assert a window "
            "is unavailable if a tool returned data for it.")
    parts.append(
        "Then RESTATE YOUR ENTIRE ANSWER IN FULL with the corrections applied — "
        "reproduce the whole report, every section and figure, as a single "
        "self-contained reply. Do NOT send only the changed lines, a diff, or a "
        "note about what you fixed; the reader sees only this message, not your "
        "previous one, so it must stand completely on its own.")
    return " ".join(parts)


def badge(rep: dict) -> str:
    issues = list(rep["ungrounded"]) + list(rep.get("unsupported_claims", []))
    if not issues:
        return "✓ all figures tool-grounded"
    tail = " (after one revision)" if rep["revised"] else ""
    return f"⚠ {len(issues)} issue(s){tail}: {', '.join(issues)}"
