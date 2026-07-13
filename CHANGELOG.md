# Changelog

All notable changes to UndoLog are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- ``build_engine()`` in ``crates/undolog-engine/src/startup.rs``: wait
  for ``undolog_tool_registry`` table before proceeding (``wait_for_schema``
  with 30s retry budget), then populate the ``TierRegistry`` synchronously
  before spawning the background refresh loop.  Previously the registry was
  empty for the first 60 seconds after startup, causing every tool to
  silently default to SAFE tier (no effects logged, no replay, no approval
  gating for IRREVERSIBLE tools).
- ``commit_effect()`` and ``fail_effect()`` in ``crates/undolog-store``:
  replaced two-query (UPDATE + SELECT) pattern with a single CTE query
  that returns both affected-row count and existence flag in one
  round-trip.  Added ``warn!()`` log on the SAFE-tier skip path for
  observability.  Eliminates the TOCTOU race window between the UPDATE
  and existence check.  Resolved a ``502 commit_failed`` error for
  unregistered tools sent through the proxy.

### Added

- ``.github/actions/start-stack/action.yml``: shared composite action for
  Docker Compose stack startup, HTTP health checks, gRPC functional probe,
  and configurable API key input.
- ``.github/workflows/e2e.yml``: end-to-end integration workflow triggered
  on push to main that starts the full UndoLog stack and runs
  ``test_live_stack.py``.
- Benchmark harness at ``infra/benchmarks/``: async timing recorder
  with warmup, steady-state detection (split-half mean drift check),
  percentile statistics (p50/p95/p99/mean/stddev/min/max), TPS
  reporting, and resource metric collection (CPU%, RSS, goroutines,
  DB connections, open FDs, engine RSS).
- Benchmark 1 (overhead latency): direct call vs SAFE vs COMPENSABLE
  vs IRREVERSIBLE (full approval round-trip) tier latency with
  delta-from-baseline table.
- Benchmark 2 (throughput): completed tool calls per second at N
  concurrent sessions (1, 5, 10, 25, 50) with per-level TPS and
  resource correlation.
- Benchmark 3 (dedup): cold (first execution) vs hot (replay) latency
  comparison with 10-worker contention test, exactly-once DB
  verification, and saturation-level testing.
- Benchmark 4 (compensation chain): rollback time for undo stacks
  of depth 1, 5, 10, 20 with per-effect compensation timing and
  LIFO ordering verification.
- Benchmark 5 (multi-tenant noise immunity): org-beta latency at
  noise levels 0, 10, 25, 50 concurrent org-alpha sessions with flat-
  line isolation analysis.
- Benchmark 6 (SSE delivery latency): emit-to-callback delta varying
  subscriber counts (1, 5, 10) and event rates (10/s, 100/s, 500/s)
  with drop-rate measurement.
- Benchmark 7 (approval workflow latency): full intercept-to-commit
  round-trip with stage breakdown (intercept, approve, total) and
  5-worker contention test (one SUCCESS, remainder CONFLICT).
- Benchmark 8 (longevity): 30-minute sustained load with resource
  drift detection (CPU/RSS/goroutine trend across first/second half).
- Makefile targets: ``bench``, ``bench-quick``, ``bench-overhead``,
  ``bench-throughput``, ``bench-dedup``, ``bench-compensation``,
  ``bench-multitenant``, ``bench-sse``, ``bench-approval``,
  ``bench-longevity``.
- ``.github/workflows/benchmarks.yml``: nightly full benchmark run
  with JSON artifact archive and regression comparison.
- ``.github/workflows/benchmarks-pr.yml``: PR quick-check workflow
  running overhead benchmark 1 at concurrency=1 with 100-sample
  minimum and plausible-latency verification.
- ``infra/benchmarks/compare_regression.py``: p95 regression checker
  with configurable threshold (default 20%), baseline auto-detection,
  and exit-code gating for release CI.

- Multi-tenant demo with concurrent agents in two isolated orgs
  (``multi_tenant_demo.py``): org-alpha runs SAFE, COMPENSABLE, and
  IRREVERSIBLE tools with auto-approve; org-beta runs SAFE, COMPENSABLE,
  and a forced failure triggering compensation rollback.  SSE dashboard
  consumer (``sse_dashboard.py``) subscribes to both org streams and
  renders lifecycle events in real time.
- Migration ``0004_seed_demo_org_two.sql``: seeds a second demo
  organisation (org-beta) with matching tool registrations.
- ``docker-compose.yml``: added second API key (``dev-key-2``) mapped to
  the second org UUID.
- ``Makefile``: ``demo-multi-tenant`` target.
- ``.env.example``: ``UNDOLOG_API_KEY_2`` variable.

- GitHub Actions CI workflow: runs ``make check`` on every push and
  pull request to main, plus example unit tests and mock-tool-server
  HTTP contract tests (``.github/workflows/ci.yml``)
- mypy static type checking configuration with strict mode and
  ``make typecheck`` target (``sdks/undolog-py/mypy.ini``,
  ``sdks/undolog-py/pyproject.toml``)
- 27 mock-tool-server HTTP contract tests: handler dispatch,
  idempotency-key dedup, health endpoint, error status codes
  (``infra/mock-tool-server/tests/test_server.py``)
- Makefile targets: ``typecheck``, ``test-examples``, ``test-mock-server``,
  ``test-all``
- Idempotency-Key header dedup in mock-tool-server: repeated key returns
  cached response without re-executing the handler
  (``infra/mock-tool-server/server.py``)
- Cross-language BLAKE3 signature parity test: hardcoded digest
  ``8f20ad...d32546`` that must match the Rust engine's canonical-JSON
  pipeline (``test_signature.py``)
