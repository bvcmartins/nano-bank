# Design: CFO Answer Verifier

**Status:** Approved for planning
**Date:** 2026-07-22
**Author:** nano-bank team
**Scope:** A reliability layer for the Agent CFO — grounds every figure it
states in a tool result, and revises the answer once when a figure is not.

## 1. Context and goals

The Agent CFO (`cfo/`) is a read-mostly financial-officer agent on GLM-5.2 that
answers questions about nano-bank's finances by calling the finance MCP tools
(`raroc`, `key_ratios`, `nim`, `provision_scenario`, …). Every defect found in
it so far has the same shape: **the model was right about arithmetic but wrong
about what to compute.** It netted an annual expected loss against a monthly
net income and reported a fake `$7,652` loss; it dropped annualisation in a
hypothetical and reported ROE `11×` too small; it hand-divided a `1.8%` loss
rate; it improvised a `42%` efficiency ratio with the wrong denominator,
contradicting its own `key_ratios` tool.

The pattern behind all of them: a **number appeared in the prose that came out
of no tool.** The model computed it in its head. The fixes so far have been to
build a named, unit-labelled tool for each case as a probe exposes it. That
works but never exhausts the long tail of questions we did not anticipate.

This spec adds a general backstop that does not depend on anticipating the
question: a **verifier** that checks every money/ratio figure in the CFO's
answer against the numbers its tools actually returned that turn, and — when a
figure is ungrounded — re-prompts the agent once to ground it or own it as an
estimate.

The principle is the one that makes Claude Code reliable: **execution output is
ground truth; the model's mental math is not.** The trace of tool results *is*
the oracle.

### Non-goals

- **Not an accuracy oracle for formulas.** The verifier confirms a stated
  figure came from a tool, not that the *right* tool was chosen. Choosing the
  wrong metric is a separate problem addressed by building better-scoped tools.
- **No LLM inside the verifier.** A model judging a model's arithmetic has
  correlated errors — it is a second opinion from the same doctor. The verifier
  is fully deterministic.
- **No arithmetic-closure in v1.** A correct delta of two grounded numbers
  (e.g. a `+6.28 pp` month-over-month change) is *not* itself in any tool
  output and will flag. That is deliberate: the flag is the signal to either
  build a tool for it (`compare_periods`) or label it an estimate. Allowing
  sums/differences of grounded numbers is a possible v2.
- **Not a policy on bare integers.** Years (`2026`), counts (`16 roles`), and
  day counts are exempt (see §3). The bug class has always been a `$` or `%`
  figure.
- **Multi-agent / reviewer-of-subagents is out of scope.** This verifier is the
  single-agent embodiment of that idea; it can later become the reviewer layer
  of a subagent team when requirement 3 (C-suite meetings) is built.

## 2. Architecture

A new pure module `cfo/verifier.py` with no I/O, invoked from
`cfo/agent.py::ask()` as a post-processing step over the `TraceRecorder`
events. Keeping it pure and separate means it is unit-testable in isolation and
the agent itself stays unaware of it.

```
ask(message)
  ├─ invoke agent (pass 1)  ──────────────► answer_1, trace_1
  ├─ verify(answer_1, trace_1) ───────────► ungrounded: list[Figure]
  │      grounded set  = numbers in tool outputs (trace)
  │      claimed set   = money/ratio figures in answer prose
  │      ungrounded    = claimed figures matching no grounded number
  ├─ if ungrounded == []:  return answer_1 + report(revised=False)
  └─ else (one retry):
        append HumanMessage(revise prompt naming the ungrounded figures)
        invoke agent (pass 2, same thread) ► answer_2, trace_2
        verify(answer_2, trace_1 ∪ trace_2) ► ungrounded_2
        return answer_2 + report(revised=True, ungrounded=ungrounded_2)
```

### Components

- **`cfo/verifier.py`** — the pure check. Public surface:
  - `grounded_numbers(trace: list[dict]) -> list[Decimal]` — every number
    parsed out of tool `output` strings in the trace.
  - `claimed_figures(answer: str) -> list[Figure]` — money/ratio figures in the
    prose, each carrying its raw text, normalised value(s), and displayed
    precision.
  - `ungrounded(answer: str, trace: list[dict], *, tol) -> list[Figure]` — the
    claimed figures that match no grounded number within rounding.
  - `report(answer, trace, revised) -> dict` — `{grounded, ungrounded, revised}`
    for the response payload.
  - `revise_prompt(figures) -> str` — the re-prompt text.
- **`cfo/trace.py`** — `on_tool_end` changed to store the tool output
  **untruncated** (see §4). Model-output truncation is unchanged.
