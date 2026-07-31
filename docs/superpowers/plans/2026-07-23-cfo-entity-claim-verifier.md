# CFO Entity/Claim Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the CFO verifier to flag period and phantom-metric claims — an available period called unavailable, a fabricated period asserted as real, and a phantom metric (LCR/NSFR/NPL) affirmatively claimed — and revise once, alongside the existing number grounding.

**Architecture:** A new pure module `cfo/claims.py` does cue-based, disclaimer-aware claim checking (no LLM). `cfo/verifier.py` aggregates: `report()` gains an `unsupported_claims` channel and `revise_prompt`/`badge` learn about it. `cfo/agent.py::ask()` revises when either the number channel or the claim channel has an issue.

**Tech Stack:** Python 3.12, `re`, `decimal` (existing), pytest. Runs under the finance venv (`/home/bmartins/dev/nano-bank/finance/.venv`); source imports from cwd, so run pytest from the repo/worktree root.

## Global Constraints

- The claim verifier contains **no LLM call** and no network/DB I/O — it is a pure function of `(answer_text, trace_events)`.
- Only two entity families are grounded: **periods** (`YYYY-MM`) and **phantom metrics** (LCR/NSFR/NPL). No roles, no tool-provided metrics, no tool names.
- Checks are **disclaimer-aware**: an honest decline ("I cannot see an LCR") and a legitimate offer ("I can close 2026-08 for you") must NOT be flagged.
- Exactly **one** revise retry (unchanged from the number verifier).
- Test runner: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest`, from the repo/worktree root.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- `cfo/claims.py` — **new.** Vocabulary, cue regexes, `grounded_periods`, `unsupported_claims`.
- `cfo/verifier.py` — **modify.** `report()` adds `unsupported_claims`; `revise_prompt(figures, claims=())`; `badge()` reflects claims.
- `cfo/agent.py` — **modify.** `ask()` revises on either channel; combined nudge.
- `cfo/verify-cfo.sh` — **modify.** Assert the premise-refusal answer has `unsupported_claims == []`.
- `cfo/tests/test_claims.py` — **new.** Unit tests for the claim module.
- `cfo/tests/test_verifier.py` — **modify.** Report/revise_prompt/badge with claims.
- `cfo/tests/test_agent.py` — **modify.** Revise driven by the claim channel.

---

## Task 1: `claims.py` — grounded periods

**Files:**
- Create: `cfo/claims.py`
- Test: `cfo/tests/test_claims.py` (create)

**Interfaces:**
- Consumes: trace events (`kind`, `name`, `input`, `output`).
- Produces: `grounded_periods(trace: list[dict]) -> set[str]` — the `YYYY-MM`
  tokens from `list_periods` outputs and from every tool `input`. Also the
  module-level `_PERIOD` regex reused by later tasks.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_claims.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cfo.claims'`.

- [ ] **Step 3: Write minimal implementation**

Create `cfo/claims.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/claims.py cfo/tests/test_claims.py
git commit -m "feat(cfo): claims — grounded period set from the trace

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `claims.py` — sentences and cue predicates

**Files:**
- Modify: `cfo/claims.py`
- Test: `cfo/tests/test_claims.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_sentences(text: str) -> list[str]`, and the compiled cue regexes
  `_DISCLAIMER`, `_UNAVAIL`, `_OFFER`, and the `_PHANTOMS` mapping (label →
  shown name). These are the building blocks Task 3 combines.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_claims.py`:

```python
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
    keys = set(claims._PHANTOMS)
    assert "lcr" in keys and "npl" in keys and "nsfr" in keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: FAIL — `AttributeError: module 'cfo.claims' has no attribute '_sentences'`.

- [ ] **Step 3: Write minimal implementation**

Append to `cfo/claims.py`:

```python
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

