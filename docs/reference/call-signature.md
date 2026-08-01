---
title: "Call Signature Reference"
description: "The call signature is a 256-bit BLAKE3 hash that uniquely identifies one tool call invocation. It provides exactly-once execution semantics across all SDKs and services."
section: "reference"
---
# Call Signature Reference

The call signature is a 256-bit BLAKE3 hash that uniquely identifies one tool call invocation. It provides exactly-once execution semantics across all SDKs and services.

---

## Algorithm

### Input byte stream

```
[session_id: 16 bytes]
[step_index: 4 bytes LE]
[tool_name_len: 4 bytes LE][tool_name: N bytes UTF-8]
[canonical_args_len: 4 bytes LE][canonical_args: M bytes UTF-8]
```

### Output

64-character lowercase hex string (BLAKE3-256 hash of the above byte stream).

### Implementation

**Rust (`undolog-types/src/effect.rs`):**

```rust
let mut hasher = blake3::Hasher::new();
hasher.update(session_id.as_uuid().as_bytes());
hasher.update(&step_index.to_le_bytes());
let name_bytes = tool_name.as_bytes();
hasher.update(&(name_bytes.len() as u32).to_le_bytes());
hasher.update(name_bytes);
let canon = canonical_json(canonical_args);
let args_bytes = canon.as_bytes();
hasher.update(&(args_bytes.len() as u32).to_le_bytes());
hasher.update(args_bytes);
Self(hasher.finalize().to_hex().to_string())
```

**Python (`undolog_sdk/signature.py`):**

```python
sid = uuid.UUID(session_id)
hasher = blake3.blake3()
hasher.update(sid.bytes)
hasher.update(struct.pack("<I", step_index))
hasher.update(struct.pack("<I", len(name_bytes)))
hasher.update(name_bytes)
hasher.update(struct.pack("<I", len(args_bytes)))
hasher.update(args_bytes)
return hasher.hexdigest()
```

---

## Canonical JSON

Deterministic, sorted-key JSON string suitable for hashing. Produces byte-for-byte identical output across the Rust, Python, and TypeScript SDKs. The Go proxy implements a separate, structurally different canonical JSON (it wraps the call in `{"tool_name", "args"}` and preserves numeric tokens verbatim), so it is not byte-identical to this contract.

### Rules

| Rule | Description |
|------|-------------|
| Key sorting | Object keys are sorted lexicographically ascending. Recursively applied for nested objects. |
| No whitespace | Compact representation with no spaces, tabs, or newlines. |
| Leaf values | Strings, booleans, and null use standard JSON serialisation. Numbers follow the ECMAScript `JSON.stringify` rules (RFC 8785, Section 3.2.2.2) described below. |
| Non-finite floats | `NaN`, `Infinity`, and `-Infinity` are rejected: Python raises `ValueError`, TypeScript throws `TypeError`, and Rust rejects them at `serde_json::Value` construction (`Number::from_f64` returns `None`). Per RFC 8785, non-finite values are not valid JSON and must cause an error. |
| Negative zero | `-0.0` serialises as `0`, matching ECMAScript `JSON.stringify` and RFC 8785. |
| Float notation | Fixed notation for values in `[1e-6, 1e21)`; exponential notation elsewhere. Exponential exponents have no leading zeros (`1e-7`, `1e+21`). Matches ECMAScript `Number.prototype.toString`. |
| Integers | Serialised as-is with no decimal point or exponent (`42`, `0`, `-7`). |
| Arrays | Preserved in insertion order. Elements recursively canonicalised. |

### Examples

| Input | Canonical JSON |
|-------|----------------|
| `{"b": 1, "a": 2}` | `{"a":2,"b":1}` |
| `{"z": {"b": 2, "a": 1}}` | `{"z":{"a":1,"b":2}}` |
| `[3, 1, 2]` | `[3,1,2]` |
| `{"name": "alice", "age": 30}` | `{"age":30,"name":"alice"}` |
| `{"v": 1e-7}` | `{"v":1e-7}` |
| `{"v": -0.0}` | `{"v":0}` |
| `{"v": 1e21}` | `{"v":1e+21}` |