- **`cfo/agent.py::ask()`** — orchestrates the one-retry loop and adds
  `verification` to its return dict.
- **`cfo/console.py`** — renders a verification badge from the report.

## 3. The check in detail

### Grounded set
Tool outputs arrive as stringified dicts/JSON in each trace event's `output`.
The verifier regex-extracts every numeric literal from those strings into a set
of `Decimal`s. Structural parsing is unnecessary — a number is grounded if it
*appears*, regardless of which field it sat in.

### Claimed set — money and ratios only
From the answer prose, extract:
- **currency**: `$1,448.08`, `$400,000`, `-$2,551.92` → strip `$`, `,`, sign
  handling preserved.
- **percentages**: `21.31%`, `6.28%`, `-3.70%`.
- **formatted decimals**: numbers with a thousands separator or ≥2 decimal
  places (`1,448.08`), to catch figures stated without a `$`.

**Exempt** (not checked): bare integers with no `$`/`%`/separator — years,
counts, day counts. Rationale: every defect to date was a `$` or `%` figure;
policing bare integers is pure false-positive surface.

### Matching rule
A claimed figure `c` is grounded if some grounded number `g` satisfies the
match after normalisation:
- Round both to `c`'s displayed decimal precision, compare for equality; OR
- allow an absolute/relative tolerance `tol` for rounding drift
  (`0.213108…` in a tool ↔ `21.31%` or `21.3%` in prose).
- **Percentages match both forms**: a prose `p%` is grounded if the grounded
  set contains a value matching `p` *or* `p / 100` — tools store ratios
  (`0.2131`), prose states percents (`21.31%`).

Return every claimed figure with no matching `g`.

## 4. Trace change (required)

`TraceRecorder.on_tool_end` currently records `_short(output)` (truncated at
2000 chars). A truncated `financial_health` bundle would drop numbers, making a
correctly-grounded figure look ungrounded — a false positive that would trigger
a needless revise. Tool outputs must be stored **in full** for verification.
Model-start/LLM-end events are unaffected (they store no output). The `input`
fields and the 2000-char cap on model-facing fields stay as they are.

## 5. Revise loop

Exactly one retry, in `ask()`:

1. Pass 1 produces `answer_1`; verify against `trace_1`.
2. If nothing is ungrounded, return `answer_1` with `revised=False`.
3. Otherwise append a single `HumanMessage` to the *same thread* (so the agent
   keeps its context and can call more tools):

   > Verification found figures not grounded in any tool result this turn:
   > `<list>`. For each, either recompute it by calling a tool, or state
   > plainly that it is your own estimate. Then give the corrected answer.

4. Pass 2 produces `answer_2`; verify against `trace_1 ∪ trace_2`.
5. Return `answer_2` with `revised=True`. Anything still ungrounded is reported
   (by now it should be figures the agent has explicitly labelled as estimates).

The returned `trace` covers both passes. No second retry — the loop is bounded
to keep latency predictable on an already-slow backend.

## 6. Response shape

`ask()` return dict gains:

```json
"verification": {
  "grounded":   ["$1,448.08", "21.31%", "6.28%"],
  "ungrounded": [],
  "revised":    false
}
```

The console shows `✓ all figures tool-grounded` or `⚠ N ungrounded:
<figures>`.

## 7. Testing

**Unit (`cfo/tests/test_verifier.py`, pure, no network):**
- extracts `$` and `%` figures and formatted decimals from prose
- ignores bare integers, years, counts
- matches within rounding: `21.31%` ↔ `0.213108`, `21.3%` ↔ `0.2131`
- percentage both-forms: prose `p%` grounds against `p` or `p/100`
- thousands separators: `$1,448.08` ↔ `1448.08`
- flags a fabricated figure absent from every tool output (the `$7,652` case)
- builds the grounded set from realistic stringified tool outputs, including a
  full `financial_health`-shaped bundle (guards the truncation fix)

**Agent (`cfo/tests/test_agent.py`, mocked model):**
- ungrounded figure in pass 1 → `ask()` re-invokes → clean pass 2; assert
  `verification.revised is True` and the report lists the offending figure
- grounded pass 1 → no re-invoke; assert `revised is False`

**Live smoke (`cfo/verify-cfo.sh`):**
- re-run an existing question and assert the response carries a
  `verification` block with `ungrounded == []`

## 8. Scope summary

In: the pure verifier, the trace change, the one-retry loop, the report, the
console badge, the tests above. Out: LLM-based checking, arithmetic-closure,
bare-integer policing, N-retry, and any multi-agent structure. The verifier is
designed so that promoting it to the reviewer layer of a subagent team later is
additive, not a rewrite.
