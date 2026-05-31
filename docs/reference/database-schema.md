---
title: "Database Schema Reference"
description: "**Engine:** PostgreSQL 16+ **Schema:** `public` **Migration:** `migrations/0001_initial.sql`"
section: "reference"
---
# Database Schema Reference

**Engine:** PostgreSQL 16+  
**Schema:** `public`  
**Migration:** `migrations/0001_initial.sql`

---

## Enum Types

### `undolog_tool_tier`

| Value | Description |
|-------|-------------|
| `safe` | Read-only or idempotent. Execute freely, no logging needed. |
| `compensable` | Has a defined compensation/undo. Log + register undo stack entry. |
| `irreversible` | Cannot be undone. Require human approval gate before execution. |

### `undolog_effect_state`

| Value | Description |
|-------|-------------|
| `pending` | Intercepted, awaiting execution (or awaiting human approval). |
| `executing` | Currently running. |
| `committed` | Completed successfully, result cached. |
| `compensating` | Compensation is running (undo in progress). |
| `compensated` | Compensation completed successfully. |
| `compensation_failed` | Compensation itself failed; requires manual intervention. |
| `approved` | Human approved an irreversible action; ready for proxy execution. |
| `rejected` | Human rejected an irreversible action; session halted. |
| `replayed` | Result was served from cache (exactly-once replay path). |

### `undolog_session_state`

| Value | Description |
|-------|-------------|
| `active` | Session is running. |
| `completed` | Session completed normally. |
| `failed` | Session failed (non-compensated). |
| `compensating` | Rolling back the undo stack. |
| `compensated` | All compensations completed. |
| `awaiting_approval` | Waiting for human approval of an irreversible action. |
| `halted` | Fatal: compensation failed, requires manual intervention. |

### `undolog_approval_state`

| Value | Description |
|-------|-------------|
| `pending` | Awaiting human decision. |
| `approved` | Human approved. |
| `rejected` | Human rejected. |
| `timed_out` | Approval window expired. |
| `auto_approved` | Approved by policy (e.g. low-risk auto-approve timeout). |

### `undolog_approval_action`

| Value | Description |
|-------|-------------|
| `approve` | Approve as-is. |
| `reject` | Reject; execution halted. |
| `modify` | Approver modified the arguments before approving. |
| `timeout` | Approval timed out without a decision. |

---

## `undolog_orgs`

Tenant root table.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `org_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique organisation identifier. |
| `name` | `text` | `NOT NULL` | Organisation name. |
| `slug` | `text` | `NOT NULL UNIQUE` | URL-safe identifier used in API paths. Format: `^[a-z0-9-]{2,64}$`. |
| `created_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Creation timestamp. |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Last update timestamp. |
| `default_approval_timeout_seconds` | `int` | `NOT NULL DEFAULT 3600` | Default approval timeout (1 hour). |
| `auto_approve_on_timeout` | `bool` | `NOT NULL DEFAULT false` | Auto-approve when approval times out. |

**Indexes:**

| Name | Columns | Type |
|------|---------|------|
| `idx_undolog_orgs_slug` | `slug` | B-tree |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_projects`

Grouping of agent deployments within an org.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `project_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique project identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `name` | `text` | `NOT NULL` | Project name. |
| `description` | `text` |, | Human-readable description. |
| `created_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Creation timestamp. |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Last update timestamp. |

**Constraints:**

| Name | Definition |
|------|-----------|
| `undolog_projects_org_name_unique` | `UNIQUE (org_id, name)` |

**Indexes:**

| Name | Columns |
|------|---------|
| `idx_undolog_projects_org` | `org_id` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_tool_registry`

