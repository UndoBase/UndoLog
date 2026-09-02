# Changelog

All notable changes to UndoLog are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Python SDK: ``__version__`` exposed via ``importlib.metadata``; async context
  manager on ``UndoLogClient``; ``py.typed`` PEP 561 marker.
- Python SDK: ``approve`` and ``reject`` methods on ``UndoLogClient`` for the
  approval lifecycle, and ``UndoLogSession`` exported from the top-level package.
- Engine: approval timeout processing with configurable interval, timeout
  duration, and auto-approve policy with audit events.
- Go proxy: HTTP hardening: configurable body/header caps (413 on oversized
  bodies), read-header and idle timeouts, fail-fast config on missing API keys
  or invalid upstream URLs, and constant-time API-key auth via SHA-256 digests.
- Go proxy: durable, ordered approval workflow backed by the engine database.
  Pending approvals are reconciled on startup and periodically, decisions use
  atomic compare-and-swap, the acting user is recorded in the audit trail,
  a failed post-approval execution calls ``fail``, and lists are newest-first
  with a ``?limit=`` bound (default 100, max 500).
- Go proxy: ``Commit`` and ``Fail`` engine RPC calls retry transient transport
  failures with bounded backoff (``Unavailable``, ``Aborted``,
  ``DeadlineExceeded``, ``Unknown``), so a momentary engine outage no longer
  502s an effect the engine records once the connection recovers. Intercept,
  Approve, and Reject are not retried. Attempts and base backoff are
  configurable (``UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS``,
  ``UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS``).
- Go proxy: the tool executor honors ``UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS``, and
  a ``4xx`` or ``5xx`` upstream response carrying a ``ToolResult`` body returns
  ``success: false`` to the caller instead of a 502 ``tool_error``.
- Go proxy: a ``/metrics`` endpoint (Prometheus text format) exposes HTTP,
  engine RPC, SSE, approval, and executor metrics, and ``/health`` stops
  echoing configuration values.
- Go proxy: request IDs are forwarded to the engine as ``x-request-id`` gRPC
  metadata for cross-service log correlation.
- Go proxy: the engine gRPC connection is created lazily on the first RPC and
  reconnects on demand, so the proxy no longer exits when the engine is down at
  startup and recovers from engine restarts without a proxy restart. The compose
  proxy also gains ``restart: unless-stopped``.
- Go proxy: ``GET /events`` now streams real SSE frames to the dashboard. The
  middleware chain preserves ``http.Flusher`` (the endpoint previously returned
  ``streaming unsupported``), the stream write deadline is cleared so long-lived
  connections survive ``WriteTimeout``, response headers are flushed as soon as
  the stream opens, a failed stream write ends the connection instead of leaving
  it idle forever, the organisation id must come from the authenticated request
  (the ``?org_id=`` query fallback is removed), and subscriber channels are
  closed before graceful shutdown.
- Go proxy: ``writeJSON`` failures log through the injected logger and
  ``LogLevel`` accepts ``warn``/``error``; the stale proxy ``Dockerfile`` and
  dead code (``effect_executed`` event, ``store.NewID``, advisory-lock helper)
  are removed.

### Changed

- **Python SDK (breaking):** SAFE-tier tools no longer consume a step index,
  aligning Python behaviour with TypeScript.
- Python SDK: license metadata corrected from MIT to Apache-2.0 (matches repo
  root ``LICENSE`` and README).
- Python SDK: ``typing.Self`` import fixed for Python 3.10 via
  ``typing_extensions`` conditional dependency.
- Python SDK: ``commit``/``fail`` docstrings corrected (no-ops through proxy);
  ``InterceptResponse.effect_id`` docstring corrected (not present for
  AwaitingApproval); unknown proxy status raises ``ValueError``; ``fail``
  error chaining preserves the original tool exception.
- Documentation: sync proxy API, integration, and cross-reference docs to
  reflect shipped approval flow, SSE events, and advisory locking ownership.
- TypeScript SDK: publish via npm Trusted Publishing (OIDC) with no
  long-lived npm token; redundant publish build step removed.
- CI workflows: Node 20 bumped to 24 (Node 20 is deprecated on GitHub
  runners).
- TypeScript SDK: ``repository.url`` prefixed with ``git+`` (``npm pkg fix``)
  so ``npm publish`` emits no auto-correction warning.
- ``sdks/undolog-ts/scripts/release.sh``: header now notes the script creates
  the tag and prints the push command rather than pushing itself.
- Go proxy: ``golangci-lint`` (repo config) enforced and ``go test -race``
  enabled in CI.

## [0.2.0] - 2026-08-01

### Added

- TypeScript SDK at ``sdks/undolog-ts/`` with full parity to the Python SDK:
  ``ToolTier`` enum, typed error hierarchy, BLAKE3 ``callSignature()``,
  ``UndoLogSession`` with ``AsyncLocalStorage`` context, ``UndoLogClient``
  (intercept/commit/fail/approve/reject), and ``wrapTool()``. Includes
  framework adapters (Vercel AI, LangChain, OpenAI Agents, Mastra, MCP server),
  a mock-server testing package, and 453 unit tests.
- TypeScript SDK renamed ``@undobase/undolog-sdk`` to ``@undolog/sdk``
  (requires ``@undolog`` scope ownership on npm); license corrected to
  Apache-2.0 (was MIT).
