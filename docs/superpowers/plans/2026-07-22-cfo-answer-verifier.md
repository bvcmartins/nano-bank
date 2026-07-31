# CFO Answer Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic verifier that flags any money/ratio figure in the CFO's answer that did not come from a tool result, and re-prompts the agent once to ground or own it.

**Architecture:** A new pure module `cfo/verifier.py` (no I/O) extracts the numbers from the run's tool outputs (the *grounded* set) and the money/ratio figures from the answer prose (the *claimed* set), and reports any claimed figure matching no grounded number within rounding. `cfo/agent.py::ask()` runs it after the agent answers; if anything is ungrounded it appends one revise message on the same thread and re-verifies. `cfo/trace.py` is changed to store tool outputs untruncated so the grounded set is complete. The console shows a verification badge. No LLM in the verifier.

**Tech Stack:** Python 3.12, `Decimal` for all number comparison, `re` for extraction, pytest. Runs under the existing finance venv (`/home/bmartins/dev/nano-bank/finance/.venv`); source is imported from the current working directory, so run pytest from the repo/worktree root.

## Global Constraints

- All money/number comparison uses `decimal.Decimal` — never `float`. (Copied from the CFO Phase-1 constraint: "Decimal for all money".)
- The verifier contains **no LLM call** and no network/DB I/O — it is a pure function of `(answer_text, trace_events)`.
- Prose extraction polices **money and ratio figures only** (`$`-prefixed, `%`-suffixed, or comma-grouped / ≥2-decimal numbers). Bare integers (years, counts, day counts) are exempt.
- Percent figures match **both** their face value and their ÷100 ratio form, because tools store ratios (`0.2131`) and prose states percents (`21.31%`).
- Unicode minus `−` (U+2212) may appear in model output; normalise it to ASCII `-` before parsing.
- Exactly **one** revise retry. No N-retry loop.
- Test runner: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest` invoked from the repo/worktree root.
- Commit message trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- `cfo/verifier.py` — **new.** Pure verifier: number extraction, matching, ungrounded classification, report, revise-prompt, badge.
- `cfo/trace.py` — **modify.** `on_tool_end` stores the tool output untruncated.
- `cfo/agent.py` — **modify.** `ask()` runs the verifier and the one-retry loop; return dict gains `verification`.
- `cfo/console.py` — **modify.** Render the verification badge under the answer.
- `cfo/verify-cfo.sh` — **modify.** Assert the live `/ask` response carries a `verification` block.
- `cfo/tests/test_verifier.py` — **new.** Unit tests for the pure verifier.
- `cfo/tests/test_trace.py` — **new.** Unit test for the untruncated-output change.
- `cfo/tests/test_agent.py` — **modify.** Test the one-retry loop with a mocked agent.

---

## Task 1: Trace stores full tool output

**Files:**
- Modify: `cfo/trace.py` (the `on_tool_end` method, ~line 27)
- Test: `cfo/tests/test_trace.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `TraceRecorder` events whose `output` field holds the **complete** tool output string (no 2000-char cap). Model events are unchanged (`output` stays `None`). Event shape is unchanged otherwise: `{"seq", "kind", "name", "ok", "elapsed_ms", "input", "output", "error"}`.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_trace.py`:

```python
from cfo.trace import TraceRecorder


def test_tool_output_is_stored_untruncated():
    """The verifier reads tool outputs to build its grounded set; a truncated
    financial_health bundle would drop numbers and cause false 'ungrounded'."""
    rec = TraceRecorder()
    big = "x" * 5000 + "END_MARKER"
    rec.on_tool_start({"name": "financial_health"}, "{}", run_id="r1")
    rec.on_tool_end(big, run_id="r1")
    ev = rec.events()[0]
    assert ev["kind"] == "tool"
    assert ev["output"].endswith("END_MARKER")
    assert len(ev["output"]) >= 5000


