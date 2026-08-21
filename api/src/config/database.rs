use crate::config::Settings;
use sqlx::postgres::{PgPool, PgPoolOptions};
use sqlx::Error as SqlxError;
use std::time::Duration;
use tracing::{info, warn};

pub type DatabasePool = PgPool;

pub async fn create_connection_pool(settings: &Settings) -> Result<DatabasePool, SqlxError> {
    info!("Creating database connection pool...");

    let database_url = settings.database_url();

    let pool = PgPoolOptions::new()
        .max_connections(settings.database.max_connections)
        .min_connections(settings.database.min_connections)
        .acquire_timeout(Duration::from_secs(settings.database.acquire_timeout))
        .idle_timeout(Duration::from_secs(600)) // 10 minutes
        .max_lifetime(Duration::from_secs(1800)) // 30 minutes
        .connect(&database_url)
        .await?;

    // Test the connection
    match sqlx::query("SELECT 1").fetch_one(&pool).await {
        Ok(_) => {
            info!("Database connection pool created successfully");
            info!("Connected to database: {}", settings.database.database_name);
        }
        Err(e) => {
            warn!("Failed to test database connection: {}", e);
            return Err(e);
        }
    }

    Ok(pool)
}

pub async fn run_migrations(pool: &DatabasePool) -> Result<(), sqlx::Error> {
    info!("Running database migrations...");

    // Note: In a real application, you would run actual migrations here
    // For now, we'll just verify that the tables exist
    let table_check = sqlx::query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'customers'")
        .fetch_optional(pool)
        .await?;

    match table_check {
        Some(_) => {
            info!("Database tables found - migrations appear to be complete");
        }
        None => {
            warn!("Database tables not found - please run the SQL scripts manually");
            warn!("Run the scripts in ~/dev/nano-bank/src/core/tables/ in order");
        }
    }

    // Self-heal the auth credentials table. The canonical DDL lives in
    // src/core/tables/02_customers.sql for fresh deploys, but issuing it here
    // (idempotently) means a DB initialised before auth existed picks up the
    // table on the next `cargo run` without a redeploy — same pattern as
    // handlers::cards::ensure_system_accounts.
    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS customer_credentials (
            customer_id UUID PRIMARY KEY REFERENCES customers(customer_id) ON DELETE CASCADE,
            password_hash VARCHAR(255) NOT NULL,
            password_changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        "#,
    )
    .execute(pool)
    .await?;

    // Self-heal the agentic-banking tables (canonical DDL: 11_agents.sql), so a
    // DB initialised before the agent plane existed picks them up on next boot.
    // Statements run one at a time: ALTER TYPE ... ADD VALUE can't share a
    // transaction with other statements.
    for ddl in [
        r#"
        CREATE TABLE IF NOT EXISTS agents (
            agent_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            display_name VARCHAR(100) NOT NULL,
            description  TEXT,
            secret_hash  VARCHAR(64) NOT NULL,
            kind         VARCHAR(20) NOT NULL DEFAULT 'external'
                         CHECK (kind IN ('external', 'first_party')),
            status       VARCHAR(20) NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'disabled')),
            created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        "#,
        r#"
        CREATE TABLE IF NOT EXISTS mandates (
            mandate_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id     UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
            agent_id        UUID NOT NULL REFERENCES agents(agent_id),
            account_id      UUID NOT NULL REFERENCES accounts(account_id),
            scopes          TEXT[] NOT NULL,
            max_per_tx      DECIMAL(15,2),
            daily_cap       DECIMAL(15,2),
            allowed_payees  UUID[],
            daily_used      DECIMAL(15,2) NOT NULL DEFAULT 0,
            last_reset_date DATE NOT NULL DEFAULT CURRENT_DATE,
            status          VARCHAR(20) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'revoked', 'expired')),
            expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            revoked_at      TIMESTAMP WITH TIME ZONE,
            CONSTRAINT chk_mandate_expiry CHECK (expires_at > created_at),
            CONSTRAINT chk_mandate_revoked_logic CHECK (
                (status = 'revoked' AND revoked_at IS NOT NULL) OR
                (status <> 'revoked' AND revoked_at IS NULL)
            ),
            CONSTRAINT chk_mandate_daily_used CHECK (
                daily_used >= 0 AND (daily_cap IS NULL OR daily_used <= daily_cap)
            )
        )
        "#,
        "CREATE INDEX IF NOT EXISTS idx_mandates_customer_id ON mandates(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_mandates_agent_id ON mandates(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_mandates_active ON mandates(status, expires_at)",
        r#"
        CREATE TABLE IF NOT EXISTS agent_actions (
            action_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mandate_id     UUID NOT NULL REFERENCES mandates(mandate_id),
            agent_id       UUID NOT NULL REFERENCES agents(agent_id),
            customer_id    UUID NOT NULL,
            account_id     UUID NOT NULL,
            operation      VARCHAR(50) NOT NULL,
            amount         DECIMAL(15,2),
            decision       VARCHAR(20) NOT NULL
                           CHECK (decision IN ('allowed', 'denied', 'step_up_required')),
            reason         TEXT,
            transaction_id UUID,
            created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        "#,
        "CREATE INDEX IF NOT EXISTS idx_agent_actions_mandate \
         ON agent_actions(mandate_id, created_at)",
        "ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'grant_mandate'",
        "ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'revoke_mandate'",
        // Additive: DBs whose mandates table predates the Phase-2 payee allowlist.
        "ALTER TABLE mandates ADD COLUMN IF NOT EXISTS allowed_payees UUID[]",
        // Economics tag columns (interest / NIM engine, spec #2). Additive; existing rows stay NULL.
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS product TEXT",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS cost_centre TEXT",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS economic_event_id UUID",
        "CREATE INDEX IF NOT EXISTS idx_transactions_event ON transactions(economic_event_id)",
        // Card auth_id -> capture, for the fraud-link rail resolver's cards arm
        // (#70). Partial: only card captures carry the key. Without it every
        // card linkage lookup is a sequential scan of `transactions`.
        "CREATE INDEX IF NOT EXISTS idx_transactions_auth_id \
         ON transactions((metadata->>'auth_id')) WHERE metadata ? 'auth_id'",
        // Carries the fraud linkage across the origination → settlement boundary
        // for AFT (#54): an entry is screened when it is created but writes no
        // transactions row until the batch settles, so without somewhere to rest
        // the engine's operation_id in between, the decision becomes unreachable.
        // Additive; entries written before this stay NULL, which reads correctly
        // as "no linkage recorded".
        "ALTER TABLE aft_entries ADD COLUMN IF NOT EXISTS metadata JSONB",
        // Carries the fraud linkage across the authorize → capture boundary for
        // cards (#54): screening happens in one request and the transactions row
        // is written in another, so without somewhere to rest the engine's
        // operation_id in between, the decision becomes unreachable. Additive;
        // holds written before this stay NULL, which reads correctly as "no
        // linkage recorded".
        "ALTER TABLE account_holds ADD COLUMN IF NOT EXISTS metadata JSONB",
        // Transfer idempotency guard (canonical DDL in 04_transactions.sql). Closes
        // the find-then-insert race: a concurrent same-key transfer trips this unique
        // index instead of double-posting the transfer + its fee. NULLS NOT DISTINCT
        // (PG16) collapses the no-mandate case; partial so the `fee` row and
        // non-idempotent transfers are excluded.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_transfer_idempotency \
         ON transactions (initiated_by, (metadata->>'idempotency_key'), (metadata->>'mandate_id')) \
         NULLS NOT DISTINCT \
         WHERE transaction_type = 'transfer' AND (metadata->>'idempotency_key') IS NOT NULL",
        // Phase 3: step-up pending approvals.
        r#"
        CREATE TABLE IF NOT EXISTS pending_approvals (
            approval_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mandate_id      UUID NOT NULL REFERENCES mandates(mandate_id),
            agent_id        UUID NOT NULL REFERENCES agents(agent_id),
            customer_id     UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
            account_id      UUID NOT NULL REFERENCES accounts(account_id),
            to_account_id   UUID NOT NULL,
            amount          DECIMAL(15,2) NOT NULL CHECK (amount > 0),
            description     TEXT NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            reason          TEXT NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'executing', 'approved', 'declined', 'expired')),
            transaction_id  UUID,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
            resolved_at     TIMESTAMP WITH TIME ZONE
        )
        "#,
        // The one-open-ask invariant covers pending AND executing (an ask being
        // executed must still swallow same-key retries). The old pending-only
        // index is dropped by its former name; the create is idempotent.
        "DROP INDEX IF EXISTS idx_pending_approvals_open_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_approvals_open_ask \
         ON pending_approvals(mandate_id, idempotency_key) \
         WHERE status IN ('pending', 'executing')",
        "CREATE INDEX IF NOT EXISTS idx_pending_approvals_customer \
         ON pending_approvals(customer_id, created_at)",
        // Denial telemetry outbox. Written in the SAME statement as the audit
        // row it mirrors (the CTE in policy.rs), drained to the fraud engine by
        // handlers/fraud_admin.rs. An outbox rather than a fire-and-forget POST
        // because loss would correlate with load, and load is when probing
        // happens — the signal would thin out exactly when it mattered.
        r#"
        CREATE TABLE IF NOT EXISTS agent_denial_outbox (
            outbox_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_id       UUID NOT NULL REFERENCES agent_actions(action_id) ON DELETE CASCADE,
            event_key       VARCHAR(255) NOT NULL UNIQUE,
            payload         JSONB NOT NULL,
            delivered       BOOLEAN NOT NULL DEFAULT FALSE,
            delivery_attempts   INTEGER NOT NULL DEFAULT 0,
            last_delivery_error TEXT,
            delivered_at        TIMESTAMP WITH TIME ZONE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        "#,
        "CREATE INDEX IF NOT EXISTS idx_agent_denial_outbox_undelivered \
         ON agent_denial_outbox (delivered, created_at) WHERE delivered = FALSE",
        // Migrate DBs whose CHECK predates the transient 'executing' claim
        // state ('approved' is only ever written together with transaction_id).
        // DROP + re-ADD each boot: the pair is idempotent, and the ADD tolerates
        // a concurrent boot having re-added it first (duplicate_object).
        "ALTER TABLE pending_approvals \
         DROP CONSTRAINT IF EXISTS pending_approvals_status_check",
        r#"
        DO $$ BEGIN
            ALTER TABLE pending_approvals ADD CONSTRAINT pending_approvals_status_check
            CHECK (status IN ('pending', 'executing', 'approved', 'declined', 'expired'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        "#,
        // Lease marker for the 'executing' claim (timed reclaim of dead claims).
        "ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS \
         claimed_at TIMESTAMP WITH TIME ZONE",
        // Saved Interac payees (address book). Self-heal for DBs predating the
        // 12_interac_recipients DDL, and migrate the old table-level UNIQUE to a
        // partial unique index so soft-deleted rows don't block re-registration.
        r#"
        CREATE TABLE IF NOT EXISTS interac_recipients (
            recipient_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id  UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
            email        TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        "#,
        "CREATE INDEX IF NOT EXISTS idx_interac_recipients_customer \
         ON interac_recipients(customer_id)",
        "ALTER TABLE interac_recipients \
         DROP CONSTRAINT IF EXISTS interac_recipients_customer_id_email_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_interac_recipients_active \
         ON interac_recipients(customer_id, email) WHERE status = 'active'",
        // Interest / NIM engine (spec #2): per-account accrual subledger + batch
        // run ledgers. Self-heal for DBs predating the 13_interest_accruals DDL.
        r#"
        CREATE TABLE IF NOT EXISTS interest_accruals (
            accrual_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id        UUID NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
            accrual_date      DATE NOT NULL,
            product           TEXT NOT NULL,
            cost_centre       TEXT NOT NULL,
            principal         DECIMAL(15,2) NOT NULL,
            rate              DECIMAL(5,4) NOT NULL,
            amount            DECIMAL(15,2) NOT NULL,
            side              TEXT NOT NULL,
            economic_event_id UUID NOT NULL,
            capitalised       BOOLEAN NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_accrual_amount_precision CHECK (amount = ROUND(amount, 2))
        )
        "#,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_interest_accruals_acct_date \
         ON interest_accruals(account_id, accrual_date)",
        "CREATE INDEX IF NOT EXISTS idx_interest_accruals_uncap \
         ON interest_accruals(account_id) WHERE capitalised = FALSE",
        r#"
        CREATE TABLE IF NOT EXISTS accrual_runs (
            accrual_date      DATE PRIMARY KEY,
            economic_event_id UUID NOT NULL,
            expense_total     DECIMAL(15,2) NOT NULL,
            income_total      DECIMAL(15,2) NOT NULL,
            status            TEXT NOT NULL DEFAULT 'completed',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        "#,
        r#"
        CREATE TABLE IF NOT EXISTS capitalisation_runs (
            period            TEXT PRIMARY KEY,
            economic_event_id UUID NOT NULL,
            deposit_total     DECIMAL(15,2) NOT NULL,
            asset_total       DECIMAL(15,2) NOT NULL,
            maintenance_total DECIMAL(15,2) NOT NULL,
            status            TEXT NOT NULL DEFAULT 'completed',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        "#,
        // Notification-outbox drainer bookkeeping. Additive self-heal for DBs
        // predating the flush-notifications consumer: a retry counter (its budget
        // caps a permanently-failing send — a dead-letter, not an infinite loop),
        // the last error for observability, and a delivery timestamp.
        "ALTER TABLE interac_notifications \
         ADD COLUMN IF NOT EXISTS delivery_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE interac_notifications \
         ADD COLUMN IF NOT EXISTS last_delivery_error TEXT",
        "ALTER TABLE interac_notifications \
         ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP WITH TIME ZONE",
        // Registration provenance, and what the per-address registration
        // throttle counts (handlers/agents.rs). Nullable: rows registered before
        // this existed simply do not count toward anyone's window.
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS registered_ip INET",
        "CREATE INDEX IF NOT EXISTS idx_agents_registered_ip \
         ON agents (registered_ip, created_at) WHERE registered_ip IS NOT NULL",
        // Idempotent "open account": a retried request (same customer, same key)
        // must return the original account, not open a second one. Additive
        // self-heal for DBs whose accounts table predates the key — the column
        // is nullable so existing rows and unkeyed callers stay NULL, and NULLs
        // are distinct in the partial index so they never collide.
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_idempotency \
         ON accounts (customer_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
        // Bank-wide decline log (canonical DDL: 15_decline_events.sql). Written
        // best-effort off the request path; self-heal so a DB predating it starts
        // collecting instead of silently swallowing every decline. Idempotent.
        r#"
        CREATE TABLE IF NOT EXISTS decline_events (
            decline_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            channel          TEXT NOT NULL,
            reason_code      TEXT NOT NULL,
            reason_category  TEXT NOT NULL,
            account_id       UUID,
            customer_id      UUID,
            amount           NUMERIC(20,2),
            currency         TEXT NOT NULL DEFAULT 'CAD',
            counterparty     TEXT,
            metadata         JSONB NOT NULL DEFAULT '{}'
        )
        "#,
        "CREATE INDEX IF NOT EXISTS idx_decline_events_occurred ON decline_events (occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_decline_events_channel ON decline_events (channel, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_decline_events_category \
         ON decline_events (reason_category, occurred_at)",
        // Held customer movements awaiting a reviewer's verdict (canonical DDL:
        // 16_pending_reviews.sql). Self-healed like the tables above so a DB
        // predating it starts parking on next boot rather than 500ing the two
        // rails that now park. Unlike the decline log this is NOT best-effort: a
        // park that fails to write must fail the request, or the customer is
        // told "under review" about a review that does not exist.
        r#"
        CREATE TABLE IF NOT EXISTS pending_reviews (
            review_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id     UUID NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
            account_id      UUID NOT NULL REFERENCES accounts(account_id),
            rail            TEXT NOT NULL CHECK (rail IN ('transfer', 'interac_etransfer')),
            amount          DECIMAL(15,2) NOT NULL CHECK (amount > 0),
            idempotency_key VARCHAR(128),
            movement        JSONB NOT NULL,
            operation_id    UUID NOT NULL UNIQUE,
            decision_id     UUID,
            status          VARCHAR(20) NOT NULL DEFAULT 'held'
                            CHECK (status IN ('held','executing','executed','refused','expired')),
            transaction_id  UUID,
            resolution_note TEXT,
            claimed_at      TIMESTAMP WITH TIME ZONE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
            resolved_at     TIMESTAMP WITH TIME ZONE
        )
        "#,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_reviews_open \
         ON pending_reviews (customer_id, idempotency_key) \
         WHERE status IN ('held', 'executing') AND idempotency_key IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_pending_reviews_customer \
         ON pending_reviews (customer_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pending_reviews_waiting \
         ON pending_reviews (created_at) WHERE status = 'held'",
        // Hash-chained agent-action ledger (canonical DDL: 14_agent_action_ledger.sql).
        // Unlike the decline log this is NOT best-effort — the COO levers surface a
        // failed audit to the caller — so a DB predating the ledger would 500 every
        // lever. Self-heal the table, sequence, functions and immutability trigger so
        // the ledger exists on next boot without a manual DDL run. digest() needs
        // pgcrypto (already created by 00_init on fresh DBs; ensured here too).
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        r#"
        CREATE TABLE IF NOT EXISTS agent_action_ledger (
            seq        BIGINT PRIMARY KEY,
            ts         TIMESTAMPTZ NOT NULL,
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            params     JSONB NOT NULL DEFAULT '{}'::jsonb,
            effect     JSONB NOT NULL DEFAULT '{}'::jsonb,
            prev_hash  TEXT NOT NULL,
            entry_hash TEXT NOT NULL
        )
        "#,
        "CREATE SEQUENCE IF NOT EXISTS agent_action_ledger_seq OWNED BY agent_action_ledger.seq",
        r#"
        CREATE OR REPLACE FUNCTION _agent_ledger_canon(
            p_seq BIGINT, p_ts TIMESTAMPTZ, p_actor TEXT, p_action TEXT,
            p_params JSONB, p_effect JSONB, p_prev TEXT
        ) RETURNS TEXT AS $$
            SELECT p_seq::text || '|'
                || to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '|'
                || p_actor || '|' || p_action || '|'
                || coalesce(p_params::text, '{}') || '|'
                || coalesce(p_effect::text, '{}') || '|' || p_prev;
        $$ LANGUAGE sql IMMUTABLE
        "#,
        r#"
        CREATE OR REPLACE FUNCTION append_agent_action(
            p_actor TEXT, p_action TEXT, p_params JSONB, p_effect JSONB
        ) RETURNS TABLE(seq BIGINT, entry_hash TEXT) AS $$
        DECLARE
            v_prev TEXT;
            v_ts   TIMESTAMPTZ := clock_timestamp();
            v_seq  BIGINT;
            v_hash TEXT;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtext('agent_action_ledger'));
            SELECT l.entry_hash INTO v_prev FROM agent_action_ledger l ORDER BY l.seq DESC LIMIT 1;
            v_prev := COALESCE(v_prev, 'GENESIS');
            v_seq  := nextval('agent_action_ledger_seq');
            v_hash := encode(digest(
                _agent_ledger_canon(v_seq, v_ts, p_actor, p_action, p_params, p_effect, v_prev),
                'sha256'), 'hex');
            INSERT INTO agent_action_ledger(seq, ts, actor, action, params, effect, prev_hash, entry_hash)
            VALUES (v_seq, v_ts, p_actor, p_action,
                    COALESCE(p_params, '{}'::jsonb), COALESCE(p_effect, '{}'::jsonb), v_prev, v_hash);
            RETURN QUERY SELECT v_seq, v_hash;
        END;
        $$ LANGUAGE plpgsql
        "#,
        r#"
        CREATE OR REPLACE FUNCTION _agent_ledger_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'agent_action_ledger is append-only and immutable';
        END;
        $$ LANGUAGE plpgsql
        "#,
        "DROP TRIGGER IF EXISTS trg_agent_action_ledger_immutable ON agent_action_ledger",
        "CREATE TRIGGER trg_agent_action_ledger_immutable \
         BEFORE UPDATE OR DELETE ON agent_action_ledger \
         FOR EACH ROW EXECUTE FUNCTION _agent_ledger_immutable()",
        r#"
        CREATE OR REPLACE FUNCTION verify_agent_ledger() RETURNS BIGINT AS $$
        DECLARE
            r      RECORD;
            v_prev TEXT := 'GENESIS';
            v_calc TEXT;
        BEGIN
            FOR r IN SELECT * FROM agent_action_ledger ORDER BY seq ASC LOOP
                v_calc := encode(digest(
                    _agent_ledger_canon(r.seq, r.ts, r.actor, r.action, r.params, r.effect, r.prev_hash),
                    'sha256'), 'hex');
                IF r.prev_hash <> v_prev OR r.entry_hash <> v_calc THEN
                    RETURN r.seq;
                END IF;
                v_prev := r.entry_hash;
            END LOOP;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        "#,
    ] {
        sqlx::query(ddl).execute(pool).await?;
    }

    Ok(())
}

pub async fn health_check(pool: &DatabasePool) -> Result<(), SqlxError> {
    sqlx::query("SELECT 1").fetch_one(pool).await?;
    Ok(())
}
