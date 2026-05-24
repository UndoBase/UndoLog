---
title: "Exactly-Once Semantics"
description: "## The Problem"
section: "explanation"
---
# Exactly-Once Semantics

## The Problem

A financial agent initiates a `transfer_funds` call. The upstream banking
API returns a timeout after 30 seconds. The framework's built-in retry
logic fires a second call with the same parameters. Both calls reach the
banking API. The account is debited twice.

This scenario is not hypothetical. Every major agent framework implements
retry at some level:
- **LangGraph** retries on node failure with configurable `retry_policy`
- **CrewAI** retries tool calls on `ToolUsageError` with exponential backoff
- **OpenAI Agents SDK** retries on network-level failures at the HTTP
  transport layer
- **Kubernetes** restarts crashed pods, which replay the workflow from the
  last saved state

Without exactly-once semantics, the same logical tool call produces
multiple physical executions. The system is at the mercy of the
upstream service's idempotency: and most APIs are not idempotent.

## The Naive Approach

The standard solution is an idempotency key attached to each API request.
Most agent frameworks generate a random UUID per call and send it as an
`Idempotency-Key` header. The upstream service deduplicates based on the
key.

This fails for two reasons. First, the key is generated at the framework
level, not derived from the call's content. A retry that changes a
parameter (e.g., a timestamp auto-injected by middleware) produces the
same key as the original: but the service receives different payloads.
The idempotency contract is violated. Second, the key is meaningless to
other components in the system. The effect log, the undo stack, and the
approval workflow cannot correlate retries to original calls because they
see different identifiers.

A different approach: database-level unique constraints on a hash of the
call: solves the correlation problem but introduces a race: two
concurrent proxy instances can both attempt to insert the same call
signature simultaneously. One insert fails with a unique violation, and
the caller must handle the error and retry. Under high concurrency, this
creates write-contention cascades.

## The UndoLog Approach: Two-Layer Idempotency

UndoLog provides exactly-once semantics through a deterministic call
signature enforced at two independent layers: an advisory lock for
concurrency control and a database unique constraint as the safety net.

### Layer 1: BLAKE3 Call Signature

Every intercepted tool call produces a deterministic 64-character hex
signature:

```
signature = BLAKE3(
    session_id (16 bytes, fixed) ||
    step_index (4 bytes, little-endian) ||
    tool_name_len (4 bytes, LE) || tool_name (N bytes, UTF-8) ||
    canonical_args_len (4 bytes, LE) || canonical_args (M bytes, UTF-8)
)
```

The inputs are carefully chosen. `session_id` scopes the signature to one
workflow run. `step_index` identifies the logical position within that
run. `tool_name` identifies the operation. `canonical_args` captures the
exact parameters.

The canonical JSON encoding is critical. `serde_json` (Rust) and
`json.dumps` (Python) serialize object keys in insertion order, which
varies across languages and runtime versions. `{"a":1,"b":2}` and
`{"b":2,"a":1}` are semantically identical but produce different byte
sequences. The `canonical_json` function recursively sorts all object
keys by name before serializing, guaranteeing that two structurally
equivalent JSON documents produce identical output.

The length-prefixed encoding prevents boundary attacks: cases where `("a", "bc")` and `("ab", "c")` could produce the same concatenated byte
sequence without explicit delimiters.

Cross-language determinism is a hard requirement. The Rust implementation
in `undolog-types/src/effect.rs:27-59` and the Python implementation in
`undolog_sdk/signature.py:45-109` must produce byte-for-byte identical
output for the same inputs. Both use the `blake3` crate/library with the
same input layout. The Go proxy computes its own canonical JSON for
signing (`internal/proxy/signature.go`) but delegates the final
deduplication to the Rust engine via gRPC.

### Layer 2: Advisory Lock (FNV-1a + pg_try_advisory_xact_lock)

Before inserting into the effect log, the engine acquires a PostgreSQL
advisory lock keyed on the call signature:

```
lock_key = FNV-1a-64(call_signature)  -- 64-bit non-cryptographic hash
acquired = pg_try_advisory_xact_lock(lock_key)
```

The advisory lock is transaction-scoped: it is automatically released
when the surrounding transaction commits or rolls back. This prevents two
concurrent proxy instances from racing to insert the same signature. The
lock key derivation uses FNV-1a, not BLAKE3, because advisory locks
require a 64-bit integer key and FNV-1a is fast (no cryptographic
overhead for a non-security use case). The Rust and Go implementations
produce identical keys:

| Language | Implementation |
|---|---|
| Rust | `crates/undolog-store/src/effect_store.rs:585-602` |
| Go | `services/undolog-proxy/internal/lock/advisory.go:10-14` |

If the lock cannot be acquired after `max_attempts` (default: 3) with
`retry_ms` delay (default: 100ms), the engine returns
`AdvisoryLockTimeout`. This is a safety valve: under normal operation,
the lock is acquired on the first attempt.

### Layer 3: UNIQUE Constraint (Safety Net)

After acquiring the lock, the engine executes:

```sql
INSERT INTO undolog_effect_log (...)
VALUES (...)
ON CONFLICT (call_signature) DO NOTHING
RETURNING effect_id, state;
```

If `RETURNING` returns a row: new call, inserted successfully. The engine
returns `Execute { effect_id }` and the proxy forwards the call to the
upstream tool server.

If `RETURNING` returns no rows: the call_signature already exists from a
previous execution. The engine loads the cached result from
`undolog_effect_log.result_snapshot` and returns `Replay { effect_id,
result }`. The proxy returns the cached result to the caller without
executing the tool. The effect state transitions to `replayed` and the
`replay_count` counter increments.

The UNIQUE constraint on `undolog_effect_log.call_signature` is the
last-resort safety net. It catches cases the advisory lock cannot:
PostgreSQL failover (lock metadata lost on promotion), multiple database
connections that bypass the proxy, or a lock acquisition failure that
raced past the retry limit.

## The Intercept Flow (Complete)

```
Tool call arrives at proxy
  ↓
Proxy normalizes args → canonical JSON
  ↓
Proxy sends ToolCall to Rust engine via gRPC
  ↓
Engine computes BLAKE3 signature
  ↓
Engine acquires pg_try_advisory_xact_lock(FNV-1a(signature))
  ↙        ↘
acquired    not acquired (retry × 3 → error)
  ↓
INSERT ... ON CONFLICT DO NOTHING
  ↙        ↘
inserted    already exists
  ↓           ↓
Execute     load cached result
  ↓           ↓
return      return
Outcome::   Outcome::
Execute     Replay
```

## Trade-offs

**Canonical JSON is strict.** Two JSON objects that differ only in key
ordering produce different signatures. This is intentional: the system
treats structurally different arguments as different calls. If a tool
adds a default parameter that a client does not send, those are two
different signatures. Teams must standardize argument formatting at the
client level.

**BLAKE3 vs. FNV-1a.** The call signature uses BLAKE3 (cryptographic,
256-bit, no collisions for this use case). The advisory lock key uses
FNV-1a (non-cryptographic, 64-bit, collision-prone at scale). FNV-1a
collisions are possible with >10,000 concurrent signatures. A collision
causes two different tool calls to contend for the same lock, not a
correctness issue, but a performance one. The unique constraint catches
any collision-induced race.

**Advisory lock overhead.** Each intercept acquires a PostgreSQL advisory
lock. Under extreme concurrency (>1000 requests/second per signature),
the lock manager becomes a bottleneck. This is acceptable because tool
calls are not web requests: an agent session makes sequential calls.

## Alternatives Considered

**Framework-level idempotency keys (standard approach).** Generated at the
framework layer, not derived from call content. Cannot correlate retries
across components. Rejected because it solves transport-level but not
application-level deduplication.

**Application-level deduplication.** The tool server stores a map of
seen call signatures and returns cached results. Requires changes to
every tool implementation. Rejected because it violates the separation
of concerns: the tool should not need to know about idempotency.

**Optimistic insert without advisory lock.** Rely on the UNIQUE constraint
alone. Works correctly but produces PostgreSQL serialization errors under
concurrent inserts. The advisory lock reduces contention by preventing
most conflicts before the INSERT.

## Further Reading

- [Safety Model](./safety-model.md): how tiers interact with replay
- [Saga Pattern](./saga-pattern.md): replay of undo stack entries on
  process restart
- Code: `crates/undolog-types/src/effect.rs:27-59`. CallSignature
  computation
- Code: `crates/undolog-store/src/effect_store.rs:52-81`: advisory lock
  acquisition
- Code: `services/undolog-proxy/internal/lock/advisory.go:10-14`. Go
  FNV-1a implementation
- Code: `sdks/undolog-py/undolog_sdk/signature.py:45-109`. Python
  call_signature implementation
- Schema: `migrations/0001_initial.sql:344-350`. UNIQUE constraint on
  call_signature
