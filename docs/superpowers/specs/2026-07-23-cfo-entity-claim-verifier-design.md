# Design: CFO Entity/Claim Verifier

**Status:** Approved for planning
**Date:** 2026-07-23
**Author:** nano-bank team
**Scope:** Extend the CFO answer verifier from grounding *numbers* to grounding
*period and phantom-metric claims* — catching phantom entities (LCR/NPL/…),
fabricated periods, and the "available period described as unavailable" bug.

## 1. Context and goals

The CFO answer verifier (spec `2026-07-22-cfo-answer-verifier-design.md`) grounds
every money/ratio **figure** in the CFO's answer against the numbers its tools
returned that turn. A fabricated number gets flagged and the agent revises once.

Grounding *numbers* leaves a gap. Asked about NIM, the CFO wrote:

> "`list_periods` shows only **2026-06** … 2026-07 may need to be closed first."

`list_periods` had returned both `2026-06` and `2026-07`, and the CFO had just
read `2026-07`'s NIM in the same answer. A false **claim**, not a false number —
and the number verifier passed it clean.

This spec extends grounding to **claims**: which periods exist / are available,
and phantom metrics no tool provides.

### What is genuinely new (and what isn't)

The number verifier **already** catches fabricated entities that arrive *with a
number*: "LCR is 95%", "3% NPL", the "$7,652 loss" were flagged on the number.
The new coverage is claims **without** a number: a period-availability predicate
("2026-07 needs closing"), a fabricated period asserted as real, and a phantom
metric asserted qualitatively ("our liquidity looks weak").

### Why only periods and phantom metrics

The membership check flags an entity the answer asserts that **no tool
surfaced**. That is sound only where "not surfaced ⇒ fabricated":

- **Phantom metrics** (LCR/NSFR/NPL): no tool *ever* provides them, so an
  affirmative claim is always a fabrication. **In.**
- **Periods** are open-ended (`YYYY-MM`): a period no tool surfaced, asserted as
  real closed data, is a genuine fabrication. **In** (with an offer guard, §5c).
- **Account roles** (`LoansReceivable`) and **tool-provided metrics** (`roe`)
  come from a finite set of *real* things. "Not fetched this turn" ≠ fabricated
   — the CFO may mention a role conceptually — so membership there has no true
  positives, only false ones; and any such entity stated *with a value* is
  already caught by the number verifier. **Out.**

### The false-positive that shapes the design

