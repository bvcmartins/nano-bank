"""Deterministic answer verifier for the Agent CFO.

Every money/ratio figure the CFO states must trace to a number some tool
returned this turn. The trace of tool outputs is the oracle; there is no LLM
here. See docs/superpowers/specs/2026-07-22-cfo-answer-verifier-design.md.
"""
from __future__ import annotations
import ast
import json
import re
from decimal import Decimal, InvalidOperation

from . import claims as _claims

# A signed number literal: comma-grouped (1,448.08) or plain (9100.00 / 510000),
# with an optional decimal part. Unicode minus is normalised before matching.
_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _to_decimal(raw: str) -> "Decimal | None":
    try:
        return Decimal(raw.replace("−", "-").replace(",", ""))
    except InvalidOperation:
        return None


def _bare_number(s: str) -> "Decimal | None":
    """A string that is *only* a number (Decimals serialise as strings), else
    None — so '0.151' grounds but '2026-07' or 'CAD, annual' does not."""
    s2 = s.strip().replace("−", "-")
    return _to_decimal(s2) if _NUM.fullmatch(s2) else None


# Keys whose values are NOT figures a CFO answer should ground against: prose
# (units/basis/note/source), the risk-weight assumption table, list-of-roles
# diagnostics, and bookkeeping scalars. Flattening these into the grounded pool
# was the #3 defect — it let "our NPL ratio is 3%" ground against a 0.03 loss
# weight, and units/basis prose integers (365, 31) ground arbitrary figures.
# Nested notes are dropped at their own level too, since the check is per-dict.
_NOISE_KEYS = frozenset({
    "units", "basis", "note", "source",
    "risk_weights", "assumed_weight_roles", "assumed_loss_roles",
    "unclassified_roles", "opening_snapshot_missing",
    "error", "period", "available", "period_days",
})


def _parse(raw):
    """Best-effort parse of a tool output into a Python structure: JSON first,
    then a Python literal (the trace stores str(dict) with single quotes)."""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(raw)
        except (ValueError, SyntaxError):
            continue
    return None


def _harvest(node, out: list) -> None:
    """Collect numbers from VALUE positions of a parsed tool output, skipping
    noise keys at every level. Strings that are themselves nested structures
    (e.g. an MCP text block wrapping the real JSON) are recursed into."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _NOISE_KEYS:
                continue
            _harvest(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _harvest(v, out)
    elif isinstance(node, bool):
        return  # bool is an int subclass — never a figure
    elif isinstance(node, (int, float)):
        d = _to_decimal(str(node))
        if d is not None:
            out.append(d)
    elif isinstance(node, str):
        d = _bare_number(node)
        if d is not None:
            out.append(d)
            return
        nested = _parse(node)
        if isinstance(nested, (dict, list)):
            _harvest(nested, out)


def grounded_values(trace: list[dict]) -> list[Decimal]:
    """Every figure a tool actually *returned* this turn — harvested from value
    positions of the parsed output, not every literal in its text. Falls back to
    a raw text scan for an output that won't parse, so it never under-grounds and
    raises false 'ungrounded' flags."""
    out: list[Decimal] = []
    for ev in trace:
        if ev.get("kind") != "tool":
            continue
        raw = ev.get("output")
        if not raw:
            continue
        parsed = _parse(raw)
        if parsed is not None:
            _harvest(parsed, out)
        else:
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


def ungrounded(answer: str, trace: list[dict]) -> list[str]:
    """The prose figures that match no number any tool returned this turn."""
    grounded = grounded_values(trace)
    return [f.text for f in claimed_figures(answer)
            if not _is_grounded(f, grounded)]


def report(answer: str, trace: list[dict], *, revised: bool) -> dict:
    grounded = grounded_values(trace)
    g: list[str] = []
    u: list[str] = []
    for f in claimed_figures(answer):
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
            "it, or state plainly you cannot see it — and never assert a period "
            "is unavailable if a tool returned data for it.")
    parts.append("Then give the corrected answer.")
    return " ".join(parts)


def badge(rep: dict) -> str:
    issues = list(rep["ungrounded"]) + list(rep.get("unsupported_claims", []))
    if not issues:
        return "✓ all figures tool-grounded"
    tail = " (after one revision)" if rep["revised"] else ""
    return f"⚠ {len(issues)} issue(s){tail}: {', '.join(issues)}"