Tool definitions with tier annotations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `tool_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique tool identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `project_id` | `uuid` | `FK → undolog_projects(project_id) ON DELETE SET NULL` | Optional project grouping. |
| `tool_name` | `text` | `NOT NULL` | Logical tool name (e.g. `"transfer_funds"`). |
| `tool_version` | `text` | `NOT NULL DEFAULT '1.0.0'` | Semantic version. |
| `tier` | `undolog_tool_tier` | `NOT NULL` | Tool tier classification. |
| `irreversibility_reason` | `text` |, | Required for `irreversible` tier. Human-readable explanation. |
| `compensation_ref` | `text` |, | Required for `compensable` tier. References `undolog_compensation_registry.fn_name`. |
| `tool_schema` | `jsonb` | `NOT NULL` | Full MCP tool JSON schema. |
| `risk_tags` | `text[]` | `NOT NULL DEFAULT '{}'` | Risk categorisation tags (e.g. `['financial']`). |
| `estimated_impact` | `text` |, | Human-readable impact description. |
| `registered_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Registration timestamp. |
| `updated_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Last update timestamp. |
| `is_active` | `bool` | `NOT NULL DEFAULT true` | Whether the tool is currently active. |

**Constraints:**

| Name | Definition |
|------|-----------|
| `undolog_tool_registry_unique` | `UNIQUE (org_id, tool_name, tool_version)` |
| `undolog_tool_compensable_has_ref` | `CHECK (tier != 'compensable' OR compensation_ref IS NOT NULL)` |
| `undolog_tool_irreversible_has_reason` | `CHECK (tier != 'irreversible' OR irreversibility_reason IS NOT NULL)` |

**Indexes:**

| Name | Columns |
|------|---------|
| `idx_undolog_tool_registry_org` | `org_id` |
| `idx_undolog_tool_registry_project` | `project_id` |
| `idx_undolog_tool_registry_name` | `tool_name` |
| `idx_undolog_tool_registry_tier` | `tier` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_compensation_registry`

Compensation function definitions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `compensation_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique compensation identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `fn_name` | `text` | `NOT NULL` | Function name matching `tool_registry.compensation_ref`. |
| `fn_version` | `text` | `NOT NULL DEFAULT '1.0.0'` | Semantic version. |
| `description` | `text` | `NOT NULL` | Human-readable description (e.g. "Cancels a payment"). |
| `fn_body` | `jsonb` | `NOT NULL` | Serialized compensation descriptor (endpoint, method, arg mapping, idempotency key). |
| `args_schema` | `jsonb` | `NOT NULL` | JSON Schema for compensation arguments. |
| `max_retries` | `int` | `NOT NULL DEFAULT 3` | Max retries before escalating to `compensation_failed`. |
| `retry_backoff_ms` | `int` | `NOT NULL DEFAULT 1000` | Base retry backoff in milliseconds. |
| `registered_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Registration timestamp. |
| `is_active` | `bool` | `NOT NULL DEFAULT true` | Whether compensation is active. |

**Constraints:**

| Name | Definition |
|------|-----------|
| `undolog_compensation_registry_unique` | `UNIQUE (org_id, fn_name, fn_version)` |

**Indexes:**

| Name | Columns |
|------|---------|
| `idx_undolog_compensation_org` | `org_id` |
| `idx_undolog_compensation_fn_name` | `fn_name` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_sessions`

One agent execution session.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `session_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique session identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `project_id` | `uuid` | `FK → undolog_projects(project_id) ON DELETE SET NULL` | Optional project grouping. |
| `external_run_id` | `text` |, | External correlation ID (LangGraph run ID, Temporal workflow ID). |
| `agent_name` | `text` |, | Human-readable agent name. |
| `state` | `undolog_session_state` | `NOT NULL DEFAULT 'active'` | Current lifecycle state. |
| `tool_calls_total` | `int` | `NOT NULL DEFAULT 0` | Counter maintained by trigger. |
| `compensations_total` | `int` | `NOT NULL DEFAULT 0` | Counter maintained by trigger. |
| `approvals_pending` | `int` | `NOT NULL DEFAULT 0` | Counter maintained by trigger. |
| `started_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Session start timestamp. |
| `completed_at` | `timestamptz` |, | Session completion timestamp. |
| `failed_at` | `timestamptz` |, | Session failure timestamp. |
| `failure_reason` | `text` |, | Human-readable failure reason. |
| `metadata` | `jsonb` | `NOT NULL DEFAULT '{}'` | Arbitrary agent metadata. |

**Indexes:**

| Name | Columns | Condition |
|------|---------|-----------|
| `idx_undolog_sessions_org` | `org_id` | |
| `idx_undolog_sessions_project` | `project_id` | |
| `idx_undolog_sessions_state` | `state` | `WHERE state != 'completed'` |
| `idx_undolog_sessions_external` | `external_run_id` | `WHERE external_run_id IS NOT NULL` |
| `idx_undolog_sessions_started` | `started_at DESC` | |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_effect_log` (PARTITIONED)

