# Agent CTO — Phase B: audited infra levers (design)

**Date:** 2026-08-10
**Status:** design approved, spec under review
**Branch:** `agent-cto-levers`, stacked on `agent-cto` (Phase A / PR #67). Base confirmed at plan time.

## Context

The CTO is nano-bank's technical C-suite agent. **Phase A** (PR #67) shipped the
**observability seat**: an analyst over the `platform_mcp` MCP (read-only k8s +
service `/health` across both kind clusters, pure-Python metrics), thin over the
shared `csuite` harness. It observes reliability and delivery and **takes no
action**.

The full CTO mandate has three independent subsystems, each its own spec → plan →
build:

- **A — Observability** (done): the analyst CTO.
- **B — Infra levers** (this spec): audited k8s recovery actions.
- **C — Coding agent** (later): a separate PR-gated plan→edit→test→commit→PR
  harness the CTO calls as a tool.

This document specifies **Phase B only**.

The **COO** already demonstrates the pattern this reuses (T2, PR #55): fully
autonomous, self-verifying, audited operational levers. Each COO lever is a
service-authed bank endpoint that (1) re-checks a **deterministic** precondition
against live bank state, refusing if it is false regardless of what the agent
believed, and (2) writes the attempt — executed **or** refused — to a
hash-chained, immutable `agent_action_ledger` via
`append_agent_action('coo', action, params, effect)`. The agent cannot bypass
the check or forge/alter the ledger.

## Goal

Turn the analyst CTO into an **operator** with two fully-autonomous,
self-verifying, audited **recovery** levers over the kube estate:

- `execute_rollout_restart(cluster, deployment)` — restart a deployment's pods.
- `execute_rollback(cluster, deployment)` — roll a deployment back to its prior
  revision.

Every attempt — executed or refused — lands in the **same** tamper-evident
`agent_action_ledger` as the COO, with `actor='cto'`, so the existing
`inspect-ledger.sh` and the suite console read CTO actions beside COO actions on
one chain.

**Non-goals (Phase B):** scaling (the CTO has no traffic/load signal, so a scale
action has no deterministic precondition to verify against — deferred until such
a signal exists); acting on stateful, system, or own-stack workloads; writing
code (Phase C); a confirm gate (Phase B is fully autonomous, matching the COO).

## Trust model

The LLM (`cto`) never touches k8s. It can only call MCP tools, and
**`platform_mcp` is the trusted verifier-and-actor** — the analog of "the bank"
for the COO. The deterministic precondition check lives **server-side in the
MCP**, not in the prompt, so the agent cannot talk its way past it. Three
independent guardrails, defense in depth:

1. **RBAC (API-server enforced).** A new **write-scoped** ServiceAccount
   (`platform-actor`) in each cluster, whose `patch`/`get` on `deployments`
   (apps) is restricted by `resourceNames` to exactly the allow-listed app
   deployments. Even a bug in `platform_mcp` cannot patch `postgres`,
   `modern-core-db`, system pods, or its own stack — the API server rejects it.
2. **App allow-list (MCP enforced).** Explicit `(cluster, deployment)` pairs in
   `platform_mcp` config, checked before any write:
   - `nano-bank`: `bank-api`, `coo`, `cfo`, `operations-mcp`, `finance-mcp`
   - `modern-core`: `modern-core`
   - Excludes all stateful (`postgres`, `*-db`, `agent-qdrant`), system
     (`coredns`, `local-path-provisioner`), and own-stack (`platform-mcp`, `cto`).
3. **Deterministic self-verify.** The precondition is re-read from **live** k8s
   at execute time — never trusting the agent's earlier observation.

## Design

### `platform_mcp/levers.py` (new, pure, unit-tested)

Dict-in / bool-out, no IO — the deterministic precondition + allow-list logic,
reusing the shapes `k8s_client`/`metrics` already produce:

- `is_allowed(allow_list, cluster, name) -> bool` — membership in the configured
  `(cluster, name)` allow-list.
- `restart_warranted(deployment, pods) -> bool` — true iff the deployment is
  **crashlooping** (a container in `CrashLoopBackOff` or restarts over the
  threshold, from `metrics.restarts`) **or** `ready < desired` (from
  `metrics.estate_health`). The concrete, grounded reason a restart is a valid
  recovery action.
- `rollback_warranted(deployment, replicasets) -> (bool, target_revision|None)`
  — true iff the rollout is **stalled** (`metrics.rollouts` state `stalled`,
  i.e. a `Progressing` condition `reason == ProgressDeadlineExceeded`) **and** a
  prior ReplicaSet revision exists (revision below the current, non-zero
  template) to roll back to. Returns the target revision so the writer knows
  where to go; `(False, None)` otherwise.

### `platform_mcp/k8s_writer.py` (new, injectable seam like `k8s_client`)

Write-capable client over the **actor** SA context. Only two methods, each a
single narrow `patch`:

- `rollout_restart(cluster, name) -> dict` — patch
  `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]` to a
  fresh RFC-3339 timestamp (exactly what `kubectl rollout restart` does),
  triggering a rolling pod replacement. Returns `{restarted_at, ...}`.
- `rollback(cluster, name, target_revision) -> dict` — read the ReplicaSet at
  `target_revision`, patch the deployment's `spec.template` back to that
  ReplicaSet's pod template (what `kubectl rollout undo` does). Returns
  `{rolled_back_to, ...}`.

A `writer_loader(kubeconfig_path, context) -> AppsV1Api` seam (mirroring
`k8s_client`'s loader) lets tests inject a fake that records the patch body with
no live cluster. Uses the `platform-actor` context, distinct from the read
context.

### `platform_mcp/audit.py` (new)

`post_action(action, params, effect)` → `POST` to the bank's ledger endpoint
(below) with the MCP's service token, tagging `actor='cto'` server-side. It is
**loud, not best-effort**: if the audit write fails, the lever fails and reports
the failure. Never act without recording — the COO audits inside the same DB
transaction as the act; the CTO's act (a k8s patch) can't share that transaction,
so the ordering is **verify → act → audit**, and an audit failure after a
successful act is surfaced as an error (the operator sees an un-audited action
and can reconcile), never swallowed.

### `platform_mcp/mcp_server.py` (extended)

Two new tools, each: allow-list check → **live** self-verify (re-read via
`k8s_client`) → act via `k8s_writer` → `audit.post_action` → return
`{"outcome": "executed"|"refused", ...}` (verbatim COO shape: `executed` carries
the effect, `refused` carries the reason). A refusal (not allow-listed, or
precondition false) is **also audited** — the attempt is a fact.

- `execute_rollout_restart(cluster: str, deployment: str) -> dict`
- `execute_rollback(cluster: str, deployment: str) -> dict`

The read tools (`estate_health`, `restarts`, `rollouts`, …) are unchanged.

### Bank (Rust): the CTO audit endpoint

`POST /api/v1/agent-ledger/actions` (service-authed, in a new
`api/src/handlers/agent_ledger.rs`): body `{action, params, effect}` →
`SELECT append_agent_action('cto', $action, $params::jsonb, $effect::jsonb)`.
The endpoint **pins `actor='cto'`** — it does not accept an actor from the caller
— so a compromised `platform_mcp` cannot forge a `coo`/`cfo` entry. No schema
change: `actor` is free `TEXT` and the hash-chain/immutability/verify machinery
already exists. Returns the `{seq, entry_hash}` the function yields.

### `cto/agent.py` (extended)

`CTO_PROMPT` gains an autonomous-operator paragraph, mirroring the COO's: you are
an operator and may pull `execute_rollout_restart` / `execute_rollback` on your
own judgment, with no human confirmation; before acting, look at the metrics to
confirm the action is warranted, then pull the lever; each lever is
self-verifying — `platform_mcp` independently re-checks a deterministic
precondition and will **refuse** an unwarranted action — and every attempt,
executed or refused, is written to a tamper-evident audit ledger you cannot read
or alter; take the action and report plainly what came back (executed with its
effect, or refused with the reason); act only within the platform — never touch
the books or money operations.

`cto/tools.py` is unchanged — the levers arrive as MCP tools.

### RBAC / kubeconfig

- `platform_mcp/k8s/rbac-actor.yaml` — the `platform-actor` ServiceAccount, a
  `Role`/`ClusterRole` granting `get`/`patch` on `deployments` (apps) **scoped by
  `resourceNames`** to the cluster's allow-listed apps, and its binding. One file
  applied into **both** clusters (the `resourceNames` list differs per cluster,
  so two variants, or a templated apply — a small per-cluster manifest).
- `platform_mcp/k8s/make-kubeconfig.sh` — extended to also mint the
  `platform-actor` SA and add a second context/user (`platform-actor@<ctx>`) to
  the mounted kubeconfig Secret, so `k8s_writer` authenticates as the actor while
  `k8s_client` keeps using the read-only `platform-reader`.

### Config

`platform_mcp/config.py` gains: `actor_context` per cluster (or a naming
convention off the read context), the `allow_list` of `(cluster, deployment)`
pairs, the bank ledger endpoint URL + service secret, and the restart threshold.

## Data flow (a restart)

```
CTO reads metrics → sees `coo` crashlooping
  → execute_rollout_restart("nano-bank", "coo")
     platform_mcp:
       is_allowed(nano-bank, coo)?            ── no  → refuse + audit(refused) → return
       live re-read: restart_warranted(coo)?  ── no  → refuse + audit(refused) → return
       k8s_writer.rollout_restart(...)  (actor SA patch)
       audit.post_action("rollout_restart", {cluster,deployment}, {executed, restarted_at})
       return {outcome: "executed", effect: {...}}
```

`execute_rollback` is identical with `rollback_warranted` (which also yields the
target revision) and `k8s_writer.rollback`.

## Testing

- **`platform_mcp/tests/test_levers.py`** — pure: `restart_warranted` (crashloop,
  ready<desired, healthy=no), `rollback_warranted` (stalled+prior rev → target;
  stalled+no prior → no; healthy → no), `is_allowed`.
- **`platform_mcp/tests/test_k8s_writer.py`** — injected fake AppsV1Api asserts
  the exact `restartedAt` patch body and the rollback template patch; no live
  cluster.
- **`platform_mcp/tests/test_audit.py`** — mock httpx transport: posts the right
  body; a non-2xx / transport error raises (loud).
- **`platform_mcp/tests/test_mcp_levers.py`** — the two `execute_*` tools built
  with fake client/writer/audit: executed path (warranted → acts → audits),
  refused-not-allowed, refused-precondition-false (both still audit), and
  **audit-failure aborts loudly** after the act.
- **Bank**: a Rust integration test for `POST /api/v1/agent-ledger/actions` —
  appends an `actor='cto'` row and `verify_agent_ledger()` stays intact;
  service-auth required (401/403 without token).
- **Live smoke `platform_mcp/verify-cto-levers.sh`** — force a deployment into a
  bad state (e.g. patch a bogus image to induce a crashloop, or trigger a stalled
  rollout), have the CTO pull the matching lever, confirm the deployment recovers,
  and confirm a fresh `actor='cto'` entry on the chain with
  `verify_agent_ledger()` still `0` (intact) via the existing `inspect-ledger.sh`.

## Rollout

- Extend RBAC: apply `rbac-actor.yaml` (both clusters) and re-run
  `make-kubeconfig.sh` to refresh the Secret with the actor context.
- Rebuild + reload `nano-platform-mcp:dev` and `bank-api` (new endpoint);
  redeploy; the CTO deployment is unchanged except the prompt (rebuild `nano-cto`).
- `/health` green; run `verify-cto-levers.sh`.

## Isolation / boundaries

- **`levers.py`** is one bounded, pure unit (precondition + allow-list) — the
  decision logic, testable with no cluster.
- **`k8s_writer.py`** isolates the only new *mutating* capability behind an
  injectable seam; it holds exactly two narrow patches and nothing else.
- **The ledger endpoint** keeps DB credentials in the bank and pins the actor, so
  the audit trail stays trustworthy even if the actuator is compromised.
- **RBAC `resourceNames`** makes the blast radius a property the API server
  enforces, not just application code.
- Phase C (the coding agent) plugs in later as an external tool the CTO calls;
  it does not reshape this seat.