- TypeScript SDK helpers: ``claimStepIndex()``, ``getEffect()``,
  ``getSession()``, and the ``MissingSessionError`` class.
- Live-stack tooling: shared ``start-stack`` action, ``e2e.yml`` workflow,
  TypeScript ``test:integration`` script, and ``test_live_stack.py`` covering
  approval lifecycle, concurrent execution, LIFO compensation, and BLAKE3
  replay idempotency.
- Benchmark harness at ``infra/benchmarks/``: eight benchmarks (overhead,
  throughput, dedup, compensation chains, multi-tenant noise immunity, SSE
  delivery, approval workflow, longevity), a p95 regression checker
  (``compare_regression.py``), and nightly plus PR quick-check workflows.
- Python SDK: strict mypy typing, stateful mock-tool-server with
  idempotency-key dedup, cross-language BLAKE3 signature parity test, 19
  state-machine property tests, and network-error simulation tests.
- Multi-tenant demo (``multi_tenant_demo.py``), live SSE dashboard consumer
  (``sse_dashboard.py``), and a second-org seed migration with ``dev-key-2``.
- Approval workflow: session auto-creation, engine resume via gRPC
  ``ApproveResponse``, auto-execution of approved irreversible tools, and
  ``rows_affected()`` guards on every state transition.
- Example agents for LangChain, Vanilla, CrewAI, Semantic Kernel, and
  LlamaIndex sharing the ``example_tools`` registry, plus a stateful LangGraph
  agent with interrupt-based approval branching and approval, compensation,
  and replay demos.

### Changed

- Cross-language canonical JSON now follows ECMAScript ``JSON.stringify``
  number rules (RFC 8785) in the Rust, Python, and TypeScript SDKs and the Go
  proxy: ``-0.0`` becomes ``0``, fixed notation in ``[1e-6, 1e21)``, no leading
  zeros in exponents, non-finite values rejected. Byte-identical output verified
  across a 5023-double corpus, and the rules are documented in
  ``docs/reference/call-signature.md``.
- TypeScript SDK: mock server emulates the proxy MCP wire protocol
  (``POST /mcp/tool_call``) and ``canonicalJson`` serialises ``bigint``
  exactly.
- Proxy and engine state safety: approve/reject flows wrapped in one Postgres
  transaction; ``commit_effect()``/``fail_effect()`` use a single CTE query
  (closes a TOCTOU race); ``ON CONFLICT DO NOTHING`` dropped from effect
  inserts (advisory locks deduplicate); tier registry reads
  ``compensation_ref``/``irreversibility_reason`` from the DB; the approval
  handler applies ``requestTimeout``.
- Engine startup: waits for the tool-registry schema and populates the tier
  registry synchronously, with a retried initial refresh, so tools no longer
  silently default to SAFE tier during the first minute after boot.
- Example agents moved to per-framework directories; shared tools moved to
  ``example_tools``; agent functions wrapped with ``StructuredTool`` for
  session injection.
- ``scripts/`` removed from version control (local tooling only).

### Fixed

- TypeScript SDK: tests are type-checked in CI; ``sessionId`` validated on
  ``intercept()``; ``wrapTool`` compensates when ``commit()`` fails and logs
  when ``fail()`` fails; mutating requests no longer retry on network errors
  (preserves exactly-once); HTTP 403 maps to ``"forbidden"``; error messages
  sanitise credentials from URLs; ``UndoLogError`` works cross-realm; auth
  headers always win in merge order; ``buildUrl`` normalises a missing leading
  ``/``; all API paths aligned with the proxy (intercept/commit/fail/approve/
  reject) so live-stack tests no longer 404.
- Rust engine: ``resolve_tier`` falls back to name-only lookup when
  ``tool_version`` is omitted; mock tool-server auto-creates escalated tickets.
- CI: removed ``|| true`` that was hiding example-test failures; added
  ``npm ci``, ``protobuf-compiler``, and ``ruff`` to the Python SDK dev
  dependencies.

### Security

- Dependency upgrades for disclosed vulnerabilities: grpc (CVE-2026-33186,
  xDS RBAC bypass, HTTP/2 Rapid Reset DoS), ``golang.org/x/net``
  (CVE-2025-22872, CVE-2025-22870), postcss (path traversal), js-yaml (DoS),
  next (SSRF/DoS), sharp (libvips CVEs), ``@hono/node-server`` (WebSocket DoS),
  esbuild, and ``@modelcontextprotocol/sdk`` (6 CVEs).
- Docs pipeline hardened: HTML sanitisation in heading extraction fixed and
  ``rehype-sanitize`` added; all CI jobs run with explicit least-privilege
  ``permissions``.

## [0.1.0] - 2026-05-24

### Added

- Initial release of UndoLog: effect-tracking and exactly-once rollback system
- Python SDK with ``@undolog_tool`` decorator for tier annotation
- Go MCP proxy for HTTP/gRPC translation
- Rust effect engine with PostgreSQL-backed state machine
- Saga orchestrator for multi-step compensation
- Three tier system: Safe, Compensable, Irreversible
- Human-in-the-loop approval for irreversible actions
- Deterministic call signature via BLAKE3 hashing
- Advisory locks for tool call deduplication
- Pre-registered compensation for crash safety
- Website and documentation at https://undolog.undobase.com