def test_tool_input_is_still_bounded():
    """Only the output cap is lifted; the input field keeps its short cap."""
    rec = TraceRecorder()
    rec.on_tool_start({"name": "raroc"}, "y" * 5000, run_id="r2")
    rec.on_tool_end("ok", run_id="r2")
    ev = rec.events()[0]
    assert len(ev["input"]) <= 2100  # 2000 + ellipsis slack
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_trace.py -q`
Expected: FAIL — `test_tool_output_is_stored_untruncated` fails because `_short` truncates the output to ~2000 chars, so `len(ev["output"])` is < 5000.

- [ ] **Step 3: Write minimal implementation**

In `cfo/trace.py`, change `on_tool_end` to store the raw output untruncated. Replace:

```python
    def on_tool_end(self, output, **kwargs):
        self._close(kwargs.get("run_id"), ok=True, output=_short(output))
```

with:

```python
    def on_tool_end(self, output, **kwargs):
        # Full output, not _short: the verifier parses these numbers to build
        # its grounded set, and a truncated bundle would drop figures and
        # produce false "ungrounded" flags. Tool outputs are bounded (a few KB).
        text = output if isinstance(output, str) else str(output)
        self._close(kwargs.get("run_id"), ok=True, output=text)
```

Leave `on_tool_start` (which uses `_short` on `input`) and every other method unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_trace.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/trace.py cfo/tests/test_trace.py
git commit -m "feat(cfo): store full tool output in the trace for verification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Grounded number extraction

**Files:**
- Create: `cfo/verifier.py`
- Test: `cfo/tests/test_verifier.py` (create)

**Interfaces:**
- Consumes: trace events from Task 1 (`kind`, `output` fields).
- Produces: `grounded_values(trace: list[dict]) -> list[Decimal]` — every numeric literal found in the `output` of every `kind == "tool"` event, as `Decimal`s. Also the private helpers `_NUM` (compiled regex) and `_to_decimal(raw: str) -> Decimal | None` that later tasks reuse.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_verifier.py`:

```python
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


def test_grounded_values_ignores_model_events_and_empty_output():
    trace = [
        {"kind": "model", "name": "model", "output": None},
        {"kind": "tool", "name": "t", "output": ""},
    ]
    assert verifier.grounded_values(trace) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cfo.verifier'`.

- [ ] **Step 3: Write minimal implementation**

Create `cfo/verifier.py`:

```python
"""Deterministic answer verifier for the Agent CFO.

Every money/ratio figure the CFO states must trace to a number some tool
returned this turn. The trace of tool outputs is the oracle; there is no LLM
here. See docs/superpowers/specs/2026-07-22-cfo-answer-verifier-design.md.
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

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
        raw = ev.get("output")
        if not raw:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        for m in _NUM.findall(text.replace("−", "-")):
            d = _to_decimal(m)
            if d is not None:
                out.append(d)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/verifier.py cfo/tests/test_verifier.py
git commit -m "feat(cfo): verifier — grounded number set from tool outputs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Prose figure extraction

**Files:**
- Modify: `cfo/verifier.py`
- Test: `cfo/tests/test_verifier.py`

**Interfaces:**
- Consumes: `_to_decimal` from Task 2.
- Produces:
  - `class Figure` with attributes `text: str` (the raw matched string), `value: Decimal` (parsed, sign-normalised, `%`/`$`/`,` stripped), `is_percent: bool`, `decimals: int` (digits after the decimal point in `text`, 0 if none).
  - `claimed_figures(answer: str) -> list[Figure]` — the money, percent, and comma-grouped/≥2-decimal figures in the prose, in order. Bare integers are **not** returned.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_verifier.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: FAIL — `AttributeError: module 'cfo.verifier' has no attribute 'claimed_figures'`.

- [ ] **Step 3: Write minimal implementation**

Append to `cfo/verifier.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/verifier.py cfo/tests/test_verifier.py
git commit -m "feat(cfo): verifier — extract money/ratio figures from prose

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Matching and `ungrounded()`

**Files:**
- Modify: `cfo/verifier.py`
- Test: `cfo/tests/test_verifier.py`

**Interfaces:**
- Consumes: `grounded_values` (Task 2), `claimed_figures` / `Figure` (Task 3).
- Produces: `ungrounded(answer: str, trace: list[dict]) -> list[str]` — the raw `text` of each claimed figure that matches no grounded value. Also the private helpers `_close(grounded: Decimal, target: Decimal, decimals: int) -> bool` and `_is_grounded(fig: Figure, grounded: list[Decimal]) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_verifier.py`:

