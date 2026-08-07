# Agent-action ledger + autonomous COO levers

**Status:** approved (design)
**Date:** 2026-08-06

## Problem

Agents are gaining the power to change bank state — the CFO already closes
periods, and the COO is about to get operational levers (cut an AFT batch, sweep
expired e-Transfers, reject stale Lynx wires, flush the notification outbox).
Two requirements:

1. **Every state-changing agent action, by any agent, must be recorded in a
   tamper-evident audit trail that no agent can read, write, or alter.**
2. The COO must be **autonomous** — it executes levers on its own judgement, with
   **no human confirm** — bounded by a **deterministic self-verify** (a rule the
   model cannot argue past), not by a person.

## Design

### 1. The agent-action ledger (hash-chained, append-only)

A `agent_action_ledger` table in the bank Postgres, written by a single
`append_agent_action(actor, action, params, effect)` PL/pgSQL function:

```
seq BIGSERIAL, ts, actor TEXT, action TEXT, params JSONB, effect JSONB,
prev_hash TEXT, entry_hash TEXT
```

- `entry_hash = sha256(seq || ts || actor || action || params || effect || prev_hash)`
  (pgcrypto `digest`), chaining each entry to the previous — a "blockchain" in the
  audit sense: tampering with any row breaks every hash after it.
- The append function takes an advisory lock so the chain is serialized under
  concurrency.
- **Immutable:** a trigger blocks `UPDATE`/`DELETE`; the table is INSERT-only.
- **Verify:** `verify_agent_ledger()` walks the chain and returns the first broken
  seq (or NULL if intact) — for a human/admin, never an agent.

### 2. Out of bounds for every agent

Agents reach the bank only through curated MCP tools. **No MCP tool (COO or CFO)
reads or writes the ledger or its table**, and the back-office read plane does not
expose it. The ledger is written only server-side by the action paths.

### 3. Every agent action writes to it

- **CFO `close_period`** (finance MCP) → `append_agent_action('cfo',
  'close_period', {period}, {roles_captured})` after the snapshot.
- **COO levers** (new bank endpoints, below) → append on every attempt, executed
  **and** refused.
- Future agent writes follow the same call.

### 4. Autonomous COO levers, self-verify then act

Four new **service-plane bank endpoints** `POST /api/v1/ops-levers/<lever>`
(`cut-aft-batch`, `sweep-expired-etransfers`, `reject-stale-wires`,
`flush-notifications`). Each, authoritatively server-side:

1. **re-reads live state and checks a deterministic rule** (the self-verify) —
   cut only an open, non-empty batch; sweep only genuinely-expired e-Transfers;
   reject only wires past the stale threshold; flush only a non-empty outbox;
2. if the rule holds → performs the lever (reusing the existing admin logic),
   returns the concrete effect;
3. if not → refuses without acting;
4. **appends to the ledger** either way.

The operations MCP gains `execute_*` tools that proxy to these endpoints; the COO
calls them autonomously. The self-verify is the bank's rule, not the model's
discretion.

### 5. Prompt + transparency

COO_PROMPT becomes an **autonomous operator**: it may pull a lever when warranted,
must state plainly what it did and the returned effect, and note a deterministic
pre-check gated it. The run-trace shows each `execute_*` call's input→effect. The
CFO and `csuite.runtime` are unchanged (levers are COO-only).

## Phases (one PR, staged commits)

1. **The ledger.** `agent_action_ledger` table + `append_agent_action` +
   immutability trigger + `verify_agent_ledger`; wire the CFO's `close_period` as
   the first writer. Tests: append chains, trigger blocks update/delete, verify
   detects a break.
2. **COO levers.** The four `ops-levers` endpoints (self-verify + execute + ledger
   append), the MCP `execute_*` tools, the COO prompt + toolset, and the console
   showing executed actions. Verified live: cut a seeded AFT batch, confirm the
   ledger row + a refused attempt + chain integrity.

## Testing & verification

- Unit: ledger append/verify/immutability (bank tests + a SQL check); MCP execute
  tools; COO prompt/tools.
- End-to-end (in-cluster): COO cuts a real AFT batch and refuses an invalid one;
  CFO close_period lands in the ledger; `verify_agent_ledger()` intact; no agent
  tool can see the ledger.

## Non-goals

- Distributed consensus / proof-of-work (a single-DB hash chain is the audit
  requirement).
- COO/CFO gaining any ledger-read tool.
- Reversing executed levers (the bank's own semantics apply).