Append-only log of every tool call. This is the heart of UndoLog.

**Partition key:** `RANGE (executed_at)` monthly.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `effect_id` | `uuid` | `NOT NULL DEFAULT uuidv7_or_random()` | Unique effect identifier. |
| `org_id` | `uuid` | `NOT NULL` | Tenant organisation. |
| `session_id` | `uuid` | `NOT NULL` | Session identifier. |
| `tool_id` | `uuid` |, | Registered tool identifier (NULL if unknown). |
| `call_signature` | `char(64)` | `NOT NULL` | BLAKE3 deduplication key. UNIQUE across all partitions. |
| `tool_name` | `text` | `NOT NULL` | Tool name at call time (denormalised). |
| `tool_version` | `text` | `NOT NULL DEFAULT '1.0.0'` | Tool version at call time. |
| `tier` | `undolog_tool_tier` | `NOT NULL` | Tool tier at call time. |
| `step_index` | `int` | `NOT NULL` | Monotonically increasing per session. |
| `args_snapshot` | `jsonb` | `NOT NULL` | Input captured before execution (canonical JSON). |
| `result_snapshot` | `jsonb` |, | Output captured after execution (NULL until committed). |
| `state` | `undolog_effect_state` | `NOT NULL DEFAULT 'pending'` | Current lifecycle state. |
| `compensation_args` | `jsonb` |, | Compensation args captured before execution (NULL for safe/irreversible). |
| `executed_at` | `timestamptz` | `NOT NULL DEFAULT now()` | **Partition key.** Execution timestamp. |
| `committed_at` | `timestamptz` |, | Commit timestamp. |
| `compensated_at` | `timestamptz` |, | Compensation completion timestamp. |
| `replay_count` | `smallint` | `NOT NULL DEFAULT 0` | Number of times this effect was replayed. |
| `last_replayed_at` | `timestamptz` |, | Most recent replay timestamp. |
| `approval_request_id` | `uuid` |, | FK to `undolog_approval_requests` for irreversible actions. |

**Primary key:** `(effect_id, executed_at)`

### Indexes

| Name | Columns | Condition | Type |
|------|---------|-----------|------|
| `idx_undolog_effect_log_session` | `session_id, step_index` | | B-tree |
| `idx_undolog_effect_log_state` | `state, org_id` | `WHERE state IN ('pending', 'executing', 'compensating', 'compensation_failed')` | B-tree |
| `idx_undolog_effect_log_org_time` | `org_id, executed_at DESC` | | B-tree |

### Partitions (2025-11, 2027-04)

Monthly partitions spanning November 2025 through April 2027 (6 months back +
12 months forward from May 2026). Each partition carries a BRIN index on
`executed_at` (`pages_per_range = 32`) and a unique index on `call_signature`:

```sql
-- Example: partition DDL for a single month
CREATE TABLE undolog_effect_log_2026_06 PARTITION OF undolog_effect_log
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE INDEX idx_vel_2026_06_brin ON undolog_effect_log_2026_06
  USING BRIN (executed_at) WITH (pages_per_range = 32);
CREATE UNIQUE INDEX idx_vel_2026_06_sig ON undolog_effect_log_2026_06 (call_signature);
```

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

### Trigger: `undolog_effect_log_counter`

Fires `AFTER INSERT OR UPDATE`. Increments `undolog_sessions.tool_calls_total` on INSERT. Increments `compensations_total` when state transitions to `compensated`.

