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

### Changed

- Tool tier registry now reads `compensation_ref` and `irreversibility_reason`
  from DB to construct proper `Compensable`/`Irreversible` tiers
- Removed `ON CONFLICT DO NOTHING` from effect inserts (advisory lock +
  `find_by_signature` provide sufficient deduplication)

### Fixed

- `approve_effect()`, `reject_effect()`, `update_args_snapshot()` now return
  `InvalidStateTransition` on zero rows affected
- `reject()` reordered to load approval before resolving (prevents
  `ApprovalNotFound` on the callback)

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
