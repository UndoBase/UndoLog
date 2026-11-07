---
title: "ADR 0023: Engine Transport TLS and Optional mTLS"
description: "- **Date:** 2026-11-07 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0023: Engine Transport TLS and Optional mTLS

- **Date:** 2026-11-07
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The UndoLog proxy connects to the effect engine over gRPC on port 50051.
[ADR 0018](0018-proxy-grpc-auth-method.md) introduced shared bearer token
authentication for this boundary using `UNDOLOG_ENGINE_AUTH_TOKEN` and
`UNDOLOG_PROXY_ENGINE_AUTH_TOKEN`. In doing so, ADR 0018 accepted plaintext
TCP transport under the assumption that proxy and engine share a trusted
local deployment network, noting that encryption could be revisited if the
endpoint required transport-level confidentiality.

In distributed, multi-host, and zero-trust cloud environments, plaintext
transport presents security risks:

- Tool execution inputs and journaled results passing between proxy and engine
  are visible to network observers on unencrypted networks.
- Bearer tokens transmitted in gRPC metadata can be sniffed if transport
  encryption is absent.
- Enterprise security policies often require cryptographic verification of
  both server and client identity across all inter-service communication.

Requirements:

- Support transport encryption (TLS) for proxy-to-engine gRPC calls.
- Support optional mutual authentication (mTLS) where the engine verifies
  the proxy's client certificate.
- Retain plaintext TCP as the default when no TLS variables are set,
  preserving friction-free local development and docker-compose workflows.
- Maintain complete orthogonality with token authentication: bearer tokens
  and TLS may be used together for defense in depth or independently.
- Fail closed on incomplete or contradictory configuration at startup.
- Follow existing configuration conventions: standard environment variables
  pointing to filesystem paths of PEM certificates and private keys.
- Keep certificate lifecycle straightforward: TLS credentials are loaded
  at process startup only.

## Decision

Amend [ADR 0018](0018-proxy-grpc-auth-method.md) to support optional transport
TLS and optional mutual TLS (mTLS) for the proxy-to-engine gRPC leg.

### Engine Server Configuration (Rust)

The engine introduces three configuration variables for TLS:

1. `UNDOLOG_ENGINE_TLS_CERT_PATH`: path to the PEM-encoded server certificate
   chain.
2. `UNDOLOG_ENGINE_TLS_KEY_PATH`: path to the PEM-encoded server private key.
3. `UNDOLOG_ENGINE_TLS_CLIENT_CA_PATH`: optional path to a PEM-encoded CA
   bundle used to verify client certificates (enabling mTLS).

Engine configuration rules:

- **Plaintext default:** If neither `UNDOLOG_ENGINE_TLS_CERT_PATH` nor
  `UNDOLOG_ENGINE_TLS_KEY_PATH` is set, the engine serves unencrypted gRPC
  (matching previous behavior).
- **Enforcement pair:** Both `UNDOLOG_ENGINE_TLS_CERT_PATH` and
  `UNDOLOG_ENGINE_TLS_KEY_PATH` must be configured together. If only one is
  supplied, engine startup fails with a configuration error.
- **Mutual TLS (mTLS):** When `UNDOLOG_ENGINE_TLS_CLIENT_CA_PATH` is configured
  alongside server TLS, the engine requires and verifies client certificates
  from connecting proxies. Setting client CA without server TLS is a startup
  error.

### Proxy Client Configuration (Go)

The proxy introduces three configuration variables for TLS to the engine:

1. `UNDOLOG_PROXY_ENGINE_TLS_CA_PATH`: optional path to a PEM-encoded CA
   bundle used to verify the engine server certificate. If unset while TLS is
   active, the proxy falls back to the system root CA pool.
2. `UNDOLOG_PROXY_ENGINE_TLS_CLIENT_CERT_PATH`: path to the PEM-encoded client
   certificate presented to the engine for mTLS.
3. `UNDOLOG_PROXY_ENGINE_TLS_CLIENT_KEY_PATH`: path to the PEM-encoded client
   private key for mTLS.

Proxy configuration rules:

