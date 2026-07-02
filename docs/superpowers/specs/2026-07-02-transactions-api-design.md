# Transaction APIs — Design

Date: 2026-07-02
Component: `api/src/handlers/transactions.rs` (+ tests, smoke script)

## Goal

Implement the four stubbed transaction endpoints so they perform real,
balanced double-entry postings, enforce banking rules, and (for
deposit/withdrawal) post the aggregate effect to the swappable general-ledger
core through the existing `Ledger` port — matching the patterns already
established in `handlers/cards.rs`.

Endpoints:

- `GET  /api/v1/transactions` — transaction history (filters + pagination)
- `POST /api/v1/transactions/transfer`
- `POST /api/v1/transactions/deposit`
- `POST /api/v1/transactions/withdrawal`

Request/response models already exist in `models/transaction.rs` and are reused
as-is; no model changes are required.

## Approach

Follow `cards.rs` exactly for the money-movement mechanics:

- one `state.pool.begin()` DB transaction per request;
- `SELECT ... FOR UPDATE` to lock every account row touched;
- **both ledger legs inserted in a single multi-row statement** so
  `trigger_validate_transaction_balance` sees a balanced set (`post_two_legged`
  pattern);
- never write `accounts.balance` / `balance_before` / `balance_after` directly —
  `trigger_update_account_balance` maintains them; we pass `0` placeholders;
- recompute `available_balance` ourselves afterward (the trigger only maintains
  `balance`);
- reference numbers via the `PREFIX + 12 digits` helper (matches
  `^[A-Z0-9]{10,20}$`); `normalize_amount` for 2dp / positivity.

Alternatives considered and rejected: a service/repository layer (repo keeps SQL
inline in handlers); adding an `account_type` enum value for the cash
counterparty (ripples into the Rust enum + match arms).

## Counterparty for deposit / withdrawal — "external cash"

Transfer is naturally two-legged (customer ↔ customer). Deposit and withdrawal
touch only one customer account, so they need a counterparty account for the
balance-validation trigger and the entry FK.

We introduce a **second synthetic system customer** `cash@nano.bank` owning a
single `chequing` account, `EXTERNAL_CASH`, with a $1 trillion overdraft and
`available_balance` left at its default `0`. Leaving `available_balance = 0`
(as `ensure_gl_account` already does for the card system accounts) means a very
negative `balance` never trips `chk_available_balance_logical`
(`available_balance <= balance + overdraft_limit`). Bootstrapped idempotently
and re-resolved per request, mirroring `cards::ensure_system_accounts`.

Using a distinct system *customer* (rather than another account under the
existing `system@nano.bank`) avoids the fact that the two account-type slots
under that customer are already taken by `VISA_CLEARING` (chequing) and
`BANK_SETTLEMENT` (savings).

## Endpoint behaviour

All three write endpoints operate only on `chequing` / `savings` accounts;
`credit_card` accounts are rejected (`BadRequest` — use the card rails).

| Endpoint | Local subledger legs | GL core post (via `state.ledger`) |
|---|---|---|
| `deposit` | customer **credit** (+bal); EXTERNAL_CASH **debit** | debit `Bank` / credit `Payable` |
| `withdrawal` | customer **debit** (−bal); EXTERNAL_CASH **credit** | debit `Payable` / credit `Bank` |
| `transfer` | from **debit** (−bal); to **credit** (+bal) | **none** (see below) |
| `GET /` | read-only | — |

`transaction_type` column values: `deposit` / `withdrawal` / `transfer`.
`initiated_by` = the customer owning the (from-)account. Status is `completed`
with `completed_at = CURRENT_TIMESTAMP` (satisfies `chk_status_timestamps`).

`available_balance` recompute for customer deposit accounts:
`balance + overdraft_limit − SUM(open holds)`. The EXTERNAL_CASH account's
`available_balance` is never touched (stays 0).

### Why transfer does not post to the GL core

Both customer deposit accounts map to the same semantic GL role (`Payable` —
customer deposits are a bank liability). A transfer's aggregate GL effect is a
debit and a credit to the *same* role, which nets to zero; most cores reject a
zero-net or same-account entry. A transfer is an internal reclassification, so
it is recorded in the local subledger only. Deposit and withdrawal do post,
because they move value across the Bank/Payable boundary. This is documented in
code.

## Validations & errors (reusing existing `AppError` variants)

