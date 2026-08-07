# COO demo — questions to type in the console

Copy-paste these into the Streamlit console one at a time. Each maps to a
capability; watch the answer, the grounding **badge**, and the **harness trace**
expander (plan / todos / subagent / memory chips). These are the same prompts
`drive.py` runs, so the console reproduces the narrated arc by hand.

> Every turn is a real model call — the multi-step review takes tens of seconds.
> That's the model working, not a hang.

---

## 1 · Grounded review + planning + subagent

```
/
```

Watch for: `🗺️ planned · ✅ todos · 🔧 tools · 🧵 subagent` in the trace chips,
every figure tool-grounded, and the float quoted with its basis. The subagent's
task + char count show in the **harness trace** expander.

## 2 · Derived figure, still grounded (the `compute` tool)

```
Of the cardholders who used their card in the last 30 days, what share used it only once? I'm worried about disengagement.
```

Watch for: a *derived* figure the raw tools don't return. The COO pulls the
components (`🔧 cards` → active vs one-and-done cardholders) then calls `🔧 compute`
to turn them into a percentage — answering the one-and-done rate, tool-grounded,
without doing the math itself. The deterministic verifier still guards every
figure; the number is grounded because a tool produced it.

## 3 · Memory — record a durable note

```
For the record: note which rail carried the most activity this window and the one operational risk you'd watch. Record it as a durable operational note.
```

Watch for: a `💾 record` chip in the trace.

## 4 · Memory — recall it

> **Reload the page first** (new browser session → fresh conversation). That way
> the recall can only come from durable memory (Qdrant), not from this chat's
> history — which is the whole point.

```
Earlier you recorded a durable operational note about rail activity and a risk to watch. Recall it and tell me where the ops team should focus this week.
```

Watch for: a `🧠 recall` chip, and the answer rebuilding on the earlier note.

## 5 · Scope discipline — fraud is out of bounds

```
Our fraud rate looks high this week — what's driving it?
```

Watch for: a refusal — fraud/AML is deliberately unreachable — with an offer of
the *operational* context it can actually see.

## 6 · Scope discipline — the books are the CFO's

```
What was our net interest margin and RAROC last month?
```

Watch for: "that's the CFO's domain," plus the operational drivers behind it.

## 7 · Caveated figure — float with its basis

```
What's our total operational float right now?
```

Watch for: the total quoted **with** its basis — a gross magnitude of signed
system balances, not a net position — never as a bare number.

## 8 · Autonomous action — the COO pulls a lever

> The COO is an **autonomous operator**, not just an analyst — it takes real
> operational actions on its own judgement, with no human confirmation. For this
> to *do* something, an open outbound AFT batch must be waiting (`run-demo.sh`
> seeds one; otherwise the COO will correctly refuse and say there's nothing to
> cut).

```
Check the outbound AFT batch. If there are entries accrued and awaiting a cutoff, cut the batch now — don't ask me first. Then tell me exactly what you did and the effect the bank returned.
```

Watch for: a `🔧 execute_cut_aft_batch` chip in the trace — the COO *acting*, not
reporting. The lever **self-verifies** server-side (the bank re-checks that an
open, non-empty batch exists and would refuse otherwise), and the attempt —
executed or refused — is written to the **tamper-evident agent-action ledger**.

Then inspect that ledger from your shell:

```
demos/05-coo/inspect-ledger.sh              # the chain + integrity check
demos/05-coo/inspect-ledger.sh --tamper-demo  # prove UPDATE/DELETE are rejected
```

You'll see the row this beat just wrote (`coo · cut_aft_batch · executed`), the
hash chain (each `prev_hash` = the prior `entry_hash`), and `chain INTACT`. The
ledger is out of bounds for the agents themselves — this is the operator view.

---

### Reading the badge

- `✓ all N figure(s) tool-grounded` — every number traces to a tool result this turn.
- `✓ revised once, now clean` — a figure was ungrounded; one revise pass fixed it.
- `⚠ ungrounded: …` — a figure matches no tool output. Note the guardrail is
  strict about the *literal*: if the COO keeps a number but relabels it "my own
  estimate," the badge can still read `⚠` even though the COO did the right thing.
