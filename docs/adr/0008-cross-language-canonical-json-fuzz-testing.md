---
title: "ADR 0008: Cross-Language Canonical JSON Fuzz Testing Strategy"
description: "- **Date:** 2026-09-10 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0008: Cross-Language Canonical JSON Fuzz Testing Strategy

- **Date:** 2026-09-10
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The exactly-once guarantee depends on Python, Go, TypeScript, and Rust producing byte-identical canonical JSON for every possible input. The canonical JSON encoding is the input to BLAKE3 call-signature hashing (ADR 0003), and the engine recomputes its own dedup key from it. If any language produces different output for the same logical input, two tool calls that should be identical produce different signatures, or worse, two different calls produce the same signature.

The existing parity test covers one hardcoded digest. Edge cases (NaN, empty dicts, escaped characters, nested structures, unicode, large numbers) are untested across languages. The failure mode is silent: a divergence would only surface as a missed dedup hit or, worse, a false dedup, both of which break the safety model without any compile-time or runtime error.

This ADR defines the fuzz testing strategy that PR-2 implements.

## Decision

Use property-based fuzz testing with a Python reference implementation as the source of truth.

### Canonical JSON contract

All four implementations must produce byte-identical output for the canonical JSON encoding of `{"tool_name": <string>, "args": <object>}`. The rules are:

1. Keys sorted recursively in lexicographic order (byte-level, Unicode-aware).
2. No whitespace between tokens.
3. Numbers follow ECMAScript `Number.prototype.toString()` rules:
   - Integers beyond 2^53 must be represented exactly (Python `int`, Rust `i64`/`u64`, TypeScript `bigint`).
   - IEEE 754 special values: `NaN` encodes as `"NaN"`, `Infinity` as `"Infinity"`, `-Infinity` as `"-Infinity"`, `-0` as `"0"`.
4. Strings are JSON-escaped (no raw unicode in output, `\uXXXX` for control characters).
5. `null`, `true`, `false` as lowercase literals.
6. Empty objects `{}` and empty arrays `[]` preserved as-is.

### Fuzz testing architecture

| Component | Role |
|---|---|
| Python script (`tests/fixtures/generate_canonical_json_fixtures.py`) | Generates reference fixtures. Produces N random JSON structures and their expected canonical JSON bytes. |
| TypeScript test (`sdks/undolog-ts/tests/unit/signature.test.ts`) | Loads fixtures, calls `canonicalJson()` from the SDK, asserts byte-identical output. |
| Go test | Loads same fixtures, calls canonical JSON function, asserts byte-identical output. |
| Rust test | Loads same fixtures, calls `canonical_json()`, asserts byte-identical output. |
| CI workflow | Runs `generate_canonical_json_fixtures.py` to produce fresh fixtures, then runs all four language tests against them. |

### Fixture categories

| Category | Count | Purpose |
|---|---|---|
| Random structures | 10,000+ | Statistical coverage of JSON shape space. |
| Edge: NaN, Infinity, -0 | 20 each | Verify IEEE 754 special-value handling. |
| Edge: empty dicts/arrays | 10 each | Verify no special-casing or omission. |
| Edge: deeply nested objects | 10 (depth 50+) | Verify recursion does not produce different output. |
| Edge: unicode escapes | 20 | Verify surrogate pairs, BMP characters, supplementary planes. |
| Edge: bigint-representable integers | 20 | Verify integers beyond 2^53 are exact. |
| Edge: float boundaries | 10 | Verify 5e-324, 1e21, 9007199254740993, MIN_SAFE_INTEGER, MAX_SAFE_INTEGER. |
| Edge: empty strings, long strings | 10 each | Verify string boundary handling. |
| Edge: special characters in keys | 10 | Verify key escaping matches value escaping. |

### Fixture format

```json
[
  {
    "input": {"tool_name": "send_email", "args": {"to": "a@b.com", "subject": "Hello"}},
    "canonical": "{\"args\":{\"to\":\"a@b.com\",\"subject\":\"Hello\"},\"tool_name\":\"send_email\"}"
  },
  ...
]
```

### CI integration

- The Python fixture generator runs on every PR as a CI job.
- Generated fixtures are committed to `tests/fixtures/canonical-json-fuzz/` as the checked-in baseline.
- CI regenerates fixtures and asserts the diff is empty, ensuring the reference implementation has not drifted.
- If a language implementation produces different output, the test fails and CI blocks the PR.

## Alternatives Considered

### Alternative 1: Property-based fuzz testing with Python reference (Chosen)

- **Pros:** Catches emergent divergence across the full input space. Python is the canonical reference (the original SDK). Fixture format is language-agnostic and reviewable.
- **Cons:** Requires a Python fixture generator in CI. Large fixture files (10k entries) are ~5 MB.
- **Chosen?** Yes. The statistical coverage is necessary to catch edge cases that hardcoded tests miss. The 5 MB cost is acceptable for CI and the fixtures are diffable.

### Alternative 2: Shared golden-file test vectors (static)

- **Pros:** Simple to implement, no CI-time generation step.
- **Cons:** Only tests the vectors someone thought to write. Misses emergent divergence. The fixed set must be manually expanded when new edge cases are discovered.
- **Chosen?** No. This is what the existing test does (one hardcoded digest). It is insufficient for catching the edge cases that matter.

### Alternative 3: Cross-language in-process testing (Rust drives all four implementations)

- **Pros:** Single process, deterministic, fast.
- **Cons:** Requires embedding Python and Go interpreters in the Rust test binary. Fragile, platform-dependent, and impractical for CI on all platforms.
- **Chosen?** No. The engineering cost is prohibitive for the testing benefit.

## Consequences

**Positive:** The exact-once safety model gains a regression gate that catches silent divergence across all four language implementations. Edge cases that have historically caused cross-language bugs (NaN handling, unicode escapes, bigint precision) are covered explicitly.

**Negative:** CI must run a Python step to generate fixtures. The fixture files grow with the number of test cases and must be regenerated periodically. Go and Rust tests must depend on the same fixture format.

**Risks:** The Python reference implementation itself could have a bug. Mitigation: the fixture generator is reviewed alongside the ADR, and the canonical JSON rules are defined precisely enough to audit independently. Cross-language parity testing catches *divergence*, not correctness of the reference; a separate review of the Python canonical JSON implementation is out of scope for this ADR.

## References

- ADR 0003: BLAKE3 for Call Signature Hashing
- plan/typescript-sdk.md, TS-1 (Cross-Language Canonical JSON Fuzz Testing)
- report/analysis.md sections 3.5, 8, 9.5
- docs/reference/call-signature.md (canonical JSON contract)
- docs/explanation/exactly-once-semantics.md (canonical JSON role in dedup)
