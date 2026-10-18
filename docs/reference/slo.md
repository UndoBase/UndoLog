---
title: "Service Level Objectives Reference"
description: "SLO latency and throughput targets for intercept, commit, compensation, approval, and recovery operations, backed by the benchmark suite."
section: "reference"
---
# Service Level Objectives Reference

UndoLog defines Service Level Objectives (SLOs) across five core runtime
operations: intercept, commit, compensation, approval, and recovery replay.
Every target on this page is backed by a runnable benchmark in
`infra/benchmarks/` and enforced via continuous p95 regression checking in CI.
No target is published without a concrete measurement path.

---

## Target Summary

All latency targets specify p95 and p99 thresholds measured under steady-state
load against a reference PostgreSQL deployment.

| Operation | Target p95 | Target p99 | Benchmark Suite | Runnable Target | SLI Metric |
|---|---|---|---|---|---|
| Safe Intercept | < 1.0ms | < 2.5ms | `overhead-safe` | `make bench-overhead` | Proxy HTTP duration |
| Compensable Intercept | < 5.0ms | < 10.0ms | `overhead-compensable` | `make bench-overhead` | Engine gRPC intercept duration |
| Commit Execution | < 3.0ms | < 6.0ms | `dedup-cold` | `make bench-dedup` | Engine gRPC commit duration |
| Compensation Rollback | < 10.0ms / step | < 25.0ms / step | `bench_compensation_chain` | `make bench-compensation` | Rollback duration per step |
| Approval Lifecycle | < 15.0ms | < 30.0ms | `approval-latency` | `make bench-approval` | Proxy approval decision duration |
| Recovery Replay | < 2.0ms | < 5.0ms | `dedup-hot` | `make bench-dedup` | Replay response latency |

---

## Intercept Latency

### Definition

The intercept operation evaluates incoming tool calls before upstream
execution. It computes the canonical BLAKE3 call signature, verifies
tool tier classification in the tier registry, checks the session cache,
acquires an advisory lock on the signature, and inserts the initial effect
record into `undolog_effect_log`.

### Targets

- **Safe Tier:** p95 < 1.0ms, p99 < 2.5ms. Safe tools do not log state to
  PostgreSQL and bypass journal persistence after signature check.
- **Compensable Tier:** p95 < 5.0ms, p99 < 10.0ms. Includes PostgreSQL
  advisory lock acquisition and effect journal insert.
- **Irreversible Tier:** p95 < 5.0ms, p99 < 10.0ms. Includes approval record
  creation in `undolog_approval_requests` (excluding human wait time).

### Measurement Path

Measured by the overhead benchmark in `infra/benchmarks/scenarios.py`:

```bash
make bench-overhead
# or
python -m infra.benchmarks.run --benchmark overhead --warmup 5 --duration 15
```

The benchmark runs direct mock server calls side-by-side with Safe,
Compensable, and Irreversible intercepts, isolating UndoLog middleware
overhead.

### Service Level Indicator (SLI)

- Prometheus metric: `undolog_proxy_http_request_duration_seconds{route="/mcp/tool_call"}`
- Engine span: `intercept` duration in OpenTelemetry traces.

---

## Commit Latency

### Definition

The commit operation records the tool output and transitions the effect from
`Executing` to `Committed` state. For compensable tools, it writes the
compensation undo record so the saga orchestrator can roll back the effect if
a subsequent tool fails.

### Targets

- **Single Effect Commit:** p95 < 3.0ms, p99 < 6.0ms on PostgreSQL.
- **Advisory Lock Release:** Sub-millisecond handoff to waiting concurrent
  callers on the same session.

### Measurement Path

Measured by the compensable overhead scenario and dedup cold runs in
`infra/benchmarks/scenarios.py`:

```bash
make bench-dedup
# or
python -m infra.benchmarks.run --benchmark dedup --warmup 5 --duration 15
```

The benchmark measures end-to-end commit RPC duration from proxy dispatch
to database persistence.

### Service Level Indicator (SLI)

- Prometheus metric: `undolog_proxy_engine_rpc_duration_seconds{method="Commit"}`
- Engine span: `commit` duration in OpenTelemetry traces.

---

## Compensation Latency

### Definition

When a session encounters an unhandled error, the saga orchestrator pops
registered compensations from the session undo stack in reverse (LIFO) order
and executes each compensation function to undo external side effects.

### Targets

- **Per-Step Execution:** p95 < 10.0ms per compensation action.
- **Chain Depth 5 Rollback:** p95 < 50.0ms total rollback duration.
- **Chain Depth 20 Rollback:** p95 < 200.0ms total rollback duration.

