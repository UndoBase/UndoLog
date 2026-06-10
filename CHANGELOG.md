# Changelog

All notable changes to UndoLog are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Auto-execute approved irreversible tools: proxy runs the tool and commits
  the result inline during the approve call (no separate retry needed)
- Engine approves effect (`pending→approved`), resumes session, and returns
  execution data to the proxy via gRPC `ApproveResponse`
- Session auto-creation on first intercept (no manual pre-creation required)
- `approval_store.get()` for loading approval request data before resolution
- `effect_store.update_args_snapshot()` for modified-args audit trail
- `rows_affected()` guards on `approve_effect()`, `reject_effect()`,
  `update_args_snapshot()` for safe state transitions
- `approval_demo.py`: full approval lifecycle demo (SAFE → COMPENSABLE →
  IRREVERSIBLE → AwaitingApprovalError → auto-approve via API)
- `compensation_demo.py`: compensation lifecycle demo (pre-registered
  compensation, LIFO rollback, custom retry policies)
- `compensate_assign_engineer()` and `compensate_escalate()` handler functions
- `replay_demo.py`: exactly-once execution via BLAKE3 signature dedup
  (same session_id + step_index + tool_name + canonical args → Replay)
- `compensate_charge_payment()` handler function for payment reversal
- `infra/mock-tool-server/`: upstream MCP tool server for local demos
- `migrations/0003_seed_demo_data.sql`: demo org + tool registrations
  (charge_payment, send_email, create_ticket, escalate_case)
- docker-compose.yml: added `tool-server` service, fixed API key UUID format
- `agent_stateful.py`: stateful LangGraph with ``interrupt``-based approval
  branching (approve → continue, reject → halt), ``MemorySaver`` checkpointing

### Changed
- Tool tier registry now reads `compensation_ref` and `irreversibility_reason`
  from DB to construct proper `Compensable`/`Irreversible` tiers
- Removed `ON CONFLICT DO NOTHING` from effect inserts (advisory lock +
  `find_by_signature` provide sufficient deduplication)
- Approve and reject flows wrap all DB writes in a single PG transaction
  for cross-store atomicity (no partial state on failure)
- Approval handler applies `requestTimeout` context to prevent hanging HTTP
  connections during post-approval tool execution

### Fixed

- `approve_effect()`, `reject_effect()`, `update_args_snapshot()` now return
  `InvalidStateTransition` on zero rows affected
- `set_active()` now returns `InvalidStateTransition` when session is not in
  `awaiting_approval` state
- `reject()` reordered to load approval before resolving (prevents
  `ApprovalNotFound` on the callback)
- `reject()` now transitions session to `halted` state instead of leaving it
  stuck in `awaiting_approval`
- `approve()` now returns the correct `tool_version` from the effect record
  instead of an empty string

### Security

- Bump google.golang.org/grpc to v1.79.3 (CVE-2026-33186, CRITICAL)
- Force postcss to >=8.5.10 via npm overrides (CVE-2026-41305, MODERATE)
- Bump golang.org/x/net to v0.48.0 (CVE-2025-22872, CVE-2025-22870, MODERATE)
- Fix incomplete HTML sanitization in heading extraction (CodeQL HIGH)
- Add rehype-sanitize to docs markdown pipeline (defense-in-depth)
- Add permissions block to all CI jobs (CodeQL MEDIUM)
- Remove scripts/ from version control (local tooling only)

## [0.1.0] - 2025-11-01

### Added

- Initial release of UndoLog: effect-tracking and exactly-once rollback system
- Python SDK with `@undolog_tool` decorator for tier annotation
- Go MCP proxy for HTTP/gRPC translation
- Rust effect engine with PostgreSQL-backed state machine
- Saga orchestrator for multi-step compensation
- Three tier system: Safe, Compensable, Irreversible
- Human-in-the-loop approval for irreversible actions
- Deterministic call signature via BLAKE3 hashing
- Advisory locks for tool call deduplication
- Pre-registered compensation for crash safety
- Website and documentation at undobase.com
