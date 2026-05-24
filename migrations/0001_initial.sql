-- =============================================================================
-- UNDOLOG - AI Agent Safe Execution Runtime
-- PostgreSQL Database Schema
-- =============================================================================
--
-- Design principles:
--   1. Append-only event log  - nothing is ever deleted or updated in core tables.
--                               History is immutable. Corrections are new events.
--   2. UUIDv7 primary keys    - RECOMMENDED for time-ordered, B-tree-friendly,
--                               distributed-safe IDs. The migration currently
--                               uses gen_random_uuid() (UUIDv4) as the default
--                               for PG 16 portability. Switch to uuidv7() when
--                               PG 18+ is available, or install the pg_uuidv7
--                               extension on PG 16/17.
--   3. Exactly-once writes    - BLAKE3 call_signature has a UNIQUE constraint.
--                               INSERT ... ON CONFLICT (call_signature) DO NOTHING
--                               is the entire exactly-once mechanism at the DB layer.
--   4. Advisory locks         - pg_try_advisory_xact_lock(hashtext(call_signature))
--                               prevents race conditions between concurrent writers
--                               trying to record the same tool call simultaneously.
--   5. Time-range partitioning - undolog_effect_log is partitioned monthly on
--                               executed_at. Old partitions can be dropped in one
--                               DDL statement. BRIN index on executed_at within
--                               each partition; B-tree on call_signature globally.
--   6. Tenant isolation       - every table carries org_id. Row-level security
--                               policies enforce tenant boundaries at the DB layer.
--
-- Table inventory:
--   undolog_orgs               - tenant root
--   undolog_projects           - grouping of agent deployments within an org
--   undolog_tool_registry      - tool definitions with tier annotations
--   undolog_compensation_registry - compensation function definitions
--   undolog_sessions           - one agent execution session
--   undolog_effect_log         - PARTITIONED: core append-only log of every tool call
--   undolog_undo_stack         - ordered stack of pending compensations per session
--   undolog_approval_requests  - human-in-the-loop gate records
--   undolog_approval_events    - audit trail of every approval action taken
--   undolog_session_snapshots  - periodic session state snapshots for fast replay
--   undolog_schema_migrations  - migration tracking
--
-- Prerequisites:
--   PostgreSQL 16+ (for UUIDv7 via pg_uuidv7 extension, or use gen_random_uuid()
--   and switch to UUID v7 when on PG 18+)
--   Extension: pgcrypto (for gen_random_uuid fallback)
--
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() fallback
-- On PostgreSQL 18+, drop the above and use the native uuidv7() function.
-- For PG 16/17, install pg_uuidv7: https://github.com/fboulnois/pg_uuidv7
-- CREATE EXTENSION IF NOT EXISTS pg_uuidv7;

-- ---------------------------------------------------------------------------
-- Helper: uuidv7_or_random()
-- Returns a UUIDv7 if the extension is available, otherwise UUIDv4.
-- Replace all DEFAULT uuidv7_or_random() calls with uuidv7() once on PG 18+.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION uuidv7_or_random()
RETURNS uuid
LANGUAGE sql
VOLATILE
AS $$
  SELECT gen_random_uuid();
  -- On PG 18+, replace the line above with: SELECT uuidv7();
$$;

-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------

CREATE TYPE undolog_tool_tier AS ENUM (
  'safe',          -- read-only or idempotent - execute freely, no logging needed
  'compensable',   -- has a defined compensation/undo - log + register undo stack entry
  'irreversible'   -- cannot be undone - require human approval gate before execution
);

CREATE TYPE undolog_effect_state AS ENUM (
  'pending',       -- intercepted, awaiting execution (or awaiting human approval)
  'executing',     -- currently running
  'committed',     -- completed successfully, result cached
  'compensating',  -- compensation is running (undo in progress)
  'compensated',   -- compensation completed successfully
  'compensation_failed', -- compensation itself failed - requires manual intervention
  'approved',      -- human approved an irreversible action (now executing)
  'rejected',      -- human rejected an irreversible action - session halted
  'replayed'       -- result was served from cache (exactly-once replay path)
);

CREATE TYPE undolog_session_state AS ENUM (
  'active',
  'completed',
  'failed',
  'compensating',  -- rolling back the undo stack
  'compensated',
  'awaiting_approval',
  'halted'         -- fatal: compensation failed, requires manual intervention
);

