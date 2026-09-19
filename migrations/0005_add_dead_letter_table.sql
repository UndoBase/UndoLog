-- =============================================================================
-- Migration: Add dead-letter table for failed compensations
-- =============================================================================
-- When a compensation exhausts its retries, the effect is moved to this
-- table for inspection, retry, or skip operations.
-- =============================================================================

CREATE TABLE IF NOT EXISTS undolog_dead_letters (
    dead_letter_id   UUID PRIMARY KEY DEFAULT uuidv7_or_random(),
    org_id           UUID NOT NULL REFERENCES undolog_orgs(org_id),
    session_id       UUID NOT NULL REFERENCES undolog_sessions(session_id),
    effect_id        UUID NOT NULL,  -- references undolog_effect_log(effect_id); FK omitted because effect_id alone is not unique (composite PK includes executed_at)
    undo_id          UUID NOT NULL,
    compensation_fn  TEXT NOT NULL,
    compensation_version TEXT NOT NULL,
    compensation_args JSONB NOT NULL DEFAULT '{}',
    error_message    TEXT NOT NULL,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    state            TEXT NOT NULL DEFAULT 'failed' CHECK (state IN ('failed', 'retrying', 'skipped')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for querying by org and state
CREATE INDEX IF NOT EXISTS idx_dead_letters_org_state
    ON undolog_dead_letters (org_id, state);

-- Index for querying by session
CREATE INDEX IF NOT EXISTS idx_dead_letters_session
    ON undolog_dead_letters (session_id);

-- Trigger to update updated_at on state changes
CREATE OR REPLACE FUNCTION update_dead_letter_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER dead_letter_updated_at_trigger
    BEFORE UPDATE ON undolog_dead_letters
    FOR EACH ROW
    EXECUTE FUNCTION update_dead_letter_updated_at();
