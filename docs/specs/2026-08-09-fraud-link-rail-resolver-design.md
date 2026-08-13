# Fraud-link rail resolver — design

## Context

The fraud engine joins its decisions to ground truth on
`outcome_events.operation_id = decisions.operation_id`; `decisions` has no
`transaction_id` column. The bank stamps that `operation_id` into
`transactions.metadata.fraud` and exposes it, service-plane only, at
`GET /api/v1/fraud/admin/transactions/{transaction_id}/fraud-link` (#46/#49).

With #53 (interac/lynx), #57 (aft) and #56 (cards) merged, **every screened
money movement now carries its linkage on the money row**. But a caller that
drove a *rail* movement doesn't hold the money `transaction_id` — it holds the
rail's own id (`etransfer_id`, `wire_id`, an aft `entry_id`, a card `auth_id`).
The `nano-bank-world-model` label loop is the concrete consumer: its
`_LINKABLE_RAILS` is pinned to `transfer`/`deposit`/`withdrawal` precisely
because it cannot get from a rail id to the linkage, so rail-driven decisions
export as unlabelled. The fraud-linkage arc is only useful once that gap closes.

## Goal

Let a service caller resolve a **rail id → the screened decision linkage**, so
the world-model can widen `_LINKABLE_RAILS` to the rails and label rail-driven
decisions.

**Scope (first pass): interac, lynx, aft.** Cards is deferred — the corpus
doesn't drive it and it needs a different (metadata) lookup; the endpoint's
`{rail}` match makes it a later one-arm add.

## Design

### Endpoint (nano-bank)

Service-token plane, beside the existing transaction fraud-link:

```
GET /api/v1/fraud/admin/rails/{rail}/{rail_id}/fraud-link
  rail ∈ { interac, lynx, aft }
  → 200 { transaction_id, operation_id, decision_id, failed_open }   # same shape as today
```

Keeping engine ids on the service plane (never the customer plane) is the #46
decision; this endpoint honours it exactly like the transaction one.

### Resolution: rail id → money `transaction_id`

The resolver maps the rail id to the money row, then **reuses the existing
fraud-link read** (`transactions.metadata.fraud`). Because #53/#57/#56 stamp the
linkage onto that money row, one read serves every rail:

| Rail | `rail_id` | Money row |
|---|---|---|
| interac | `etransfer_id` | `interac_etransfers.hold_transaction_id` |
| lynx | `wire_id` | `lynx_wires.settlement_transaction_id` |
| aft | **`entry_id`** | `aft_entries.settle_transaction_id` |

### Refactor

Extract the current `fraud_link` handler's core into
`fraud_link_for(pool, transaction_id) -> FraudLinkResponse` (the
`metadata.fraud` read + response build). Both the transaction route and the new
rail route call it. **No behaviour change** to the existing route.

### Status codes

- unknown `rail` → **400** (`"unknown rail: {rail}"`)
- `rail_id` not found, or its money row is NULL (e.g. an aft entry not yet
  settled) → **404**
- found → **200** with the linkage (nulls if that row wasn't screened — the same
  honest "not screened" semantics as the transaction route)

### World-model consumer (nano-bank-world-model)

- Add `BankClient.fraud_link_rail(service_token, rail, rail_id)` calling the new
  endpoint.
- `bank_driver._fraud_link`: widen `_LINKABLE_RAILS` to include
  `interac_etransfer`, `lynx_transfer`, `aft_batch`, and dispatch each to the
  rail resolver with the id it already captures — **except aft, which must
  capture `entry_id`** (the aft credit/debit response returns it) instead of
  `batch_id`. Update `_realize_one`'s aft branch accordingly (it still submits +
  settles the batch; the resolver reads `settle_transaction_id`, populated by
  then).
- The `_fraud_link` docstring's "do not widen before #52" warning is removed —
  #52 landed and this is the mechanism that unblocks it.

## Testing

**nano-bank** (`api/tests/fraud_port.rs`, engine mode): one test per rail —
send/settle the rail movement, resolve its id, assert the returned `operation_id`
equals the one the engine recorded and names a real `decisions` row. Plus
unknown-rail → 400 and unknown-id → 404. DB-only (no core call), so
backend-agnostic.

**nano-bank-world-model**: `fraud_link_rail` client call (fake bank); a
rail-driven realize result resolves an `operation_id`; the aft path uses
`entry_id`.

## Rollout

Two PRs — the **endpoint first** (nano-bank), then the **consumer**
(nano-bank-world-model), since the consumer calls the endpoint. Cards is a
tracked follow-up (add the `cards` arm keyed on `transactions.metadata->>'auth_id'`
+ a world-model card driver when a scenario needs it).

## Non-goals

- No customer-plane exposure of engine ids (unchanged from #46).
- No change to how the linkage is *stamped* (that's #53/#57/#56, done).
- Cards coverage (deferred).