---

## `undolog_undo_stack`

Ordered stack of pending compensations per session.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `undo_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique undo entry identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `session_id` | `uuid` | `NOT NULL FK → undolog_sessions(session_id) ON DELETE CASCADE` | Session identifier. |
| `effect_id` | `uuid` | `NOT NULL` | References `undolog_effect_log.effect_id`. |
| `stack_position` | `int` | `NOT NULL` | LIFO ordering: higher = pushed later = compensated first. |
| `compensation_fn` | `text` | `NOT NULL` | Matches `undolog_compensation_registry.fn_name`. |
| `compensation_version` | `text` | `NOT NULL DEFAULT '1.0.0'` | Compensation function version. |
| `compensation_args` | `jsonb` | `NOT NULL` | Argument snapshot captured **before** action execution. |
| `state` | `text` | `NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','compensated','failed','skipped'))` | Compensation execution state. |
| `retry_count` | `smallint` | `NOT NULL DEFAULT 0` | Current retry attempt count. |
| `last_error` | `text` |, | Most recent error message. |
| `registered_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Must be **before** action executes. |
| `compensated_at` | `timestamptz` |, | Compensation completion timestamp. |

**Constraints:**

| Name | Definition |
|------|-----------|
| `undolog_undo_stack_session_position` | `UNIQUE (session_id, stack_position)` |

**Indexes:**

| Name | Columns | Condition |
|------|---------|-----------|
| `idx_undolog_undo_stack_session` | `session_id, stack_position DESC` | `WHERE state = 'pending'` |
| `idx_undolog_undo_stack_effect` | `effect_id` | |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_approval_requests`

Human-in-the-loop gate for irreversible actions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `approval_request_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique approval request identifier. |
| `org_id` | `uuid` | `NOT NULL FK → undolog_orgs(org_id) ON DELETE CASCADE` | Owning organisation. |
| `session_id` | `uuid` | `NOT NULL FK → undolog_sessions(session_id) ON DELETE CASCADE` | Suspended session. |
| `effect_id` | `uuid` | `NOT NULL` | The pending effect waiting for approval. |
| `tool_name` | `text` | `NOT NULL` | Tool requesting approval. |
| `tier` | `undolog_tool_tier` | `NOT NULL DEFAULT 'irreversible'` | Tool tier (always `irreversible`). |
| `irreversibility_reason` | `text` | `NOT NULL` | Human-readable explanation. |
| `risk_tags` | `text[]` | `NOT NULL DEFAULT '{}'` | Risk categorisation tags. |
| `estimated_impact` | `text` |, | Human-readable impact description. |
| `proposed_args` | `jsonb` | `NOT NULL` | The agent's proposed tool arguments. |
| `agent_context` | `jsonb` | `NOT NULL DEFAULT '{}'` | Session context for UI (last N tool calls + reasoning trace). |
| `state` | `undolog_approval_state` | `NOT NULL DEFAULT 'pending'` | Current approval state. |
| `timeout_at` | `timestamptz` | `NOT NULL` | Deadline for approval decision. |
| `auto_approve_on_timeout` | `bool` | `NOT NULL DEFAULT false` | Auto-execute on timeout. |
| `resolved_at` | `timestamptz` |, | Resolution timestamp. |
| `resolved_by` | `text` |, | User ID or `'system:timeout'`. |
| `approved_args` | `jsonb` |, | Final approved args (may differ from proposed). |
| `created_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Creation timestamp. |

**Indexes:**

| Name | Columns | Condition |
|------|---------|-----------|
| `idx_undolog_approval_org_state` | `org_id, state, created_at DESC` | `WHERE state = 'pending'` |
| `idx_undolog_approval_session` | `session_id` | |
| `idx_undolog_approval_timeout` | `timeout_at` | `WHERE state = 'pending'` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

### Trigger: `undolog_approval_counter`

Fires `AFTER INSERT OR UPDATE`. Increments `undolog_sessions.approvals_pending` on INSERT with `state = 'pending'`. Decrements when state leaves `pending`.

