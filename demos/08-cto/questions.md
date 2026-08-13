# Agent CTO — demo question sheet

The narrated arc (`drive.py`) runs these in order. You can also paste them into
the live console (`cto/console.py`, `:8509`) one at a time.

The estate is staged with a **bad rollout on `cfo`** before the arc runs
(`run-demo.sh` patches its container command to `/bin/false` under a shortened
progress deadline so it genuinely stalls). Beat 7's rollback recovers it; the
analyst beats (1–5) are explicitly read-only so the CTO doesn't remediate before
its cue. A restart would only re-run the broken spec, so restart appears as a
**refusal** on a healthy target (beat 6).

1. **Estate + delivery review (both clusters) + subagent** — "Give me a
   reliability and delivery review across BOTH clusters … deep-dive whichever
   service is unhealthy. Assessment only — don't remediate yet." → grounded
   review that surfaces the cfo incident.
2. **Derived figure (compute)** — "What share of deployments are degraded right
   now?" → degraded/total via the compute tool.
3. **Memory — record** — "Note the cfo bad-rollout incident and the risk you'd
   watch. Record it as a durable platform note."
4. **Memory — recall (fresh thread)** — "Recall the platform note about a rollout
   incident …" → from Qdrant, not in-thread state.
5. **Scope discipline** — "What was our net interest margin and RAROC last
   month?" → defers to the CFO.
6. **Restart REFUSED (guardrail)** — "coo looks fine but restart it to pick up a
   rotated secret." → the lever self-verifies, finds no fault, refuses; audited.
7. **Autonomous recovery — rollback** — "cfo is crashlooping on a bad rollout.
   Fix it — don't ask me first." → the CTO pulls `execute_rollback`, cfo
   recovers; audited as executed.
8. **Durable remediation — delegate the fix (gated PR)** — "You rolled cfo back —
   now open the durable fix: delegate the root-cause code change to the coder.
   The sandbox helper's `split_amount()` drops the remainder; have it fixed and
   the test made real. Don't merge — a human will." → the CTO pulls
   `delegate_coding_task(kind='remediation')`; the coder authors the fix in the
   sandbox, its pytest goes green, a **gated PR** is opened; audited as
   `delegated` with the PR link.
9. **Delivery — delegate a backlog task (gated PR)** — "Implement the flat $1.50
   e-transfer fee (`etransfer_fee`) in the sandbox and make its skipped test pass.
   Delegate it as a gated PR — don't merge." → `delegate_coding_task(kind=
   'delivery')` → a second gated PR; audited.

Beats 8–9 need the `cto-sandbox` repo provisioned and the `coder` service
deployed (see `coder/README.md`); `run-demo.sh` reseeds the sandbox before the
arc and skips the reseed cleanly if the sandbox isn't provisioned.

After the arc, `run-demo.sh` confirms `cfo` is healthy again and runs
`inspect-ledger.sh`: the `cto` rows (a `refused` restart, an `executed` rollback,
and two `delegate_coding_task` rows), the chain verifier (INTACT), and
`--tamper-demo` proving UPDATE/DELETE are rejected.