```python
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


def test_currency_matches_after_separator_strip():
    trace = _trace("{'total_assets': '815636.08'}")
    assert verifier.ungrounded("Total assets $815,636.08.", trace) == []


def test_grounded_and_ungrounded_together():
    trace = _trace("{'roe': '0.2131', 'net_income': '1448.08'}")
    ans = "ROE 21.31% on $1,448.08, and an invented 42.0% efficiency."
    assert verifier.ungrounded(ans, trace) == ["42.0%"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: FAIL — `AttributeError: module 'cfo.verifier' has no attribute 'ungrounded'`.

- [ ] **Step 3: Write minimal implementation**

Append to `cfo/verifier.py`:

```python
def _close(grounded: Decimal, target: Decimal, decimals: int) -> bool:
    """True if a grounded value equals the target at the figure's displayed
    precision. Tolerance is half of the last shown decimal place, OR 0.1% of
    the target for large rounded figures — whichever is larger."""
    place = Decimal(5) * (Decimal(10) ** -(decimals + 1))   # half last digit
    rel = abs(target) * Decimal("0.001")                    # 0.1% presentation
    tol = place if place > rel else rel
    return abs(grounded - target) <= tol


def _is_grounded(fig: Figure, grounded: list[Decimal]) -> bool:
    targets = [fig.value]
    if fig.is_percent:
        # tools store the ratio; prose shows the percent
        targets.append(fig.value / Decimal(100))
    for t in targets:
        for g in grounded:
            if _close(g, t, fig.decimals):
                return True
    return False