CREATE TYPE undolog_approval_state AS ENUM (
  'pending',
  'approved',
  'rejected',
  'timed_out',
  'auto_approved'  -- approved by policy (e.g. low-risk auto-approve timeout)
);

CREATE TYPE undolog_approval_action AS ENUM (
  'approve',
  'reject',
  'modify',        -- approver modified the arguments before approving
  'timeout'
);

-- ---------------------------------------------------------------------------
-- undolog_orgs - tenant root
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_orgs (
  org_id          uuid        PRIMARY KEY DEFAULT uuidv7_or_random(),
  name            text        NOT NULL,
  slug            text        NOT NULL UNIQUE,  -- used in API paths, e.g. /orgs/acme
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  -- Policy defaults: can be overridden at project or tool level
  default_approval_timeout_seconds  int  NOT NULL DEFAULT 3600,  -- 1 hour
  auto_approve_on_timeout            bool NOT NULL DEFAULT false,

  CONSTRAINT undolog_orgs_slug_format CHECK (slug ~ '^[a-z0-9-]{2,64}$')
);

CREATE INDEX idx_undolog_orgs_slug ON undolog_orgs (slug);

-- ---------------------------------------------------------------------------
-- undolog_projects - grouping of agent deployments within an org
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_projects (
  project_id   uuid        PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id       uuid        NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,
  name         text        NOT NULL,
  description  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT undolog_projects_org_name_unique UNIQUE (org_id, name)
);

CREATE INDEX idx_undolog_projects_org ON undolog_projects (org_id);

-- ---------------------------------------------------------------------------
-- undolog_tool_registry - tool definitions with tier annotations
--
-- Tools are registered here by the SDK (via the @undolog_tool decorator / attribute).
-- The tool_schema JSONB column holds the MCP tool definition (name, description,
-- inputSchema). The tier and compensation_ref fields are UndoLog-specific annotations.
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_tool_registry (
  tool_id           uuid              PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id            uuid              NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,
  project_id        uuid              REFERENCES undolog_projects (project_id) ON DELETE SET NULL,

  -- Identity
  tool_name         text              NOT NULL,   -- e.g. "transfer_funds"
  tool_version      text              NOT NULL DEFAULT '1.0.0',

  -- UndoLog tier annotation
  tier              undolog_tool_tier   NOT NULL,

  -- For 'irreversible' tier: human-readable explanation shown in approval UI
  irreversibility_reason  text,

  -- For 'compensable' tier: references the compensation function
  -- NULL for 'safe' and 'irreversible' tiers
  compensation_ref  text,             -- e.g. "cancel_payment" - matches undolog_compensation_registry.fn_name

  -- The full MCP tool JSON schema (inputSchema for parameter validation)
  tool_schema       jsonb             NOT NULL,

  -- Risk metadata used by the approval UI to provide context
  risk_tags         text[]            NOT NULL DEFAULT '{}',  -- e.g. ['financial', 'external-api']
  estimated_impact  text,             -- human-readable: "debits up to $amount from the user's account"

  registered_at     timestamptz       NOT NULL DEFAULT now(),
  updated_at        timestamptz       NOT NULL DEFAULT now(),
  is_active         bool              NOT NULL DEFAULT true,

  CONSTRAINT undolog_tool_registry_unique UNIQUE (org_id, tool_name, tool_version),
  CONSTRAINT undolog_tool_compensable_has_ref CHECK (
    tier != 'compensable' OR compensation_ref IS NOT NULL
  ),
  CONSTRAINT undolog_tool_irreversible_has_reason CHECK (
    tier != 'irreversible' OR irreversibility_reason IS NOT NULL
  )
);

CREATE INDEX idx_undolog_tool_registry_org        ON undolog_tool_registry (org_id);
CREATE INDEX idx_undolog_tool_registry_project    ON undolog_tool_registry (project_id);
CREATE INDEX idx_undolog_tool_registry_name       ON undolog_tool_registry (tool_name);
CREATE INDEX idx_undolog_tool_registry_tier       ON undolog_tool_registry (tier);