- amount positive, 2dp (`normalize_amount` → `BadRequest`)
- account(s) exist → else `NotFound`
- status must be `Active` → `Frozen` ⇒ `AccountFrozen`; other non-active ⇒
  `InvalidAccountStatus`
- account type not `credit_card` → `BadRequest`
- withdrawal / transfer: `available_balance >= amount` → else `InsufficientFunds`
- transfer: `from_account_id != to_account_id` → else `BadRequest`
- transfer idempotency: `idempotency_key` (when supplied) is stored in
  `transactions.metadata->>'idempotency_key'`; a replay with the same key
  returns the already-posted transaction (HTTP 200) instead of double-posting.

## Full limits enforcement

- Lazily ensure an `account_limits` row per customer account with table
  defaults: `INSERT INTO account_limits (account_id) VALUES ($1)
  ON CONFLICT (account_id) DO NOTHING` (unique index on `account_id`).
- **Reset-on-read** using `last_reset_date`: if the date rolled to a new day
  zero `daily_*_used`; new month zero `monthly_transfer_used`; new year zero
  `annual_transfer_used`; then set `last_reset_date = CURRENT_DATE`.
- Checks:
  - withdrawal → `daily_withdrawal_used + amount <= daily_withdrawal_limit`
  - transfer (from side) → daily **and** monthly **and** annual transfer limits
  - over any limit ⇒ `TransactionLimitExceeded`
- Increment the relevant `*_used` counters after a successful post. The
  `chk_used_within_limits` CHECK is the DB backstop (a 23514 maps to
  `TransactionLimitExceeded`).
- Deposits are not limited.
- `daily_transaction_summaries` upsert (`ON CONFLICT (account_id, summary_date)`)
  for each **customer** account touched: accumulate `total_debits` /
  `total_credits`, `transaction_count`, `largest_debit` / `largest_credit`, and
  set `end_of_day_balance` to the post-transaction balance. System/cash accounts
  are excluded.

All limit + summary writes happen inside the same DB transaction as the posting.

## GET history

`TransactionHistoryQuery`: `account_id?`, `start_date?`, `end_date?`,
`transaction_type?`, `status?`, `limit?` (1–100, default e.g. 50), `offset?`
(default 0). Built with sqlx `QueryBuilder`:

- `account_id` filter joins `transaction_entries` (a transaction matches if it
  has an entry for that account); `SELECT DISTINCT`.
- other filters are ANDed; `ORDER BY created_at DESC`, `LIMIT`/`OFFSET`.
- a parallel `COUNT` query (same filters) yields `total_count`; `has_more` /
  `next_offset` derived from `offset + returned` vs `total_count`.
- each returned transaction is hydrated with its `transaction_entries`
  (mapped to `TransactionEntryResponse`).

## Testing

Both, per request:

1. **Rust integration tests** — `api/tests/transactions.rs`, using `reqwest`
   against a running stack (`:8081` + Kind Postgres + a core). Because the
   crate is a binary (its items aren't importable from `tests/`) and
   deposit/withdrawal need a live core, tests drive the real HTTP surface. Each
   test **probes `GET /health` first and skips (returns) when unreachable**, so
   `cargo test` still passes with no stack up. Coverage: deposit → transfer →
   withdrawal balance math; insufficient funds; inactive-account and
   credit-card rejection; same-account transfer; idempotent transfer replay;
   limit-exceeded; history filters/pagination.
   Trade-off: no assertions run unless the stack is up (accepted — avoids a
   lib/bin refactor).
2. **curl smoke script** — `testing/transactions_smoke.sh`: create customer +
   two accounts, deposit, transfer, withdraw, then `GET` history, asserting
   balances and a couple of error cases. Plus appended request blocks in
   `nano-bank.http`.

## Files

- `api/src/handlers/transactions.rs` — implementation (main work)
- `api/tests/transactions.rs` — new integration tests
- `testing/transactions_smoke.sh` — new end-to-end smoke script
- `nano-bank.http` — appended transaction requests
- `api/CLAUDE.md` / root `CLAUDE.md` — note transactions are now wired
  (deposit/withdrawal through the port; transfer local-only and why)

## Out of scope

- Reversals / fees endpoints (`transaction_reversals`, `transaction_fees`
  tables exist but no endpoints are requested here).
- Auth: `initiated_by` / ownership still comes from the request body (no JWT
  principal yet), consistent with the rest of the API.
