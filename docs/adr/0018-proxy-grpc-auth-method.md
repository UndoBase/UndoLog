---
title: "ADR 0018: gRPC Auth Method Selection"
description: "- **Date:** 2026-10-11 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0018: gRPC Auth Method Selection

- **Date:** 2026-10-11
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The engine serves its gRPC API on port 50051 (its default) with no
authentication. Its intercept, commit, fail, approve, and reject RPCs
can each change an effect's state, so anyone who can reach the port
holds the same power as the proxy. The proxy authenticates its own
callers with the `X-Api-Key` header, but the leg from proxy to engine
is open.

The two processes are written in different languages ([ADR
0001](0001-rust-for-effect-engine.md), [ADR
0002](0002-go-for-mcp-proxy.md)), configured through environment
variables, and deployed side by side inside one network. The decision
must therefore fix a contract that both sides can implement
identically.

Requirements:

- The engine rejects unauthenticated calls with a clear error.
- The proxy presents credentials on every call.
- Configuration rides the existing environment-variable path.
- Local development and the compose stack keep working unchanged when
  no credentials are configured.
- The comparison on the engine side does not leak which bytes matched.

## Decision

The proxy authenticates to the engine with a shared bearer token
carried in gRPC metadata under the `authorization` key, in the form
`Bearer <token>`. The engine reads `UNDOLOG_ENGINE_AUTH_TOKEN`; a
missing or empty value disables enforcement, so local development is
unchanged. The proxy reads `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` and sends
nothing when it is unset. The engine compares the presented token in
constant time and answers a mismatch with `UNAUTHENTICATED` and a
message naming the expected header without echoing any secret.

Enforcement defaults to off, so nothing changes until both sides are
configured. An engine with no configured token ignores any credential
the proxy presents, and a proxy with no token sends nothing. A
partial configuration fails closed rather than open: an enforcing
engine rejects a proxy that presents no token or the wrong one, and
the error names the header to set. A deployment enables the check by
setting the same value in both variables.

## Alternatives Considered

### Alternative 1: Shared Bearer Token in gRPC Metadata (Chosen)

- **Pros:** No certificate lifecycle to operate: no CA, no issuance,
  no renewal, and no trust bundles to distribute across a Rust and a
  Go binary. Credentials ride the configuration path the proxy and
  engine already use. Rotation is an environment change followed by a
  restart. The contract is small enough to test on both sides.
- **Cons:** A static shared secret is weaker than per-connection
  cryptographic identity. Metadata is only as private as the transport
  beneath it, so a deployment that needs encryption on this leg must
  add it at the network or TLS layer. A leaked token is valid until it
  is rotated.
- **Chosen?** Yes. The gRPC port sits inside the deployment network,
  and authenticating it removes the practical attack without taking on
  a PKI the project would have to operate in two languages.

### Alternative 2: Mutual TLS (Rejected)

- **Pros:** Per-connection cryptographic identity with no shared
  secret, and rotation through ordinary certificate renewal.
- **Cons:** Every deployment shape (compose, Kubernetes, bare virtual
  machine) needs its own certificate story in two languages.
  Handshake failures are opaque to operators. Local development needs
  either a baked-in development certificate or a special case.
- **Chosen?** No. The operational cost exceeds the risk reduction for
  a port inside the deployment network. Revisit this if the engine
  endpoint is ever exposed beyond it.

### Alternative 3: Network Controls Only (Rejected)

- **Pros:** Nothing to build or configure.
- **Cons:** Any process that reaches the network can approve effects,
  which makes the approval gate meaningless against a compromised
  neighbour.
- **Chosen?** No. This is the gap the decision closes.

## Consequences

**Positive:** Unauthenticated engine calls fail with `UNAUTHENTICATED`
instead of mutating state. Deployments that never set the variables
behave exactly as before, so no coordinated change accompanies the
rollout. Both sides implement one contract fixed here rather than two
half-compatible guesses.

**Negative:** The two variables must be kept in sync by whatever
deploys the pair, and a token is only as strong as the value chosen
for it. Encryption of the leg remains the deployment's
responsibility.

**Risks:** A token could be written to logs by accident. Mitigation:
both sides treat it as a secret, error messages never echo it, and
neither side logs request metadata. One side could be configured
without the other. Mitigation: the mismatch fails closed, never open:
an engine that requires a token answers every unauthenticated call
with `UNAUTHENTICATED` and names the header to set.

## References

- [ADR 0001](0001-rust-for-effect-engine.md): the engine is a separate
  Rust process
- [ADR 0002](0002-go-for-mcp-proxy.md): the proxy is a separate Go
  process
- [Running in production](../guides/running-in-production.md): where
  deployed configuration is documented