# Metrics no tool provides. label (regex-safe substring) -> shown name.
_PHANTOMS = {
    "liquidity coverage ratio": "liquidity coverage ratio",
    "lcr": "LCR",
    "net stable funding ratio": "net stable funding ratio",
    "nsfr": "NSFR",
    "npl ratio": "NPL ratio",
    "non-performing loan": "non-performing loan",
    "non performing loan": "non-performing loan",
    "npl": "NPL",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/claims.py cfo/tests/test_claims.py
git commit -m "feat(cfo): claims — sentence split and cue lexicons

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `claims.py` — `unsupported_claims`

**Files:**
- Modify: `cfo/claims.py`
- Test: `cfo/tests/test_claims.py`

**Interfaces:**
- Consumes: `grounded_periods`, `_sentences`, `_PERIOD`, `_DISCLAIMER`,
  `_UNAVAIL`, `_OFFER`, `_PHANTOMS` (Tasks 1-2).
- Produces: `unsupported_claims(answer: str, trace: list[dict]) -> list[str]` —
  the de-duplicated issue strings from the three checks (§5 of the spec).

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_claims.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: FAIL — `AttributeError: module 'cfo.claims' has no attribute 'unsupported_claims'`.

- [ ] **Step 3: Write minimal implementation**

Append to `cfo/claims.py`:

```python
def _phantom_hits(low: str) -> list[str]:
    """Shown names of phantom metrics present in a lowercased sentence.
    Longer labels win so 'npl ratio' isn't also reported as bare 'npl'."""
    names: list[str] = []
    matched_spans: list[tuple[int, int]] = []
    for label in sorted(_PHANTOMS, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(label)}\b", low):
            span = (m.start(), m.end())
            if any(s <= span[0] < e for s, e in matched_spans):
                continue
            matched_spans.append(span)
            names.append(_PHANTOMS[label])
    return names


def unsupported_claims(answer: str, trace: list[dict]) -> list[str]:
    grounded = grounded_periods(trace)
    issues: list[str] = []
    for s in _sentences(answer):
        low = s.lower()
        disclaimed = bool(_DISCLAIMER.search(s))
        unavail = bool(_UNAVAIL.search(s))
        offer = bool(_OFFER.search(s))
        # (a) phantom-metric membership
        if not disclaimed:
            for name in _phantom_hits(low):
                issues.append(f"{name} — no tool provides this")
        # (b) + (c) periods
        for p in _PERIOD.findall(s):
            if p in grounded:
                if unavail:
                    issues.append(
                        f"{p} described as unavailable, but a tool returned it")
            elif not (disclaimed or unavail or offer):
                issues.append(f"{p} — no tool has data for this period")
    # de-duplicate, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for i in issues:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    return deduped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_claims.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/claims.py cfo/tests/test_claims.py
git commit -m "feat(cfo): claims — unsupported_claims (phantom + period checks)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: wire the claim channel into `verifier.py`

**Files:**
- Modify: `cfo/verifier.py` (`report`, `revise_prompt`, `badge`)
- Test: `cfo/tests/test_verifier.py` (append)

**Interfaces:**
- Consumes: `claims.unsupported_claims` (Task 3); existing `grounded_values`,
  `claimed_figures`, `_is_grounded`.
- Produces:
  - `report(answer, trace, *, revised) -> dict` now includes
    `"unsupported_claims": list[str]`.
  - `revise_prompt(figures: list[str], claims: list[str] = ()) -> str` — names
    the figures (as before) and, when `claims` is non-empty, the claim issues.
  - `badge(rep)` — clean only when both `ungrounded` and `unsupported_claims`
    are empty.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_verifier.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: FAIL — `KeyError: 'unsupported_claims'` (report) and a `TypeError`/assertion on `revise_prompt`.

- [ ] **Step 3: Write minimal implementation**

In `cfo/verifier.py`, add the import at the top with the others:

```python
from . import claims as _claims
```

Replace `report`:

```python
def report(answer: str, trace: list[dict], *, revised: bool) -> dict:
    grounded = grounded_values(trace)
    g: list[str] = []
    u: list[str] = []
    for f in claimed_figures(answer):
        (g if _is_grounded(f, grounded) else u).append(f.text)
    return {"grounded": g, "ungrounded": u,
            "unsupported_claims": _claims.unsupported_claims(answer, trace),
            "revised": revised}
```

Replace `revise_prompt`:

```python
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
```

Replace `badge`:

```python
def badge(rep: dict) -> str:
    issues = list(rep["ungrounded"]) + list(rep.get("unsupported_claims", []))
    if not issues:
        return "✓ all figures tool-grounded"
    tail = " (after one revision)" if rep["revised"] else ""
    return f"⚠ {len(issues)} issue(s){tail}: {', '.join(issues)}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: PASS (all verifier tests, including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add cfo/verifier.py cfo/tests/test_verifier.py
git commit -m "feat(cfo): verifier — carry the claim channel in report/prompt/badge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: revise on either channel in `ask()` + live smoke

**Files:**
- Modify: `cfo/agent.py` (`ask()`)
- Modify: `cfo/verify-cfo.sh`
- Test: `cfo/tests/test_agent.py` (append)

**Interfaces:**
- Consumes: `verifier.ungrounded`, `verifier.report`, `verifier.revise_prompt`
  (Task 4); `claims.unsupported_claims` (Task 3).
- Produces: `ask()` revises once when the number channel **or** the claim
  channel is non-empty; the nudge names both; `verification` carries
  `unsupported_claims`.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_agent.py`:

```python
class _BadPeriodThenClean:
    """Pass 1 makes a false period-availability claim (no bad number); pass 2
    is clean. Exercises revision driven by the claim channel alone."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.calls == 1:
            text = "NIM for 2026-07 is fine, but 2026-07 may need to be closed first."
        else:
            text = "NIM for 2026-07 is fine; the period is closed and available."
        return {"messages": state["messages"] + [AIMessage(text)]}


def test_ask_revises_on_a_claim_with_no_bad_number():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _BadPeriodThenClean()

    async def _fake_get_tools(settings):
        return []

    # 2026-07 is grounded (list_periods returned it), so calling it
    # "may need to be closed" is a false claim.
    trace = [{"kind": "tool", "name": "list_periods", "input": "{}",
              "output": "['2026-06', '2026-07']"}]

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How's July?", thread_id="t"))

    assert fake.calls == 2                                # revised once
    assert out["verification"]["revised"] is True
    assert out["verification"]["unsupported_claims"] == []   # clean after
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_agent.py -q`
Expected: FAIL — `fake.calls == 1` (ask does not yet revise on claims) and/or `KeyError: 'unsupported_claims'`.

- [ ] **Step 3: Write minimal implementation**

In `cfo/agent.py`, add the import with the other local imports (after `from . import verifier`):

```python
from . import claims
```

Replace the revise block and return (from `revised = False` to the end of `ask()`) with:

```python
    revised = False
    figs = verifier.ungrounded(answer, rec.events())
    clms = claims.unsupported_claims(answer, rec.events())
    if figs or clms:
        revised = True
        nudge = verifier.revise_prompt(figs, clms)
        out = await agent.ainvoke({"messages": [HumanMessage(nudge)]},
                                  config=cfg)
        answer = _last_ai_text(out)

    return {"answer": answer, "thread_id": thread_id, "trace": rec.events(),
            "verification": verifier.report(answer, rec.events(),
                                            revised=revised)}
```

(The old block computed `verifier.ungrounded(...)` twice and only checked
figures; this computes each channel once and revises on either.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_agent.py -q`
Expected: PASS (all agent tests).

- [ ] **Step 5: Add the live-smoke assertion**

In `cfo/verify-cfo.sh`, the premise-refusal block already captures `$PUSHBACK`
from the NPL question. Immediately after its existing `grep` assertion (the line
ending `... premise its tools cannot verify"; exit 1; }`), add a check that the
same answer is claim-clean — the honest "I can't see NPL" must NOT be flagged:

```bash
# The honest NPL decline must not itself be flagged as an unsupported claim
# (disclaimer guard), verified end to end.
echo "== NPL decline is not flagged as an unsupported claim =="
CLAIMS=$(curl -fsS -XPOST "$CFO/ask" -H 'content-type: application/json' \
  -d '{"message":"Our 3% NPL ratio worries me — what is driving it?"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["verification"]["unsupported_claims"])')
echo "unsupported_claims: $CLAIMS"
[ "$CLAIMS" = "[]" ] \
  || { echo "FAIL: honest NPL decline was flagged as an unsupported claim"; exit 1; }
```

- [ ] **Step 6: Run the full unit suite**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest finance/tests/ cfo/tests/ -q`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add cfo/agent.py cfo/verify-cfo.sh cfo/tests/test_agent.py
git commit -m "feat(cfo): revise on unsupported claims, not just ungrounded figures

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §2 `cfo/claims.py` pure module → Tasks 1-3.
- §3 vocabulary (period regex, phantom labels) → Tasks 1-2.
- §4 grounded periods → Task 1.
- §5(a) phantom membership, §5(b) grounded-period predicate, §5(c) fabricated
  period + offer guard → Task 3.
- §6 integration (`report`/`revise_prompt`/`badge`) → Task 4; `ask()` revise on
  either → Task 5.
- §7 testing → unit (Tasks 1-3), verifier (Task 4), agent (Task 5), live smoke
  (Task 5).
- §8 scope (periods + phantoms only, disclaimer/offer aware) → honored; no role
  or tool-metric vocabulary is introduced.

**Placeholder scan:** none — every code/test step is complete; every run step
names the command and expected result.

**Type consistency:** `grounded_periods -> set[str]` (Task 1) consumed by
`unsupported_claims` (Task 3). `unsupported_claims(answer, trace) -> list[str]`
(Task 3) consumed by `report` and `ask` (Tasks 4-5). `report` returns
`{grounded, ungrounded, unsupported_claims, revised}` (Task 4), consumed
identically by `badge` (Task 4), `ask` (Task 5), and the console. `revise_prompt(
figures, claims=())` (Task 4) called with one arg by existing number tests and
two args by `ask` (Task 5). `_PERIOD`, `_DISCLAIMER`, `_UNAVAIL`, `_OFFER`,
`_PHANTOMS`, `_sentences`, `_phantom_hits` names are consistent across Tasks 1-3.
