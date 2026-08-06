# CFO demo — questions to type in the console

Copy-paste these into the Streamlit console one at a time. Each maps to a
capability; watch the answer, the grounding **badge**, and the **harness trace**
expander (plan / todos / subagent / memory chips). These are the same prompts
`drive.py` runs, so the console reproduces the narrated arc by hand.

> Every turn is a real model call — a full period review takes a while, and the
> **first** request after a fresh deploy is slow (the CFO downloads its memory
> embedder). Replace `2026-08` with the current month.

---

## 1 · Grounded review + planning + subagent

```
Close period 2026-08 if needed, then give me a financial health review — balance-sheet highlights, income, NIM, key ratios and RAROC, with the numbers. Do a focused subagent deep-dive on one segment and fold its finding into your summary.
```

Watch for: `🗺️ planned · ✅ todos · 🔧 tools · 🧵 subagent`, every figure
tool-grounded, and the period named. The CFO says plainly when it uses
`close_period` (its one state-changing tool).

## 2 · Derived figure, still grounded (the `compute` tool)

```
What was our cost-to-income ratio for 2026-08? Give me just that number.
```

Watch for: a ratio no metric tool returns — the CFO pulls the components
(`🔧 income_statement`) then calls `🔧 compute` to divide them, answering it
grounded without doing the math itself.

## 3 · Memory — record a durable note

```
For the record: note the single biggest financial risk in this month's numbers, and record it as a durable note.
```

Watch for: a `💾 record` chip.

## 4 · Memory — recall it

> **Reload the page first** (new session → fresh conversation), so the recall can
> only come from durable memory (Qdrant), not this chat's history.

```
Earlier you recorded a durable note about our biggest financial risk. Recall it and tell me where finance should focus.
```

Watch for: a `🧠 recall` chip, and the answer rebuilding on the earlier note.

## 5 · Refuse an unverifiable premise

```
Our 3% NPL ratio worries me — what's driving it?
```

Watch for: the CFO **declines** — the ledger holds no non-performing-loan data,
so completing the narrative would be inventing it. "The ledger doesn't show that"
is the correct answer.

## 6 · Scope discipline — operations are the COO's

```
How's our settlement backlog and rail throughput looking?
```

Watch for: the CFO defers to the COO rather than answering outside the books.

## 7 · Period discipline — no fabricated span

```
How did we do last quarter?
```

Watch for: snapshots are **monthly** — the CFO says which periods actually exist
and won't answer from a single month as if it were a quarter.

---

### Reading the badge

- `✓ all N figure(s) tool-grounded` — every number traces to a tool result this turn.
- `✓ revised once, now clean` — a figure was ungrounded; one revise pass fixed it.
- `⚠ ungrounded: …` — a figure matches no tool output (the guardrail catching drift).
