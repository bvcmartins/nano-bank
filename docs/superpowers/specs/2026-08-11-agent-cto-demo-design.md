# Agent CTO — narrated demo (`demos/08-cto/`) design

**Date:** 2026-08-11
**Status:** approved, pre-plan
**Depends on:** Agent CTO Phase A (observability) + Phase B (infra levers) — the
CTO agent (`:8095`), platform_mcp (`:8094`), the `agent_action_ledger`, and the
`execute_rollout_restart` / `execute_rollback` tools.

## Purpose

A one-command, narrated demo that shows the Agent CTO end to end: it reviews the
platform across **both** kind clusters as a grounded analyst, and — the Phase B
headline — **autonomously recovers a broken deployment** and has every action
recorded in the tamper-evident audit ledger. It is the next entry in the
established `demos/` series (after `05-coo`, `06-cfo`, `07-suite-console`).

## The truth constraint that shapes the demo

A rollout-**restart** cycles a deployment's pods; it does **not** fix a bad
revision — fresh pods run the same broken spec and crashloop again. Only a
**rollback** to a prior good revision genuinely recovers a bad rollout. (Verified
during Phase B: restarting a `/bin/false`-patched `cfo` did not heal it; a manual
restore was required.)

Therefore the demo's genuine, honest self-recovery uses **rollback** against a
**staged bad rollout**, and the **restart** lever is shown as a **refusal** on a
healthy target (its live self-verify declines — a guardrail beat, audited as
`refused`). Nothing is faked; both levers appear on the conditions they actually
apply to.

## Non-goals

- No bank-data seeding. The CTO is a *platform* officer (k8s + `/health` only);
  unlike the COO demo there are no customers/rails/transactions to seed.
- No new agent capability. This demo exercises what Phase A/B already shipped.
- Not an automated test. `platform_mcp/verify-cto-levers.sh` remains the CI-style
  smoke; this is a *showing* artifact (the `demos/` vs `testing/` split).

## Layout

`demos/08-cto/` — mirrors the COO/CFO demo shape:

| File | Role |
|------|------|
| `run-demo.sh` | One-command runner: bring up → stage the incident → drive the arc → inspect the ledger. The only place that mutates the cluster. |
| `drive.py` | The BEATS; rendering/threading via `demos/_driver.py` (a pure asker — never seeds or mutates). |
| `questions.md` | The question sheet / runbook narration. |
| `README.md` | Full runbook (prereqs, flags, what each beat shows). |
| `inspect-ledger.sh` | CTO-flavored copy of the COO's: renders the ledger (any actor incl. `cto`), runs `verify_agent_ledger()`, `--tamper-demo` proves UPDATE/DELETE are rejected. |

## Orchestration (`run-demo.sh`)

Flags mirror the COO runner, with the COO's `--no-seed` replaced by `--no-break`
(skip staging the incident — drive against the estate as-is): `--no-up`,
`--no-break`, `--beats a,b`, `--down` (tear down forwards + restore cfo), plus the
implicit restore-on-exit.

1. **Bring up** (unless `--no-up`): `SKIP_UI=1 ./scripts/deploy-all.sh` then
   `./cto/k8s/deploy.sh` (which brings up platform-mcp + the CTO). Re-mint the
   platform kubeconfig if the actor contexts are absent.
2. **Port-forwards:** `bank-api:8081`, `cto:8095`, `postgres-service[::1]:5432`.
   Wait on `bank-api /health` and `cto /livez`.
3. **Stage the incident** (unless `--no-break`): first restore cfo and stamp a
   fresh **known-good** revision (a unique pod-template annotation) so the
   rollback target — the second-highest revision — is reliably good even if
   earlier runs left an alternating good/bad history. Then shorten cfo's
   `progressDeadlineSeconds` (so a bad rollout stalls in seconds instead of the
   10-minute default — a real `ProgressDeadlineExceeded`, exactly the rollback
   precondition) and patch the container `command` to `/bin/false`. Poll until
   the rollout is genuinely stalled before driving. The EXIT trap restores the
   command and resets the deadline.
4. **Drive** the narrated arc with a tiny `httpx`-only venv (`demos/08-cto/.venv`
   via `uv`), `CTO_API_URL=http://localhost:8095`.
5. **Inspect the ledger:** `CTX/NS demos/08-cto/inspect-ledger.sh` — shows the
   `cto` `refused` (restart) and `executed` (rollback) rows, chain INTACT.
