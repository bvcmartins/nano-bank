-- ============================================================================
-- Part 14: Agent-action ledger — a hash-chained, append-only, tamper-evident
-- record of EVERY state-changing action any C-suite agent takes (COO operational
-- levers, CFO close_period, and any future agent write).
--
-- Out of bounds for all agents by construction: it is written only server-side,
-- through append_agent_action(); no MCP tool (COO or CFO) reads or writes it, and
-- the back-office read plane does not expose it. "Blockchain" in the audit sense
-- — each entry hashes the previous, so altering any row breaks every hash after
-- it, and a trigger blocks UPDATE/DELETE outright.
-- ============================================================================

CREATE TABLE agent_action_ledger (
    seq        BIGINT PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL,
    actor      TEXT NOT NULL,                       -- 'coo' | 'cfo' | …
    action     TEXT NOT NULL,                       -- 'cut_aft_batch' | 'close_period' | …
    params     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- what was requested
    effect     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- outcome, or {"refused": "<reason>"}
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE SEQUENCE agent_action_ledger_seq OWNED BY agent_action_ledger.seq;

-- Canonical string hashed for an entry: seq | ts(UTC, fixed fmt) | actor | action
-- | params | effect | prev_hash. jsonb::text is normalised (stable key order), and
-- the fixed UTC timestamp format makes the hash reproducible for verification.
CREATE OR REPLACE FUNCTION _agent_ledger_canon(
    p_seq BIGINT, p_ts TIMESTAMPTZ, p_actor TEXT, p_action TEXT,
    p_params JSONB, p_effect JSONB, p_prev TEXT
) RETURNS TEXT AS $$
    SELECT p_seq::text || '|'
        || to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '|'
        || p_actor || '|' || p_action || '|'
        || coalesce(p_params::text, '{}') || '|'
        || coalesce(p_effect::text, '{}') || '|' || p_prev;
$$ LANGUAGE sql IMMUTABLE;

-- Append one entry, chained to the last. An advisory xact lock serialises appends
-- so concurrent writers can't fork the chain. Returns the new seq + entry_hash.
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
$$ LANGUAGE plpgsql;

-- Immutability: no row may ever change or be removed.
CREATE OR REPLACE FUNCTION _agent_ledger_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'agent_action_ledger is append-only and immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_action_ledger_immutable
    BEFORE UPDATE OR DELETE ON agent_action_ledger
    FOR EACH ROW EXECUTE FUNCTION _agent_ledger_immutable();

-- Integrity check (for a human/admin, never an agent): recompute the chain and
-- return the first seq whose hash breaks, or NULL if the whole chain is intact.
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
$$ LANGUAGE plpgsql;