### Implementation references

| Language | Location |
|----------|----------|
| Rust | `undolog-types/src/effect.rs`: `pub fn canonical_json(v: &serde_json::Value) -> String` |
| Python | `undolog_sdk/signature.py`: `def canonical_json(value: Any) -> str` |
| TypeScript | `sdks/undolog-ts/src/signature.ts`: `export function canonicalJson(value: unknown): string` |
| Go (proxy, separate format) | `services/undolog-proxy/internal/proxy/signature.go`: `func writeCanonicalJSON(buf *bytes.Buffer, v any) error` |

---

## BLAKE3 Details

| Property | Value |
|----------|-------|
| Algorithm | BLAKE3 (default mode) |
| Output length | 256 bits (32 bytes) |
| Hex length | 64 characters |
| Character set | `[0-9a-f]` (lowercase) |
| Language | `blake3` Rust crate / `blake3` Python PyPI package |

### Cross-language invariant

Same inputs → same 64-char hex signature, regardless of language or platform.

---

## Collision Properties

| Property | Value |
|----------|-------|
| Preimage resistance | 256 bits |
| Collision resistance | 128 bits (BLAKE3 birthday bound) |
| Unique constraint | `undolog_effect_log.call_signature` has `UNIQUE` index |
| Conflict strategy | `INSERT ... ON CONFLICT (call_signature) DO NOTHING` |
| Collision probability | Less than 2^-128 for distinct inputs. Sufficient for the application use case. |

### Input uniqueness

The length-prefixed encoding format prevents boundary attacks where two different `(tool_name, args)` pairs could produce the same byte sequence without delimiters. For example:

```
step=1, tool="ab", args="c"     → 01|00:00:00|02:00:00:00|ab|01:00:00:00|c
step=1, tool="a",  args="bc"    → 01|00:00:00|01:00:00:00|a |02:00:00:00|bc
```

The length prefixes ensure these produce different hashes even though the concatenated raw bytes would overlap.

---

## FNV-1a Advisory Lock Derivation

The call signature is used to derive PostgreSQL advisory lock keys for concurrent write protection.

### Algorithm

```
FNV-1a 64-bit hash of the 64-char hex signature string
→ int64 (signed, for pg_try_advisory_xact_lock)
```

### Implementation references

| Language | Location |
|----------|----------|
| Rust | `undolog-engine/src/engine.rs` internal logic |
| Go | `services/undolog-proxy/internal/lock/advisory.go`: `func AdvisoryLockKey(signature string) int64` |

### Lock strategy

```
1. Compute AdvisoryLockKey(call_signature)
2. SELECT pg_try_advisory_xact_lock(key)
3. If false: retry up to UNDOLOG_LOCK_MAX_ATTEMPTS with UNDOLOG_LOCK_RETRY_MS backoff
4. If still false: return AdvisoryLockTimeout error
5. INSERT INTO undolog_effect_log ... ON CONFLICT (call_signature) DO NOTHING
```

The advisory lock is a performance optimisation (avoids write conflict rollbacks under high concurrency). The `UNIQUE` constraint on `call_signature` is the last-resort safety net.

---

## Database Column

| Column | Type | Constraint |
|--------|------|------------|
| `call_signature` | `char(64)` | `NOT NULL`, `UNIQUE` across all partitions |

---

## Testing properties

| Property | Test |
|----------|------|
| Deterministic | Same inputs → same output every time |
| Length | Always 64 lowercase hex characters |
| Step sensitivity | Different `step_index` → different signature |
| Args sensitivity | Different args → different signature |
| Key order invariance | `{"b":1,"a":2}` and `{"a":2,"b":1}` produce the same signature |
| Cross-language | Python, Rust, and Go produce identical output for identical inputs |