- 19 property-style state machine tests: effect transitions, session step
  invariants, and compensation descriptor invariants
  (``test_state_machine.py``)
- Tool bodies now make real HTTP calls to a stateful mock-tool-server
  (replaces hardcoded mock returns in ``tools.py``, ``compensation_demo.py``,
  ``replay_demo.py``)
- Stateful mock-tool-server with CRUD handlers, in-memory store, and
  seeded customer/plan/engineer data (``infra/mock-tool-server/server.py``)
- Integration tests for live UndoLog stack: approval lifecycle (approve,
  reject, double-approve/reject), concurrent execution, LIFO compensation
  chain, and BLAKE3 replay idempotency (``test_live_stack.py``)
- Network error simulation tests: ``ConnectError``, ``ReadTimeout``,
  HTTP 4xx/5xx, and commit/fail network errors propagate correctly
  through the ``@undolog_tool`` decorator (``test_decorators.py``)
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
- Interrupt lifecycle tests for ``agent_stateful.py``: AAE catch, approve
  resume, reject halt, checkpointer verification
- ``examples/example_tools/`` package: shared tool registry and compensation
  handlers consumed by all framework examples
- ``examples/vanilla-support-agent/``: standalone vanilla asyncio agent with
  OpenAI client, ``pyproject.toml``, ``Makefile``, and 11 unit tests
- ``examples/crewai-support-agent/``: standalone CrewAI agent with
  ``StructuredTool`` wrappers, ``pyproject.toml``, ``Makefile``, and 3 tests
- ``examples/semantic-kernel-support-agent/``: standalone Semantic Kernel
  agent with ``KernelFunctionFromMethod`` wrappers, ``pyproject.toml``,
  ``Makefile``, and 2 tests
- ``examples/llama-index-support-agent/``: standalone LlamaIndex agent with
  ``FunctionTool`` wrappers, ``pyproject.toml``, ``Makefile``, and 2 tests
- ``test_crewai_agent.py``, ``test_semantic_kernel_agent.py``,
  ``test_llama_index_agent.py``: per-framework import and missing-API-key
  tests split from ``test_cross_framework.py``

### Changed

- ``.github/workflows/benchmarks.yml``, ``.github/workflows/benchmarks-pr.yml``:
  stack startup logic replaced with ``uses: ./.github/actions/start-stack``
  shared action (removed 22-line inline shell block).
- ``.opencode/QUALITY_PRINCIPLES.md``: Principle 2 (print) broadened
  to allow structured CLI output from named ``print_*`` / ``output_*``
  helpers; Principle 11 (test coverage) exempts benchmark tools whose
  validation is execution-based regression comparison.
- Tool tier registry now reads `compensation_ref` and `irreversibility_reason`
  from DB to construct proper `Compensable`/`Irreversible` tiers
- Removed `ON CONFLICT DO NOTHING` from effect inserts (advisory lock +
  `find_by_signature` provide sufficient deduplication)
- Approve and reject flows wrap all DB writes in a single PG transaction
  for cross-store atomicity (no partial state on failure)
- Approval handler applies `requestTimeout` context to prevent hanging HTTP
  connections during post-approval tool execution
- ``vanilla_agent.py``, ``crewai_agent.py``, ``semantic_kernel_agent.py``,
  ``llama_index_agent.py`` moved from ``langchain-support-agent/`` to their
  own per-framework directories
- ``tools.py`` and ``compensations.py`` removed from
  ``langchain-support-agent/``; all agents import from ``example_tools``
- Upgraded type annotations across SDK client and decorator modules to
  pass strict mypy validation (``client.py``, ``decorators.py``,
  ``test_decorators.py``)

### Fixed

- ``.github/workflows/ci.yml``: removed ``|| true`` that was silently
  swallowing example test suite failures; any test breakage now fails CI.
- ``.github/actions/start-stack/action.yml``: removed dead ``psql`` step
  that used an HTTP URL (``UNDOLOG_PROXY_URL``) as a PostgreSQL connection
  string, always silently failing. Migrations are handled by Postgres
  ``docker-entrypoint-initdb.d``.
- ``api_key`` in start-stack action: changed from hardcoded ``dev-key`` to
  an input parameter with default ``dev-key``, allowing callers to override.
- Example agent projects now use standard ``setuptools.build_meta`` instead
  of experimental ``setuptools.backends._legacy:_Backend`` (unavailable in
  CI runner's setuptools version); added ``[tool.setuptools.packages.find]``
  to ``langchain-support-agent`` for flat-layout discovery
- ``ruff`` added to Python SDK dev dependencies (was missing, causing
  ``make: ruff: No such file or directory`` in CI)
- ``npm ci`` step added to CI workflow before ``make check`` (was missing,
  causing ``npm run build`` to fail with no ``node_modules``)
- ``protobuf-compiler`` install step added to CI workflow (was missing,
  causing ``undolog-engine`` build to fail without ``protoc``)
- CHANGELOG validation, commitlint, broken link check, and rustdoc
  completeness steps added to the consolidated ``check`` job (were
  separate jobs in the old workflow, dropped during consolidation)
- `agent.py` now wraps raw ``@undolog_tool`` functions with ``StructuredTool``
  + context var for session injection (was crashing with missing ``_session``
  kwarg when called by ``create_react_agent``)
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
- Bump golang.org/x/net to v0.56.0 (CVE-2025-22872, CVE-2025-22870,
  CVE-2026-XXXXX, MODERATE)
- Bump js-yaml to v3.15.0 (quadratic-complexity DoS in merge key
  handling, MODERATE)
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