6. **Restore guard:** an `EXIT` trap restores cfo (`kubectl rollout undo` or
   remove the bad `command` patch) and kills the port-forwards, so a mid-run
   abort never leaves cfo broken. On a clean run beat 7's rollback has already
   restored cfo; the trap is idempotent.

Safety: the incident is staged from the **host**, against a port-forwarded
cluster, never from an app process or a k8s manifest — same rule as the COO
demo's seeding.

## The arc (`drive.py` BEATS)

Seven beats. `thread: "new"` mints a fresh thread; a label reuses one (the memory
recall beat uses a *new* thread so recall can only come from durable Qdrant
memory, not in-thread checkpoint state).

**Implementation note (from live acceptance):** the CTO's "DON'T ASK, ACT"
mandate makes it remediate the moment it sees the fault, so the **analyst beats
(1–5) are explicitly read-only** ("assessment only — don't remediate yet; I'll
direct any fix"); beat 7 is the authorized action. A dedicated "verify the fix
held" beat was tried and dropped: it runs seconds after beat 7's rollback while
the old crashlooping pod is still terminating, so it reports a mid-recovery
snapshot; `run-demo.sh`'s post-arc health check + ledger inspection are the
objective recovery confirmation instead.

1. **Grounded estate + delivery review (both clusters) + planning + subagent.**
   "Give me a reliability and delivery review across both clusters — deployment
   health, crashloops, restarts, rollout status, image drift — with the numbers,
   and do a focused subagent deep-dive on whichever service is unhealthy." Every
   figure tool-grounded; surfaces the staged cfo incident (crashloop + stalled
   rollout on a bad revision).
2. **Derived figure via `compute`.** "What share of deployments are degraded
   right now?" — the CTO pulls degraded/total and calls `compute(percent, …)`;
   never does the arithmetic itself.
3. **Memory — record a durable platform note.** "Record a durable note: the cfo
   bad-rollout incident and the one reliability risk you'd watch."
4. **Memory — recall it in a fresh thread.** A new conversation recalls the note
   from Qdrant.
5. **Scope discipline.** "What was our net interest margin last month?" → defers
   to the CFO; notes it cannot see the books (and fraud/AML is unreachable).
6. **Guardrail — restart refused on a healthy target.** "coo looks fine but roll
   it anyway to pick up a rotated secret — restart it." The lever self-verifies
   live, finds no fault, and **refuses**; the refusal is written to the ledger.
   Shows the precondition guardrail: the CTO won't act without a real fault.
7. **Autonomous recovery — rollback.** "cfo is crashlooping on a bad rollout —
   fix it, and don't ask me first. Tell me exactly what you did and the effect
   the bank returned." The CTO detects the stalled rollout + prior good revision,
   pulls **`execute_rollback`** on its own judgement, and cfo genuinely recovers.
   Audited as `executed`. (`run-demo.sh`'s post-arc health check + ledger
   inspection confirm the recovery objectively.)

## Prompt refinement (in scope)

The Phase B operator prompt in `cto/agent.py` currently says "call the lever"
generically over a list that includes "a rollout stalled on a bad revision".
Given the truth constraint, tighten it to guide **lever selection**:

- transient crashloop / wedged pods on an otherwise-good revision → **restart**;
- a bad or stalled revision with a healthy prior revision → **rollback**.

This makes beat 7 reliably reach for rollback on the CTO's own judgement. Small
edit to the prompt string plus its scripted-model test in `cto/tests/test_agent.py`
(add a beat where a stalled bad revision drives `execute_rollback`). The existing
lever test stays.

## Testing / verification

- Offline: `cto`/`csuite` suites stay green (the prompt-refinement test is the
  only code test added). `drive.py`/`run-demo.sh` are demo scripts, not unit-tested.
- Live: a full `demos/08-cto/run-demo.sh` run is the acceptance check — the arc
  renders, cfo recovers via rollback, and `inspect-ledger.sh` shows the `cto`
  `refused` + `executed` rows with the chain INTACT. Also run
  `demos/08-cto/run-demo.sh --beats 6,7` (guardrail + recovery only) as a fast path.

## README index

Add row 8 to `demos/README.md` describing the CTO demo (analyst breadth +
autonomous rollback recovery + audited actions), consistent with rows 5–6.