---

## `undolog_approval_events`

Full audit trail of every action on an approval request.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `event_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique event identifier. |
| `approval_request_id` | `uuid` | `NOT NULL FK → undolog_approval_requests(approval_request_id) ON DELETE CASCADE` | Parent approval request. |
| `org_id` | `uuid` | `NOT NULL` | Organisation identifier. |
| `action` | `undolog_approval_action` | `NOT NULL` | Action taken. |
| `actor` | `text` | `NOT NULL` | User ID or `'system:timeout'`. |
| `actor_ip` | `inet` |, | Actor IP address. |
| `note` | `text` |, | Human-readable note. |
| `args_diff` | `jsonb` |, | For `modify` actions: changes to proposed args. |
| `occurred_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Event timestamp. |

**Indexes:**

| Name | Columns |
|------|---------|
| `idx_undolog_approval_events_request` | `approval_request_id, occurred_at ASC` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_session_snapshots`

Periodic state snapshots for fast session replay.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `snapshot_id` | `uuid` | `PK DEFAULT uuidv7_or_random()` | Unique snapshot identifier. |
| `session_id` | `uuid` | `NOT NULL FK → undolog_sessions(session_id) ON DELETE CASCADE` | Session identifier. |
| `org_id` | `uuid` | `NOT NULL` | Organisation identifier. |
| `up_to_step` | `int` | `NOT NULL` | Step index up to which this snapshot reflects state. |
| `state_bytes` | `bytea` | `NOT NULL` | Serialized Rust session state (MessagePack or CBOR). |
| `state_format` | `text` | `NOT NULL DEFAULT 'msgpack'` | Serialization format identifier. |
| `effect_ids` | `uuid[]` | `NOT NULL DEFAULT '{}'` | Included effect IDs (for cache invalidation). |
| `created_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Creation timestamp. |

**Constraints:**

| Name | Definition |
|------|-----------|
| `undolog_session_snapshots_unique` | `UNIQUE (session_id, up_to_step)` |

**Indexes:**

| Name | Columns |
|------|---------|
| `idx_undolog_session_snapshots_session` | `session_id, up_to_step DESC` |

**RLS policy:** `org_id = current_setting('undolog.current_org_id')::uuid`

---

## `undolog_schema_migrations`

Migration version tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `version` | `text` | `PK` | Migration version identifier. |
| `description` | `text` | `NOT NULL` | Human-readable description. |
| `applied_at` | `timestamptz` | `NOT NULL DEFAULT now()` | Application timestamp. |

---

## Row-Level Security

RLS is enabled on all data tables. The application layer must set `undolog.current_org_id` at the start of every transaction:

```sql
SET LOCAL undolog.current_org_id = '<uuid>';
```

| Table | Policy name | Policy definition |
|-------|-------------|-------------------|
| `undolog_orgs` | `undolog_orgs_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_projects` | `undolog_projects_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_tool_registry` | `undolog_tool_registry_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_compensation_registry` | `undolog_compensation_registry_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_sessions` | `undolog_sessions_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_effect_log` | `undolog_effect_log_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_undo_stack` | `undolog_undo_stack_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_approval_requests` | `undolog_approval_requests_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_approval_events` | `undolog_approval_events_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |
| `undolog_session_snapshots` | `undolog_session_snapshots_isolation` | `USING (org_id = current_setting('undolog.current_org_id')::uuid)` |

---

## Utility View: `undolog_active_sessions`

```sql
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
  (SELECT json_build_object(
      'tool_name', el.tool_name,
      'tier', el.tier,
      'state', el.state,
      'executed_at', el.executed_at
   ) FROM undolog_effect_log el
   WHERE el.session_id = s.session_id
   ORDER BY el.executed_at DESC
   LIMIT 1) AS latest_effect
FROM undolog_sessions s
LEFT JOIN undolog_projects p ON p.project_id = s.project_id
WHERE s.state IN ('active', 'awaiting_approval', 'compensating');
```
