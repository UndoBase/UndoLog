---
title: "ADR 0003: BLAKE3 for Call Signature Hashing"
description: "- **Date:** 2026-01-29 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0003: BLAKE3 for Call Signature Hashing

- **Date:** 2026-01-29
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The call signature uniquely identifies a tool invocation by its function name and serialized arguments. This identifier must be deterministic across three programming languages (Python, Go, Rust) to ensure the same tool call produces the same hash in every layer. It must be collision-resistant because uniqueness is a safety guarantee, two different tool calls with the same signature could cause incorrect locking or compensation. It must be fast because the hash is computed on every tool call intercepted. It must produce a fixed-size output suitable for a `VARCHAR` column with a `UNIQUE` index and as input to the advisory lock key derivation.

## Decision

Use BLAKE3 with canonical JSON serialization (sorted keys, no whitespace) as the input representation.

## Alternatives Considered

### SHA-256

- **Pros:** FIPS-certified, universally available in standard libraries across all target languages, well-understood security properties.
- **Cons:** Significantly slower than BLAKE3 in software implementations. No hardware acceleration in common Python/Go/Node.js runtimes (unlike BLAKE3's SIMD optimizations).
- **Chosen?** No. The performance gap is material at hot-path interception volume, and FIPS certification is not required for this use case.

### SHA-3

- **Pros:** Latest NIST standard, sponge construction offers different security properties than SHA-2.
- **Cons:** Similar software speed to SHA-256. Less library support across the three target languages compared to BLAKE3. Larger state size increases overhead for the small inputs typical of tool call arguments.
- **Chosen?** No. Performance is comparable to SHA-256 but with less ecosystem support.

### xxHash

- **Pros:** Extremely fast: orders of magnitude faster than any cryptographic hash. Ample 64-bit and 128-bit output variants.
- **Cons:** Not collision-resistant. xxHash outputs collisions under adversarial or even accidental high-volume conditions. A collision would cause two different tool calls to share the same lock and compensation identity, breaking UndoLog's core safety guarantee.
- **Chosen?** No. The non-cryptographic properties make xxHash unsuitable for correctness-guaranteeing identifier generation.

## Consequences

**Positive:** BLAKE3 is the fastest cryptographic hash algorithm available, with SIMD optimizations giving near-memory-bandwidth speeds. Native implementations exist for Rust (the `blake3` crate), Python (`blake3` package), and Go (`lukechampine.com/blake3`). The 256-bit output represented as a 64-character hex string fits cleanly in `VARCHAR(64)` for the database index.

**Negative:** BLAKE3 is a newer algorithm compared to SHA-256, which means some security auditors may question its use. Tree hashing adds complexity for streamed inputs (irrelevant here, tool call arguments are typically small, fitting in a single chunk).

**Risks:** BLAKE3 is not FIPS 140-3 certified. If a future compliance requirement mandates FIPS, the project would need to add SHA-256 as an alternative hash mode. The canonical JSON serialization must be implemented identically in all three languages to ensure cross-platform determinism.