def ungrounded(answer: str, trace: list[dict]) -> list[str]:
    """The prose figures that match no number any tool returned this turn."""
    grounded = grounded_values(trace)
    return [f.text for f in claimed_figures(answer)
            if not _is_grounded(f, grounded)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/verifier.py cfo/tests/test_verifier.py
git commit -m "feat(cfo): verifier — match prose figures against grounded set

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Report, revise-prompt, and badge

**Files:**
- Modify: `cfo/verifier.py`
- Test: `cfo/tests/test_verifier.py`

**Interfaces:**
- Consumes: `claimed_figures`, `ungrounded` (Tasks 3–4).
- Produces:
  - `report(answer: str, trace: list[dict], *, revised: bool) -> dict` → `{"grounded": list[str], "ungrounded": list[str], "revised": bool}` where `grounded` is the raw text of figures that matched and `ungrounded` those that did not.
  - `revise_prompt(figures: list[str]) -> str` — the message appended to the thread when figures are ungrounded.
  - `badge(rep: dict) -> str` — a one-line status string for the console.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_verifier.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: FAIL — `AttributeError: module 'cfo.verifier' has no attribute 'report'`.

- [ ] **Step 3: Write minimal implementation**

Append to `cfo/verifier.py`:

```python
def report(answer: str, trace: list[dict], *, revised: bool) -> dict:
    grounded = grounded_values(trace)
    g: list[str] = []
    u: list[str] = []
    for f in claimed_figures(answer):
        (g if _is_grounded(f, grounded) else u).append(f.text)
    return {"grounded": g, "ungrounded": u, "revised": revised}


def revise_prompt(figures: list[str]) -> str:
    joined = ", ".join(figures)
    return (
        "Verification found figures in your answer that are not grounded in "
        f"any tool result from this turn: {joined}. For each one, either "
        "recompute it by calling the appropriate tool, or state plainly that "
        "it is your own estimate rather than a tool figure. Then give the "
        "corrected answer.")


def badge(rep: dict) -> str:
    if not rep["ungrounded"]:
        return "✓ all figures tool-grounded"
    figs = ", ".join(rep["ungrounded"])
    tail = " (after one revision)" if rep["revised"] else ""
    return f"⚠ {len(rep['ungrounded'])} figure(s) ungrounded{tail}: {figs}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_verifier.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/verifier.py cfo/tests/test_verifier.py
git commit -m "feat(cfo): verifier — report, revise-prompt, and badge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Wire the one-retry loop into `ask()`

**Files:**
- Modify: `cfo/agent.py` (`ask()`, lines 61-77)
- Modify: `cfo/verify-cfo.sh` (add a `verification`-block assertion)
- Test: `cfo/tests/test_agent.py` (append)

**Interfaces:**
- Consumes: `report`, `ungrounded`, `revise_prompt` from Task 5; `TraceRecorder` (Task 1).
- Produces: `ask()` return dict gains `verification` (the `report(...)` dict). When pass 1 leaves figures ungrounded, `ask()` appends one `revise_prompt` message on the same thread, re-invokes, and re-verifies; the returned `trace` covers both passes (same `TraceRecorder`), and `verification.revised` is `True`.

- [ ] **Step 1: Write the failing test**

Append to `cfo/tests/test_agent.py` (note the existing file already imports `asyncio`, `patch`, `AIMessage`, `Settings`, and `cfo_agent`):

```python
class _TwoPassAgent:
    """Pass 1 returns an ungrounded figure; pass 2 (after the revise message)
    returns a clean, grounded answer. Records how many times it was invoked."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.calls == 1:
            text = "Net income $1,448.08, and an invented loss of -$7,652.00."
        else:
            text = "Corrected: net income $1,448.08 (my estimate: none)."
        return {"messages": state["messages"] + [AIMessage(text)]}


def test_ask_revises_once_when_a_figure_is_ungrounded():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _TwoPassAgent()

    async def _fake_get_tools(settings):
        return []

    # The grounded set comes from the trace; stub it so 1448.08 is grounded and
    # 7652 is not, regardless of what the fake agent "called".
    trace = [{"kind": "tool", "name": "income_statement",
              "output": "{'net_income': '1448.08'}"}]

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How did we do?", thread_id="t"))

    assert fake.calls == 2                       # revised exactly once
    assert out["verification"]["revised"] is True
    assert "$1,448.08" in out["answer"]


def test_ask_does_not_revise_when_all_grounded():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    fake = _TwoPassAgent()

    async def _fake_get_tools(settings):
        return []

    trace = [{"kind": "tool", "name": "income_statement",
              "output": "{'net_income': '1448.08'}"}]

    # Pass-1 answer here contains only grounded figures.
    async def _one_pass(state, config=None):
        fake.calls += 1
        return {"messages": state["messages"] +
                [AIMessage("Net income was $1,448.08.")]}
    fake.ainvoke = _one_pass

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=fake), \
         patch.object(cfo_agent.mf, "llm", return_value=object()), \
         patch.object(cfo_agent.TraceRecorder, "events", lambda self: trace):
        out = asyncio.run(cfo_agent.ask(s, "How did we do?", thread_id="t"))

    assert fake.calls == 1                       # no revision
    assert out["verification"]["revised"] is False
    assert out["verification"]["ungrounded"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_agent.py -q`
Expected: FAIL — `KeyError: 'verification'` (and `fake.calls` is 1, not 2) because `ask()` does not yet verify or revise.

- [ ] **Step 3: Write minimal implementation**

In `cfo/agent.py`, add the import near the other local imports (after `from .trace import TraceRecorder`):

```python
from . import verifier
```

Replace the body of `ask()` (everything from `out = await agent.ainvoke(` to the `return` statement) with:

```python
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40,
           "callbacks": [rec]}

    def _last_ai_text(state) -> str:
        for m in reversed(state["messages"]):
            if isinstance(m, AIMessage) and (m.content or "").strip():
                return m.content
        return "(no answer)"

    out = await agent.ainvoke({"messages": [HumanMessage(message)]}, config=cfg)
    answer = _last_ai_text(out)

    # One revise pass: if a figure isn't grounded in a tool result this turn,
    # ask the agent (same thread, so it keeps context and can call more tools)
    # to ground it or own it as an estimate. Exactly one retry.
    revised = False
    if verifier.ungrounded(answer, rec.events()):
        revised = True
        nudge = verifier.revise_prompt(verifier.ungrounded(answer, rec.events()))
        out = await agent.ainvoke({"messages": [HumanMessage(nudge)]},
                                  config=cfg)
        answer = _last_ai_text(out)

    return {"answer": answer, "thread_id": thread_id, "trace": rec.events(),
            "verification": verifier.report(answer, rec.events(),
                                            revised=revised)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest cfo/tests/test_agent.py -q`
Expected: PASS (all agent tests, including the two new ones).

- [ ] **Step 5: Add the live-smoke assertion**

In `cfo/verify-cfo.sh`, immediately before the final `echo "CFO SMOKE PASSED"`, insert:

```bash
# The response must carry a verification block, and the health question's
# figures should all be tool-grounded (empty ungrounded list).
echo "== verification block present and clean =="
VERI=$(curl -fsS -XPOST "$CFO/ask" -H 'content-type: application/json' \
  -d "{\"message\":\"Give me the key ratios for $PERIOD with the numbers.\"}" \
  | python -c 'import sys,json; d=json.load(sys.stdin); v=d["verification"]; \
print("REVISED", v["revised"], "UNGROUNDED", v["ungrounded"])')
echo "$VERI"
echo "$VERI" | grep -q "UNGROUNDED \[\]" \
  || { echo "FAIL: key-ratios answer has ungrounded figures"; exit 1; }
```

- [ ] **Step 6: Run the unit suite to confirm nothing regressed**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -m pytest finance/tests/ cfo/tests/ -q`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add cfo/agent.py cfo/verify-cfo.sh cfo/tests/test_agent.py
git commit -m "feat(cfo): verify answers and revise once on ungrounded figures

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Console verification badge

**Files:**
- Modify: `cfo/console.py`

**Interfaces:**
- Consumes: `verifier.badge` (Task 5); the `verification` field on the `/ask` response (Task 6).
- Produces: the console renders the badge under each assistant answer. No new public interface.

- [ ] **Step 1: Render the badge**

`cfo/console.py` has no unit tests (it is a Streamlit script); this task is verified live. In `cfo/console.py`, add the import at the top with the others:

```python
from cfo.verifier import badge
```

Then, in the assistant block, after `answer = data.get("answer", "(no answer)")` and before `st.markdown(answer)`, capture the verification field; and after `st.markdown(answer)` render the badge. The assistant `with` block becomes:

```python
    with st.chat_message("assistant"):
        veri = None
        try:
            r = httpx.post(f"{API}/ask",
                           json={"message": prompt,
                                 "thread_id": st.session_state.thread_id},
                           timeout=600)
            r.raise_for_status()
            data = r.json()
            st.session_state.thread_id = data.get("thread_id")
            answer = data.get("answer", "(no answer)")
            veri = data.get("verification")
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ CFO unreachable: {e}"
        st.markdown(answer)
        if veri is not None:
            line = badge(veri)
            if veri.get("ungrounded"):
                st.warning(line)
            else:
                st.caption(line)
        st.session_state.history.append(("assistant", answer))
```

- [ ] **Step 2: Verify import resolves**

Run: `/home/bmartins/dev/nano-bank/finance/.venv/bin/python -c "import ast; ast.parse(open('cfo/console.py').read()); from cfo.verifier import badge; print('ok')"`
Expected: prints `ok` (syntax valid, `badge` importable).

- [ ] **Step 3: Live check (manual)**

With the demo stack running (`bash cfo/demo/run-cfo-stack.sh`), open `http://localhost:8506`, ask "What are the key ratios for the latest period?", and confirm a `✓ all figures tool-grounded` caption renders under the answer.

- [ ] **Step 4: Commit**

```bash
git add cfo/console.py
git commit -m "feat(cfo): show the verification badge in the console

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §2 pure module `cfo/verifier.py` → Tasks 2–5.
- §3 grounded set → Task 2; prose money/ratio extraction + integer exemption → Task 3; matching with rounding + percent both-forms → Task 4.
- §4 trace untruncated → Task 1.
- §5 one-retry loop → Task 6.
- §6 `verification` in the response → Task 6; console badge → Task 7.
- §7 testing → unit tests in Tasks 2–5, agent test in Task 6, live smoke in Task 6, console live check in Task 7.
- §8 scope (one retry, money+ratio only, no LLM, no arithmetic-closure) → honored across Tasks 3–6.

**Placeholder scan:** none — every code and test step contains complete code; every run step names the command and expected result.

**Type consistency:** `Figure(text, value, is_percent, decimals)` defined in Task 3 is used unchanged in Tasks 4–5. `report()` returns `{grounded, ungrounded, revised}` in Task 5, consumed identically by `badge()` (Task 5), `ask()` (Task 6), and the console (Task 7). `ungrounded()` returns `list[str]` in Task 4, consumed by `revise_prompt()` (Task 5) and `ask()` (Task 6). `grounded_values()` / `_to_decimal` / `_is_grounded` / `_close` names are consistent across tasks.