The CFO **must be able to name entities it is declining** — the premise-refusal
work exists so it says "I cannot see an LCR." So the check separates an
**affirmative** claim ("our LCR is weak") from a **disclaimer** ("I can't see an
LCR"), and a fabricated-period assertion from an **offer** ("I can close 2026-08
for you"). This is cue-based predicate detection: deterministic, no LLM, but
heuristic.

### Non-goals

- **No LLM in the verifier.** A model judging a model's claims has correlated
  errors.
- **No role or tool-provided-metric grounding** (see above — only false
  positives).
- **No tool-name or loose-synonym grounding** ("the loan book", "per the RAROC
  tool") — fragile, low value.
- **Not a general claim checker.** Two entity families only: periods and phantom
  metrics.

## 2. Architecture

A new pure module `cfo/claims.py` holds the phantom-metric vocabulary, the
grounded-period builder, and the cue classifiers. `cfo/verifier.py` stays
focused on numbers and becomes the **aggregator**: `report()` now returns
number-grounding *and* the claim channel. `ask()`'s one-retry loop fires when
**either** channel has an issue.

```
ask(message)
  ├─ invoke agent (pass 1) ─────────────► answer_1, trace
  ├─ figs  = verifier.ungrounded(answer_1, trace)       # numbers
  ├─ clms  = claims.unsupported_claims(answer_1, trace)  # periods + phantoms
  ├─ if figs or clms:  (one retry)
  │     revise message names BOTH the ungrounded figures and the claims
  │     invoke agent (pass 2, same thread) ─► answer_2
  └─ return answer_2 + verifier.report(...)  # {grounded, ungrounded,
                                             #  unsupported_claims, revised}
```

### Components

- **`cfo/claims.py`** — new, pure:
  - `grounded_periods(trace) -> set[str]` — `YYYY-MM` tokens from `list_periods`
    outputs and from tool inputs (a tool called with `period=X` that returned a
    result proves `X` is available).
  - `unsupported_claims(answer, trace) -> list[str]` — human-readable issue
    strings from the three checks in §5.
  - private: `_sentences(text)`, and the cue regexes `_DISCLAIMER`, `_UNAVAIL`,
    `_OFFER`, `_PERIOD`, `_PHANTOMS`.
- **`cfo/verifier.py`** — `report()` gains `unsupported_claims`;
  `revise_prompt(figures, claims=())` gains the claims argument (default empty
  keeps existing callers/tests working); `badge()` reflects claims.
- **`cfo/agent.py`** — `ask()` computes both channels, revises on either, builds
  the combined nudge.
- **`cfo/console.py`** — badge already read from the report; no change beyond the
  extended `badge()`.

## 3. Vocabulary

- **Periods**: `_PERIOD = \b20\d{2}-(?:0[1-9]|1[0-2])\b` (a `YYYY-MM`).
- **Phantom metrics** `_PHANTOMS` (label → shown name), matched case-insensitive,
  word-boundaried:
  - `lcr`, `liquidity coverage ratio`
  - `nsfr`, `net stable funding ratio`
  - `npl ratio`, `npl`, `non-performing loan`, `non performing loan`

No role or tool-metric vocabulary (see §1).

## 4. Grounded periods

From the trace (tool events only):
- every `YYYY-MM` in any `list_periods` **output** (the authoritative list), and
- every `YYYY-MM` in any tool **input** (the period a tool was successfully
  called with).

Phantom metrics need no grounding — they are never surfaced, so an affirmative
mention is always flagged (§5a).

## 5. The checks

Split the answer into sentences (`_sentences`: break on `.!?`, newlines, and
table-row pipes `|`). Per sentence, evaluate three predicates once
(`disclaimed`, `unavail`, `offer`) and apply:

### (a) Phantom-metric membership
If the sentence is **not** disclaimed and contains a `_PHANTOMS` label, record
`"<name> — no tool provides this"`.

`disclaimed` (from `_DISCLAIMER`): the sentence carries a negation/inability cue
— `cannot`, `can't`, `can not`, `do not`, `don't`, `does not`, `doesn't`,
`unable`, `outside`, `not available`, `no … tool`, `not … (see|track|produce|
capture|have|show)`.

### (b) Grounded-period predicate
For every `YYYY-MM` in the sentence that **is** grounded, if the sentence is
`unavail`, record `"<period> described as unavailable, but a tool returned it"`.

`unavail` (from `_UNAVAIL`): `not closed`, `un-closed`, `unclosed`, `needs to
(be) closed`, `need(s) to close`, `may need to (be) closed`, `no snapshot`, `not
available`, `isn't closed`, `unavailable`.

### (c) Fabricated-period membership
For every `YYYY-MM` in the sentence that is **not** grounded, if the sentence is
**not** `disclaimed`, **not** `unavail`, and **not** `offer`, record
`"<period> — no tool has data for this period"`.

`offer` (from `_OFFER`): `would you like`, `if you'd like`, `want me to`, `shall
I`, `let me know`, `I can close`, `I can run`, `I can capture`. This keeps a
legitimate offer ("I can close 2026-08 for you") from being flagged; `disclaimed`
/ `unavail` keep an honest "2026-08 is not closed" from being flagged.

`unsupported_claims` returns the de-duplicated issue strings, order preserved.

## 6. Integration

- `verifier.report(answer, trace, *, revised)` →
  `{"grounded": [...], "ungrounded": [...], "unsupported_claims": [...],
    "revised": bool}`; it calls `claims.unsupported_claims` for the new field.
- `verifier.revise_prompt(figures, claims=())` — when `claims` is non-empty, the
  message adds: *"You also made claims not supported by your tools this turn:
  <claims>. Correct each — call the tool that settles it, or state plainly you
  cannot see it — and never assert a period is unavailable if a tool returned
  data for it."*
- `ask()` revises when `figures or claims`; the nudge is built from both.
- `badge(report)` — `✓ all figures tool-grounded` only when both `ungrounded`
  and `unsupported_claims` are empty; otherwise a `⚠` line naming the figure
  count and the claim issues.

## 7. Testing

**Unit (`cfo/tests/test_claims.py`, pure):**
- `grounded_periods` collects periods from a `list_periods` output and from tool
  inputs.
- (a) flags "Our LCR is weak." but **not** "I cannot see an LCR." nor "my tools
  don't produce an NPL ratio."
- (b) flags a grounded period in "…2026-07 may need to be closed first."; passes
  a grounded period stated plainly ("NIM for 2026-07 is 6.28%").
- (c) flags "In 2026-05 our NIM was 5%." when 2026-05 is not grounded; does
  **not** flag "I can close 2026-08 for you." (offer) nor "2026-08 is not closed
  yet." (unavail).
- de-duplication: the same issue mentioned twice yields one entry.

**Verifier (`cfo/tests/test_verifier.py`):**
- `report` includes `unsupported_claims`; `revise_prompt(figs, claims)` names
  both; `badge` warns when only a claim is present.

**Agent (`cfo/tests/test_agent.py`, mocked):**
- pass-1 answer with a bad period claim but no bad number → `ask()` revises
  (`revised True`), driven by the claim channel alone.

**Live smoke (`cfo/verify-cfo.sh`):**
- the existing premise-refusal question's response carries
  `unsupported_claims == []` — the CFO's honest "I can't see NPL" must **not**
  be flagged (the disclaimer guard, verified end to end).

## 8. Scope summary

In: `cfo/claims.py` (phantom vocabulary, grounded periods, three cue-based
checks), the `unsupported_claims` channel through
`report`/`revise_prompt`/`ask`/`badge`, and the tests above. Out: LLM checking,
role/tool-metric grounding, tool-name/synonym grounding. The heuristic's
mis-fires cost at most one revise turn — never a wrong figure or claim shipped —
and every rule is inspectable.
