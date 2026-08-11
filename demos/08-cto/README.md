# Demo 8 — Agent CTO (analyst + audited self-recovery)

The **Chief Technology Officer** agent over the platform MCP: a grounded analyst
across BOTH kind clusters (reliability + delivery) that also **acts** — it
autonomously **rolls back a bad deployment** and every action lands in the
tamper-evident `agent_action_ledger`. Same `csuite` harness as the COO/CFO demos.

## What it shows

A 7-beat narrated arc (`drive.py`): a grounded estate/delivery review across both
clusters with planning + a subagent deep-dive; a degraded-share figure via the
compute tool; durable memory recorded and recalled in a fresh thread; scope
discipline (the books are the CFO's); a **restart refused** on a healthy target
(the self-verify guardrail); and an **autonomous rollback** that genuinely
recovers a staged bad rollout on `cfo`. The analyst beats are explicitly
read-only so the CTO doesn't remediate before its cue. Then the runner confirms
`cfo` is healthy again and the audit ledger is inspected.

## Honesty note

A rollout-restart cycles pods; it does **not** fix a bad revision. So the demo's
genuine recovery uses **rollback** against a staged bad rollout, and **restart**
is shown as a **refusal** on a healthy service. Nothing is faked — both levers
appear on the conditions they actually apply to.

## Run it

```bash
# one command: bring up -> stage the incident -> drive -> inspect the ledger
demos/08-cto/run-demo.sh

# against an already-deployed stack:
demos/08-cto/run-demo.sh --no-up

# fast path — just the guardrail + recovery beats:
demos/08-cto/run-demo.sh --beats 6,7

# tear down port-forwards and restore cfo:
demos/08-cto/run-demo.sh --down
```

Prereqs: docker + kind + kubectl + uv, and (for bring-up) the sibling
`nano-bank-modern-core` repo beside this one. The runner stages the incident from
the host against a port-forwarded cluster (demo-only), and an EXIT trap restores
`cfo` if a run is aborted mid-arc.

## Inspect the audit trail

```bash
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh --tamper-demo
```
