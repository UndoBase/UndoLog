-- Add retry configuration columns to undolog_undo_stack.
-- These are set at insertion time by the orchestrator and read
-- during compensation retry loops.

ALTER TABLE undolog_undo_stack
  ADD COLUMN max_retries      smallint NOT NULL DEFAULT 3,
  ADD COLUMN retry_backoff_ms integer  NOT NULL DEFAULT 1000;