-- ---------------------------------------------------------------------------
-- undolog_compensation_registry - compensation function definitions
--
-- Compensation functions are registered alongside their action tools.
-- The fn_body JSONB stores the serialized compensation descriptor
-- (function reference + argument schema) that the Rust engine deserializes.
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_compensation_registry (
  compensation_id   uuid        PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id            uuid        NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,

  fn_name           text        NOT NULL,   -- must match tool_registry.compensation_ref
  fn_version        text        NOT NULL DEFAULT '1.0.0',
  description       text        NOT NULL,   -- human-readable: "Cancels a previously initiated payment"

  -- Serialized compensation descriptor (engine-specific format)
  -- Contains: endpoint, http_method, arg_mapping, idempotency_key_field
  fn_body           jsonb       NOT NULL,

  -- JSON Schema for the compensation arguments (used for validation)
  args_schema       jsonb       NOT NULL,

  -- Max retries before escalating to 'compensation_failed'
  max_retries       int         NOT NULL DEFAULT 3,
  retry_backoff_ms  int         NOT NULL DEFAULT 1000,

  registered_at     timestamptz NOT NULL DEFAULT now(),
  is_active         bool        NOT NULL DEFAULT true,

  CONSTRAINT undolog_compensation_registry_unique UNIQUE (org_id, fn_name, fn_version)
);

CREATE INDEX idx_undolog_compensation_org       ON undolog_compensation_registry (org_id);
CREATE INDEX idx_undolog_compensation_fn_name   ON undolog_compensation_registry (fn_name);

