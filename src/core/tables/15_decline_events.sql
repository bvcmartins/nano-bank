-- Bank-wide decline log: every declined authorization / money movement, across
-- cards and rails. Append-only; readers only aggregate. NEVER stores fraud
-- scores/rules — only the operational fact of a decline. reason_category is the
-- reporting bucket; the COO read surface folds 'risk' into 'other'.
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
);
CREATE INDEX IF NOT EXISTS idx_decline_events_occurred ON decline_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_decline_events_channel  ON decline_events (channel, occurred_at);
CREATE INDEX IF NOT EXISTS idx_decline_events_category ON decline_events (reason_category, occurred_at);