### Measurement Path

Measured by the compensation chain benchmark in
`infra/benchmarks/scenarios.py`:

```bash
make bench-compensation
# or
python -m infra.benchmarks.run --benchmark compensation --warmup 5 --duration 20
```

The benchmark registers chains of varying lengths (1, 5, 10, 20 effects),
commits each, triggers a session failure, and measures end-to-end rollback
latency while verifying strict LIFO execution order.

### Service Level Indicator (SLI)

- Benchmark output: `latency_us.p95` for `compensation-chain-N`.
- Dead-letter queue counter: `undolog_dead_letters` table zero accumulation
  under normal rollback conditions.

---

## Approval Workflow Latency

### Definition

Irreversible tools pause execution until an operator approves or rejects the
action. The approval workflow latency measures the system processing time
required to transition the approval request, update effect state, and resume
the waiting agent once an approval decision is received.

### Targets

- **Intercept to Approval Request:** p95 < 5.0ms. Time from tool call arrival
  to approval creation in `undolog_approval_requests` and SSE broadcast.
- **Decision Resolution:** p95 < 10.0ms. Time to process `POST /approvals/{id}/approve`
  or reject, transition state, and emit resolution events.
- **Total System Processing:** p95 < 15.0ms (excluding human operator wait time).

### Measurement Path

Measured by the approval latency benchmark in `infra/benchmarks/scenarios.py`:

```bash
make bench-approval
# or
python -m infra.benchmarks.run --benchmark approval --warmup 5 --duration 20
```

The scenario initiates irreversible intercepts, captures the approval ID,
submits approval decisions via the proxy REST API, and records sub-stage
timings (`stage_intercept_us`, `stage_approve_us`, `stage_total_us`).

### Service Level Indicator (SLI)

- Prometheus metric: `undolog_proxy_approval_decision_duration_seconds`
- Metric: `undolog_proxy_approval_decisions_total{action="approve",result="applied"}`

---

## Recovery and Replay Latency

### Definition

When an agent retries an operation or submits a duplicate tool call with an
identical BLAKE3 signature, UndoLog bypasses upstream tool execution and
returns the cached result from the session cache or effect store.

### Targets

- **Hot Replay (Cache Hit):** p95 < 2.0ms, p99 < 5.0ms. Answered from the
  in-memory session cache without database round-trip.
- **Cold Replay (Database Read):** p95 < 5.0ms, p99 < 10.0ms. Answered from
  `undolog_effect_log` on session cache miss.
- **Contention Replay:** Zero duplicate executions when 10 concurrent callers
  submit the same call signature simultaneously.

### Measurement Path

Measured by the deduplication benchmark in `infra/benchmarks/scenarios.py`:

```bash
make bench-dedup
# or
python -m infra.benchmarks.run --benchmark dedup --warmup 5 --duration 15
```

The benchmark evaluates cold execution (`dedup-cold`), repeated replay
(`dedup-hot`), and concurrent execution races (`dedup-contention`), verifying
exactly-once execution semantics in the database store.

### Service Level Indicator (SLI)

- Benchmark output: `latency_us.p95` for `dedup-hot` versus `dedup-cold`.
- OpenTelemetry span attribute: `cache_hit = true` on `intercept` spans.

---

## Continuous Regression Enforcement

### CI Regression Gate

Every benchmark run produces a JSON artifact containing raw samples and
calculated percentiles (`bench-results/<timestamp>/results.json`). CI enforces
these SLOs by comparing current pull request results against the recorded
baseline using `compare_regression.py`:

```bash
python infra/benchmarks/compare_regression.py \
  --current bench-results/current/results.json \
  --baseline bench-results/baseline/results.json \
  --threshold 20
```

### Enforcement Rules

1. **Threshold Budget:** A pull request is rejected if any benchmark's p95
   latency degrades by more than 20% compared to baseline.
2. **Missing Baseline:** If no baseline exists, the run records current
   results as the new baseline and passes with an informational log.
3. **Automated Verification:** The regression checker exits with code 1 upon
   detecting an unexcused regression, blocking the pull request merge.

---

## Baseline Reference Environment

Benchmark targets reflect execution within the following reference environment:

- **Host:** 4 vCPU, 8 GB RAM, NVMe storage.
- **Operating System:** Linux x86_64 (kernel 6.x).
- **Topology:** Split container deployment via Docker Compose.
- **PostgreSQL:** PostgreSQL 16 with PgBouncer connection pooling (`default_pool_size = 25`).
- **Network:** Bridge Docker network between proxy, engine, and database containers.