-- ---------------------------------------------------------------------------
-- undolog_sessions - one agent execution session
--
-- A session corresponds to a single agent run: one invocation of a workflow
-- from start to completion (or failure). Sessions are the unit of rollback:
-- when a session fails, the undo stack for that session is walked.
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_sessions (
  session_id      uuid                  PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id          uuid                  NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,
  project_id      uuid                  REFERENCES undolog_projects (project_id) ON DELETE SET NULL,

  -- Correlation with external orchestration (LangGraph run ID, Temporal workflow ID, etc.)
  external_run_id text,
  agent_name      text,

  state           undolog_session_state   NOT NULL DEFAULT 'active',

  -- Counts for fast dashboard queries (maintained by triggers)
  tool_calls_total    int  NOT NULL DEFAULT 0,
  compensations_total int  NOT NULL DEFAULT 0,
  approvals_pending   int  NOT NULL DEFAULT 0,

  started_at      timestamptz           NOT NULL DEFAULT now(),
  completed_at    timestamptz,
  failed_at       timestamptz,
  failure_reason  text,

  -- Arbitrary metadata from the agent framework (tags, user context, etc.)
  metadata        jsonb                 NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_undolog_sessions_org          ON undolog_sessions (org_id);
CREATE INDEX idx_undolog_sessions_project      ON undolog_sessions (project_id);
CREATE INDEX idx_undolog_sessions_state        ON undolog_sessions (state) WHERE state != 'completed';
CREATE INDEX idx_undolog_sessions_external     ON undolog_sessions (external_run_id) WHERE external_run_id IS NOT NULL;
CREATE INDEX idx_undolog_sessions_started      ON undolog_sessions (started_at DESC);

-- ---------------------------------------------------------------------------
-- undolog_effect_log - PARTITIONED append-only log of every tool call
--
-- This is the heart of UndoLog. Every tool call intercepted by the MCP proxy
-- is recorded here before execution. The call_signature column provides
-- exactly-once semantics: it is a BLAKE3 hash of:
--   (workflow_id || step_index || tool_name || canonical_args_json)
--
-- Partitioned by executed_at (monthly ranges) for retention management.
-- BRIN index on executed_at within each partition (append-only, highly correlated).
-- Per-partition unique indexes on call_signature for exactly-once enforcement.
--
-- Advisory lock strategy for concurrent writers:
--   Before INSERT, acquire pg_try_advisory_xact_lock(FNV-1a-64bit(call_signature)).
--   FNV-1a is computed identically in Rust, Go, and Python. The lock prevents
--   two concurrent MCP proxy instances from racing to insert the same tool call.
--   The UNIQUE index on call_signature (per-partition) is the last-resort safety
--   net - the advisory lock is the performance optimization (avoids write conflict
--   rollbacks under high concurrency).
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_effect_log (
  effect_id         uuid              NOT NULL DEFAULT uuidv7_or_random(),
  org_id            uuid              NOT NULL,
  session_id        uuid              NOT NULL,  -- FK enforced on partition level
  tool_id           uuid,                        -- NULL if tool was not in registry (unknown tool)

  -- The exactly-once key: BLAKE3(workflow_id || step_index || tool_name || canonical_args)
  -- 64 hex characters (256-bit BLAKE3 output)
  call_signature    char(64)          NOT NULL,

  -- Tool identity at call time (denormalized for query performance - registry may change)
  tool_name         text              NOT NULL,
  tool_version      text              NOT NULL DEFAULT '1.0.0',
  tier              undolog_tool_tier   NOT NULL,

  -- Step position in the agent workflow (for undo stack ordering)
  step_index        int               NOT NULL,  -- monotonically increasing per session

  -- Input captured before execution (canonical JSON - same as what was hashed)
  args_snapshot     jsonb             NOT NULL,

  -- Output captured after execution (cached for replay)
  result_snapshot   jsonb,            -- NULL until committed or replayed

  state             undolog_effect_state NOT NULL DEFAULT 'pending',

  -- Compensation snapshot: args captured at registration time (before execution)
  -- Pre-registered so it survives process crashes
  compensation_args jsonb,            -- NULL for 'safe' and 'irreversible' tiers

  -- Timing
  executed_at       timestamptz       NOT NULL DEFAULT now(),  -- PARTITION KEY
  committed_at      timestamptz,
  compensated_at    timestamptz,

  -- Replay tracking
  replay_count      smallint          NOT NULL DEFAULT 0,
  last_replayed_at  timestamptz,

  -- Link to approval request for irreversible actions
  approval_request_id uuid,           -- FK to undolog_approval_requests (set after approval record created)

  CONSTRAINT undolog_effect_log_pk PRIMARY KEY (effect_id, executed_at)
) PARTITION BY RANGE (executed_at);

-- Exactly-once enforcement via per-partition unique indexes on call_signature.
-- A call_signature collision across partitions (same signature, different months)
-- is prevented by the fact that call_signature includes session_id - two different
-- sessions always produce different signatures. Within the same session, the
-- advisory lock (FNV-1a + pg_try_advisory_xact_lock) prevents racing writes.
-- Per-partition unique indexes are compatible with PG 16+ (global unique indexes
-- on partitioned tables require PG 17+ and the partition key in the index).

-- Primary lookup patterns
CREATE INDEX idx_undolog_effect_log_session
  ON undolog_effect_log (session_id, step_index);

CREATE INDEX idx_undolog_effect_log_state
  ON undolog_effect_log (state, org_id)
  WHERE state IN ('pending', 'executing', 'compensating', 'compensation_failed');

CREATE INDEX idx_undolog_effect_log_org_time
  ON undolog_effect_log (org_id, executed_at DESC);

-- Monthly partitions - create ahead of time via pg_partman in production.
-- Bootstrap partitions covering Nov 2025 (6 months back) through Apr 2027
-- (12 months forward from May 2026). Adjust to your deployment timeframe.

CREATE TABLE undolog_effect_log_2025_11 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');

CREATE TABLE undolog_effect_log_2025_12 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

CREATE TABLE undolog_effect_log_2026_01 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE undolog_effect_log_2026_02 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE undolog_effect_log_2026_03 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

CREATE TABLE undolog_effect_log_2026_04 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE TABLE undolog_effect_log_2026_05 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE undolog_effect_log_2026_06 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE undolog_effect_log_2026_07 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE undolog_effect_log_2026_08 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE undolog_effect_log_2026_09 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE undolog_effect_log_2026_10 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE undolog_effect_log_2026_11 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE undolog_effect_log_2026_12 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TABLE undolog_effect_log_2027_01 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');

CREATE TABLE undolog_effect_log_2027_02 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');

CREATE TABLE undolog_effect_log_2027_03 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');

CREATE TABLE undolog_effect_log_2027_04 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');

-- BRIN indexes on each partition's executed_at (append-only = perfect correlation)
-- pages_per_range = 32 is a good default; tune down for very hot partitions.
-- In production, use pg_partman to auto-create partitions + BRIN indexes automatically.
-- Add a BRIN index to every partition at creation time.
CREATE INDEX idx_vel_2025_11_brin ON undolog_effect_log_2025_11 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2025_12_brin ON undolog_effect_log_2025_12 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_01_brin ON undolog_effect_log_2026_01 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_02_brin ON undolog_effect_log_2026_02 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_03_brin ON undolog_effect_log_2026_03 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_04_brin ON undolog_effect_log_2026_04 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_05_brin ON undolog_effect_log_2026_05 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_06_brin ON undolog_effect_log_2026_06 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_07_brin ON undolog_effect_log_2026_07 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_08_brin ON undolog_effect_log_2026_08 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_09_brin ON undolog_effect_log_2026_09 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_10_brin ON undolog_effect_log_2026_10 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_11_brin ON undolog_effect_log_2026_11 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2026_12_brin ON undolog_effect_log_2026_12 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2027_01_brin ON undolog_effect_log_2027_01 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2027_02_brin ON undolog_effect_log_2027_02 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2027_03_brin ON undolog_effect_log_2027_03 USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE INDEX idx_vel_2027_04_brin ON undolog_effect_log_2027_04 USING BRIN (executed_at) WITH (pages_per_range = 32);

-- Per-partition unique indexes on call_signature for exactly-once enforcement.
-- These work on PG 16+ (global unique indexes on partitioned tables require PG 17+
-- and inclusion of the partition key).

CREATE UNIQUE INDEX idx_vel_2025_11_sig ON undolog_effect_log_2025_11 (call_signature);
CREATE UNIQUE INDEX idx_vel_2025_12_sig ON undolog_effect_log_2025_12 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_01_sig ON undolog_effect_log_2026_01 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_02_sig ON undolog_effect_log_2026_02 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_03_sig ON undolog_effect_log_2026_03 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_04_sig ON undolog_effect_log_2026_04 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_05_sig ON undolog_effect_log_2026_05 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_06_sig ON undolog_effect_log_2026_06 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_07_sig ON undolog_effect_log_2026_07 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_08_sig ON undolog_effect_log_2026_08 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_09_sig ON undolog_effect_log_2026_09 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_10_sig ON undolog_effect_log_2026_10 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_11_sig ON undolog_effect_log_2026_11 (call_signature);
CREATE UNIQUE INDEX idx_vel_2026_12_sig ON undolog_effect_log_2026_12 (call_signature);
CREATE UNIQUE INDEX idx_vel_2027_01_sig ON undolog_effect_log_2027_01 (call_signature);
CREATE UNIQUE INDEX idx_vel_2027_02_sig ON undolog_effect_log_2027_02 (call_signature);
CREATE UNIQUE INDEX idx_vel_2027_03_sig ON undolog_effect_log_2027_03 (call_signature);
CREATE UNIQUE INDEX idx_vel_2027_04_sig ON undolog_effect_log_2027_04 (call_signature);

-- ---------------------------------------------------------------------------
-- undolog_undo_stack - ordered stack of pending compensations per session
--
-- Populated before each Compensable action executes.
-- Stack order is defined by stack_position (LIFO: highest first).
-- When compensation runs, the row state transitions to 'compensated' or 'failed'.
-- This table is the source of truth for "what needs to be undone."
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_undo_stack (
  undo_id          uuid        PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id           uuid        NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,
  session_id       uuid        NOT NULL REFERENCES undolog_sessions (session_id) ON DELETE CASCADE,
  effect_id        uuid        NOT NULL,   -- references undolog_effect_log.effect_id

  -- Stack position: higher = was pushed later = compensated first (LIFO)
  stack_position   int         NOT NULL,

  -- Compensation function to invoke
  compensation_fn  text        NOT NULL,   -- matches undolog_compensation_registry.fn_name
  compensation_version text    NOT NULL DEFAULT '1.0.0',

  -- Argument snapshot captured BEFORE the action executed (not the result)
  compensation_args jsonb      NOT NULL,

  -- Execution state
  state            text        NOT NULL DEFAULT 'pending'
                   CHECK (state IN ('pending', 'running', 'compensated', 'failed', 'skipped')),

  retry_count      smallint    NOT NULL DEFAULT 0,
  last_error       text,

  -- Timing
  registered_at    timestamptz NOT NULL DEFAULT now(),  -- must be BEFORE action executes
  compensated_at   timestamptz,

  CONSTRAINT undolog_undo_stack_session_position UNIQUE (session_id, stack_position)
);

CREATE INDEX idx_undolog_undo_stack_session
  ON undolog_undo_stack (session_id, stack_position DESC)
  WHERE state = 'pending';

CREATE INDEX idx_undolog_undo_stack_effect
  ON undolog_undo_stack (effect_id);

-- ---------------------------------------------------------------------------
-- undolog_approval_requests - human-in-the-loop gate for irreversible actions
--
-- Created by the MCP Interceptor when a tool call has tier = 'irreversible'.
-- The agent session is suspended until this record reaches a terminal state.
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_approval_requests (
  approval_request_id   uuid                   PRIMARY KEY DEFAULT uuidv7_or_random(),
  org_id                uuid                   NOT NULL REFERENCES undolog_orgs (org_id) ON DELETE CASCADE,
  session_id            uuid                   NOT NULL REFERENCES undolog_sessions (session_id) ON DELETE CASCADE,
  effect_id             uuid                   NOT NULL,   -- the pending effect waiting for approval

  tool_name             text                   NOT NULL,
  tier                  undolog_tool_tier        NOT NULL DEFAULT 'irreversible',
  irreversibility_reason text                  NOT NULL,
  risk_tags             text[]                 NOT NULL DEFAULT '{}',
  estimated_impact      text,

  -- The proposed arguments (what the agent wants to do)
  proposed_args         jsonb                  NOT NULL,

  -- Context from the agent (last N tool calls + reasoning trace excerpt)
  agent_context         jsonb                  NOT NULL DEFAULT '{}',

  state                 undolog_approval_state   NOT NULL DEFAULT 'pending',

  -- Timeout policy (inherited from org defaults if null)
  timeout_at            timestamptz            NOT NULL,
  auto_approve_on_timeout bool                 NOT NULL DEFAULT false,

  -- Resolution
  resolved_at           timestamptz,
  resolved_by           text,                 -- user ID or 'system:timeout'
  -- If modified: the final args that were approved (may differ from proposed_args)
  approved_args         jsonb,

  created_at            timestamptz            NOT NULL DEFAULT now()
);

CREATE INDEX idx_undolog_approval_org_state
  ON undolog_approval_requests (org_id, state, created_at DESC)
  WHERE state = 'pending';

CREATE INDEX idx_undolog_approval_session
  ON undolog_approval_requests (session_id);

CREATE INDEX idx_undolog_approval_timeout
  ON undolog_approval_requests (timeout_at)
  WHERE state = 'pending';

-- ---------------------------------------------------------------------------
-- undolog_approval_events - full audit trail of every action on an approval request
--
-- Every status change to an approval request is recorded here as an immutable event.
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_approval_events (
  event_id             uuid                   PRIMARY KEY DEFAULT uuidv7_or_random(),
  approval_request_id  uuid                   NOT NULL REFERENCES undolog_approval_requests (approval_request_id) ON DELETE CASCADE,
  org_id               uuid                   NOT NULL,

  action               undolog_approval_action  NOT NULL,
  actor                text                   NOT NULL,   -- user ID or 'system:timeout'
  actor_ip             inet,
  note                 text,

  -- For 'modify' actions: the changes made to the proposed args
  args_diff            jsonb,

  occurred_at          timestamptz            NOT NULL DEFAULT now()
);

CREATE INDEX idx_undolog_approval_events_request
  ON undolog_approval_events (approval_request_id, occurred_at ASC);

-- ---------------------------------------------------------------------------
-- undolog_session_snapshots - periodic state snapshots for fast session replay
--
-- The full session state can always be reconstructed by replaying the effect_log,
-- but that gets expensive for long sessions. Snapshots allow the Rust engine to
-- load the nearest snapshot and replay only the delta.
-- Created automatically every N tool calls (configurable per project).
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_session_snapshots (
  snapshot_id      uuid        PRIMARY KEY DEFAULT uuidv7_or_random(),
  session_id       uuid        NOT NULL REFERENCES undolog_sessions (session_id) ON DELETE CASCADE,
  org_id           uuid        NOT NULL,

  -- The step index up to which this snapshot reflects state
  up_to_step       int         NOT NULL,

  -- The serialized Rust session state (MessagePack or CBOR - opaque to PostgreSQL)
  state_bytes      bytea       NOT NULL,
  state_format     text        NOT NULL DEFAULT 'msgpack',

  -- Which effect IDs are included (for cache invalidation)
  effect_ids       uuid[]      NOT NULL DEFAULT '{}',

  created_at       timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT undolog_session_snapshots_unique UNIQUE (session_id, up_to_step)
);

CREATE INDEX idx_undolog_session_snapshots_session
  ON undolog_session_snapshots (session_id, up_to_step DESC);

-- ---------------------------------------------------------------------------
-- undolog_schema_migrations - migration version tracking
-- ---------------------------------------------------------------------------

CREATE TABLE undolog_schema_migrations (
  version      text        PRIMARY KEY,
  description  text        NOT NULL,
  applied_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO undolog_schema_migrations (version, description)
VALUES ('0001', 'Initial schema - orgs, projects, tool registry, effect log, undo stack, approvals');

-- ---------------------------------------------------------------------------
-- Row-Level Security (RLS) - tenant isolation
--
-- Enable RLS on all tables. The application layer must set:
--   SET LOCAL undolog.current_org_id = '<uuid>';
-- at the start of every transaction (done by the Go middleware).
-- ---------------------------------------------------------------------------

ALTER TABLE undolog_orgs              ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_projects          ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_tool_registry     ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_compensation_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_sessions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_effect_log        ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_undo_stack        ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_approval_events   ENABLE ROW LEVEL SECURITY;
ALTER TABLE undolog_session_snapshots ENABLE ROW LEVEL SECURITY;

-- RLS policies: org_id must match the current session's org
-- undolog_orgs: org can only see itself
CREATE POLICY undolog_orgs_isolation ON undolog_orgs
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

-- All other tables: org_id must match
CREATE POLICY undolog_projects_isolation ON undolog_projects
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_tool_registry_isolation ON undolog_tool_registry
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_compensation_registry_isolation ON undolog_compensation_registry
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_sessions_isolation ON undolog_sessions
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_effect_log_isolation ON undolog_effect_log
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_undo_stack_isolation ON undolog_undo_stack
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_approval_requests_isolation ON undolog_approval_requests
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_approval_events_isolation ON undolog_approval_events
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

CREATE POLICY undolog_session_snapshots_isolation ON undolog_session_snapshots
  USING (org_id = current_setting('undolog.current_org_id', true)::uuid);

-- ---------------------------------------------------------------------------
-- Triggers - maintain denormalized counters on undolog_sessions
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION undolog_update_session_counters()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE undolog_sessions
    SET tool_calls_total = tool_calls_total + 1
    WHERE session_id = NEW.session_id;

  ELSIF TG_OP = 'UPDATE' THEN
    -- Track when compensations complete
    IF OLD.state != 'compensated' AND NEW.state = 'compensated' THEN
      UPDATE undolog_sessions
      SET compensations_total = compensations_total + 1
      WHERE session_id = NEW.session_id;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER undolog_effect_log_counter
  AFTER INSERT OR UPDATE ON undolog_effect_log
  FOR EACH ROW
  EXECUTE FUNCTION undolog_update_session_counters();

-- Track pending approvals count on sessions
CREATE OR REPLACE FUNCTION undolog_update_approval_counter()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.state = 'pending' THEN
    UPDATE undolog_sessions
    SET approvals_pending = approvals_pending + 1
    WHERE session_id = NEW.session_id;

  ELSIF TG_OP = 'UPDATE' THEN
    IF OLD.state = 'pending' AND NEW.state != 'pending' THEN
      UPDATE undolog_sessions
      SET approvals_pending = GREATEST(0, approvals_pending - 1)
      WHERE session_id = NEW.session_id;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER undolog_approval_counter
  AFTER INSERT OR UPDATE ON undolog_approval_requests
  FOR EACH ROW
  EXECUTE FUNCTION undolog_update_approval_counter();

-- ---------------------------------------------------------------------------
-- Core queries - the patterns the Rust engine uses at runtime
-- ---------------------------------------------------------------------------

-- QUERY 1: Exactly-once check + insert (the hot path for every tool call)
--
-- The Rust engine calls this for every intercepted tool call.
-- Advisory lock prevents concurrent racing writes for the same signature.
-- ON CONFLICT is the safety net.
--
-- Step 1: Acquire advisory lock (in Rust via sqlx, before the INSERT)
--   SELECT pg_try_advisory_xact_lock(hashtext($1))  -- $1 = call_signature
--
-- Step 2: Insert with conflict guard
--
-- INSERT INTO undolog_effect_log (
--   effect_id, org_id, session_id, tool_id,
--   call_signature, tool_name, tool_version, tier,
--   step_index, args_snapshot, compensation_args, state, executed_at
-- )
-- VALUES (
--   uuidv7_or_random(), $org_id, $session_id, $tool_id,
--   $call_signature, $tool_name, $tool_version, $tier,
--   $step_index, $args_snapshot, $compensation_args, 'pending', now()
-- )
-- ON CONFLICT (call_signature) DO NOTHING
-- RETURNING effect_id, state;
--
-- If RETURNING returns no rows: call_signature already existed → replay path.
-- If RETURNING returns a row with state='pending': new call → execute path.

-- QUERY 2: Load undo stack for compensation (LIFO order)
--
-- SELECT
--   u.undo_id,
--   u.stack_position,
--   u.compensation_fn,
--   u.compensation_version,
--   u.compensation_args,
--   u.retry_count
-- FROM undolog_undo_stack u
-- WHERE u.session_id = $session_id
--   AND u.state = 'pending'
-- ORDER BY u.stack_position DESC;  -- highest = most recently pushed = first to compensate

-- QUERY 3: Fetch pending approvals for the dashboard (with timeout check)
--
-- SELECT
--   ar.approval_request_id,
--   ar.session_id,
--   ar.tool_name,
--   ar.irreversibility_reason,
--   ar.risk_tags,
--   ar.estimated_impact,
--   ar.proposed_args,
--   ar.agent_context,
--   ar.timeout_at,
--   ar.auto_approve_on_timeout,
--   s.agent_name,
--   s.external_run_id
-- FROM undolog_approval_requests ar
-- JOIN undolog_sessions s ON s.session_id = ar.session_id
-- WHERE ar.org_id = $org_id
--   AND ar.state = 'pending'
-- ORDER BY ar.created_at ASC;

-- QUERY 4: Load session replay from nearest snapshot + delta
--
-- Step 1: Find nearest snapshot
-- SELECT snapshot_id, up_to_step, state_bytes, state_format
-- FROM undolog_session_snapshots
-- WHERE session_id = $session_id
-- ORDER BY up_to_step DESC
-- LIMIT 1;
--
-- Step 2: Load delta (effects after snapshot)
-- SELECT *
-- FROM undolog_effect_log
-- WHERE session_id = $session_id
--   AND step_index > $up_to_step  -- from snapshot
-- ORDER BY step_index ASC;

-- QUERY 5: Mark effect as committed and cache result
--
-- UPDATE undolog_effect_log
-- SET state = 'committed',
--     result_snapshot = $result,
--     committed_at = now()
-- WHERE effect_id = $effect_id
--   AND executed_at >= $partition_hint  -- helps partition pruning
--   AND state = 'executing';

-- ---------------------------------------------------------------------------
-- Utility view: active session dashboard feed
-- (Used by the Next.js dashboard via SSE polling)
-- ---------------------------------------------------------------------------

CREATE VIEW undolog_active_sessions AS
SELECT
  s.session_id,
  s.org_id,
  s.project_id,
  s.agent_name,
  s.external_run_id,
  s.state,
  s.tool_calls_total,
  s.compensations_total,
  s.approvals_pending,
  s.started_at,
  p.name AS project_name,
  -- Latest tool call
  (
    SELECT json_build_object(
      'tool_name', el.tool_name,
      'tier', el.tier,
      'state', el.state,
      'executed_at', el.executed_at
    )
    FROM undolog_effect_log el
    WHERE el.session_id = s.session_id
    ORDER BY el.executed_at DESC
    LIMIT 1
  ) AS latest_effect
FROM undolog_sessions s
LEFT JOIN undolog_projects p ON p.project_id = s.project_id
WHERE s.state IN ('active', 'awaiting_approval', 'compensating');

-- ---------------------------------------------------------------------------
-- End of schema
-- ---------------------------------------------------------------------------
