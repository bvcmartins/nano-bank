# Agent COO — demo

A narrated walk-through of the nano-bank Chief Operating Officer: an **autonomous
operational officer** that answers **only** from its operations tools, plans and
delegates multi-step reviews, grounds every figure against a tool result, stays
in its lane — and **acts**, pulling self-verifying operational levers on its own
judgement with no human in the loop, every action written to a tamper-evident
audit ledger.

Two ways to run it, same story:

- **Narrated script** (`run-demo.sh` → `drive.py`) — reproducible; brings the
  stack up, seeds a bounded burst of demo activity, and prints each beat with the
  answer, the verification badge, and the harness trace highlights.
- **Live console** (`coo/console.py`) — a Streamlit chat you drive by hand; type
  the same beats and watch the answer, the grounding badge, and an expandable
  **harness trace** panel (plan / todos / subagent / memory) update live.

The narration only **asks** the COO — but the COO itself may **act** (beat 8
pulls a real lever). Seeding is demo/test-only: it runs from the host against a
port-forwarded bank, never from an app process or a k8s manifest.

## Run it (in-cluster)

```bash
# up (modern core + bank + agent + COO) → seed → narrated arc
demos/05-coo/run-demo.sh

# variations
demos/05-coo/run-demo.sh --no-up       # stack already deployed
demos/05-coo/run-demo.sh --no-seed     # leave the bank's data as-is
demos/05-coo/run-demo.sh --beats 1,5   # only these beats
demos/05-coo/run-demo.sh --down        # tear down the demo port-forwards
```

Prereqs: `docker` + `kind` + `kubectl` + `uv`, and the sibling
`nano-bank-modern-core` repo checked out beside this one (the GL core the seed
posts through). Bring-up leans on `scripts/deploy-all.sh` + `coo/k8s/deploy.sh`.

### Live console

`run-demo.sh` tears down its own port-forwards when it exits, so for the live
console open a forward yourself (the stack stays up in-cluster):

```bash
kubectl -n nano-bank port-forward svc/coo 8093:8093 &
COO_API_URL=http://localhost:8093 streamlit run coo/console.py   # :8501
```

Then type the beats below and watch the answer, the grounding badge, and the
expandable **harness trace** panel update per turn.

## The arc — what each beat proves

| # | Ask | What it demonstrates |
|---|-----|----------------------|
| 1 | 30-day operational health review + subagent deep-dive on the busiest rail | **Grounded reads** across float/txns/rails/exceptions/cards; the harness **plans**, keeps **todos**, and **spawns a subagent** for a focused rail deep-dive (its tool chatter never enters the main context) |
| 2 | "What share of cardholders used the card only once (disengagement)?" | A **derived figure** the raw tools don't return: the COO pulls active vs one-and-done cardholders and calls the deterministic **`compute`** tool to turn them into a %, answered **grounded** — no hand-arithmetic, no "you do the math," and the verifier still guards it |
| 3a | "Record a durable note: busiest rail + one risk to watch" | Durable **memory write** |
| 3b | *(fresh conversation)* "Recall that note; where should ops focus?" | **Memory recall across turns** — a new thread with no shared state, so the only way it knows the note is durable Qdrant memory, not in-thread history |
| 4 | "Fraud rate looks high — what's driving it?" and "What was our NIM and RAROC?" | **Scope discipline** — fraud/AML is deliberately unreachable, and the books are the CFO's domain; the COO refuses rather than engaging |
| 5 | "What's our total operational float right now?" | **Caveated figures** — the headline float is quoted with its **basis** (a gross magnitude of signed system balances, not a net position), never as a bare number |
| 6 | "Cut the outbound AFT batch now — don't ask me first." | **Autonomous action** — the COO checks the batch and pulls **`execute_cut_aft_batch`** on its own judgement (no human confirmation). The lever **self-verifies** server-side and refuses if there's nothing to cut; every attempt lands in the **tamper-evident agent-action ledger**. Inspect it with `demos/05-coo/inspect-ledger.sh` |

## What you're looking at

Each `/ask` returns `{answer, thread_id, trace, verification}`:

- **verification** — `grounded` / `ungrounded` figures and whether a revise pass
  ran. A clean badge means every number in the prose traces to a tool result
  *this turn*.
- **trace** — one ordered list merging tool/model steps with harness events
  (plan, todos, subagent spawn/return, memory writes, context compaction). The
  script distils it to a highlights line; the console shows the full thing in the
  "harness trace" expander.

## Notes

- The **memory** beat needs Qdrant up (deployed by the agent stack). If it's
  absent the COO still answers from live tools — memory just degrades to a no-op,
  and beat 3b will say it has no note to recall.
- Beat 2 relies on the **`compute`** tool: derived figures (averages, ratios,
  shares) are done deterministically by a tool so they stay grounded — the COO
  never hand-computes and never asks you to. The verifier still catches any
  ungrounded number that slips through and forces one revise pass.
- The **autonomous-action** beat needs an open outbound AFT batch to cut;
  `run-demo.sh` seeds one right before the arc (`seed_open_aft.py`). With
  `--no-seed`, or if you re-run the beat after the batch is already cut, the COO
  will correctly **refuse** ("no open batch to cut") — which is still audited.
  Either way, `demos/05-coo/inspect-ledger.sh` shows the ledger row and verifies
  the hash chain; `--tamper-demo` proves UPDATE/DELETE are rejected server-side.
  The ledger spans every agent (the CFO's period closes appear there too) and is
  out of bounds for the agents themselves.
- Re-running `run-demo.sh` (with seeding) adds *more* bounded activity; the
  figures grow. `testing/cleanup.sh` wipes the bank back to empty.