- **Activation:** The proxy enables gRPC TLS when `UNDOLOG_PROXY_ENGINE_TLS_CA_PATH`
  or `UNDOLOG_PROXY_ENGINE_TLS_CLIENT_CERT_PATH` is set.
- **Client key pair:** `UNDOLOG_PROXY_ENGINE_TLS_CLIENT_CERT_PATH` and
  `UNDOLOG_PROXY_ENGINE_TLS_CLIENT_KEY_PATH` must be configured together. If
  only one is provided, proxy startup fails with a configuration error.
- **Plaintext default:** If none of the TLS variables are set, the proxy
  connects to the engine over plaintext TCP.

### Independence from Token Authentication

Transport TLS and shared bearer token authentication ([ADR 0018](0018-proxy-grpc-auth-method.md))
are completely decoupled:

- A deployment may use TLS alone, token authentication alone, both (defense
  in depth), or neither (local development).
- TLS guarantees confidentiality, integrity, and cryptographic host identity.
- Bearer tokens enforce application-level gRPC caller authorization.

### Startup-Only Loading Lifecycle

TLS certificates, private keys, and CA trust roots are loaded into memory
once during process initialization:

- Neither binary implements file watching, hot reloading, or dynamic key
  polling.
- Certificate rotation is performed by updating mounted secret volumes and
  restarting the proxy and engine processes.

## Alternatives Considered

### Alternative 1: Opt-In TLS via Environment-Configured Paths (Chosen)

- **Pros:** Preserves zero-setup local development and existing plaintext
  deployments without breaking changes. Works naturally with container secret
  mounts (such as Kubernetes Secret volumes or Docker secrets). Introduces zero
  new runtime dependencies beyond the built-in TLS stacks (`rustls`/`tonic` in
  Rust, `crypto/tls` in Go).
- **Cons:** Operators must provision and mount certificates and coordinate
  process restarts upon renewal.
- **Chosen?** Yes. It offers standard, robust enterprise security while
  maintaining simplicity and backwards compatibility.

### Alternative 2: Mandatory TLS with Self-Signed Certificates (Rejected)

- **Pros:** Guarantees wire encryption in all environments out of the box.
- **Cons:** Automatically generated self-signed certificates require complex
  cross-container trust bundle sharing in docker-compose and local environments.
  Causes handshake failures and friction for new developers.
- **Chosen?** No. Plaintext default is superior for local development and
  testing.

### Alternative 3: Defer Entirely to Service Meshes or Network Overlays (Rejected)

- **Pros:** Eliminates TLS configuration logic from application code.
- **Cons:** Restricts production deployment options by forcing teams to run a
  service mesh (such as Istio or Linkerd) or host-level IPsec/WireGuard tunnels.
  Fails users deploying on bare VMs or lightweight container runners.
- **Chosen?** No. Application-level TLS provides defense in depth regardless
  of underlying network topology.

## Consequences

**Positive:**

- The gRPC channel between proxy and engine can be encrypted in transit,
  protecting sensitive effect payloads and tokens.
- Mutual TLS provides cryptographic identity assurance between proxy and engine.
- Backwards compatibility is preserved: existing compose and local workflows
  continue to run without modification.
- Incomplete configurations fail fast at startup before serving traffic.

**Negative:**

- Deployments opting into TLS must manage certificate generation, trust
  distribution, and renewal cycles.
- Certificate updates require process restarts.

**Risks:**

- Certificate expiration leading to unexpected connection termination; mitigated
  by explicit startup validation, error logging during handshake failures, and
  standard monitoring.
- Hostname verification mismatches between certificate Subject Alternative Names
  and connection targets; mitigated by documenting SAN requirements.

## References

- [ADR 0001](0001-rust-for-effect-engine.md): the engine is a separate Rust process
- [ADR 0002](0002-go-for-mcp-proxy.md): the proxy is a separate Go process
- [ADR 0018](0018-proxy-grpc-auth-method.md): gRPC auth method selection and bearer token authorization
- [Configuration reference](../reference/configuration.md): where proxy and engine configuration variables are documented
