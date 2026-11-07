---
title: "ADR 0022: Administrative API Key Roles for the Proxy"
description: "- **Date:** 2026-11-07 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0022: Administrative API Key Roles for the Proxy

- **Date:** 2026-11-07
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The UndoLog proxy authenticates incoming HTTP and MCP-style tool call
requests using an API key map configured via `UNDOLOG_PROXY_API_KEYS`
or the TOML `api_keys` table ([ADR 0002](0002-go-for-mcp-proxy.md),
[ADR 0020](0020-proxy-rate-limiting.md)). Every authenticated key resolves
to an organization identifier, and all keys hold equal permissions to
execute and query tenant-scoped routes.

As UndoLog introduces administrative control endpoints (such as dead-letter
queue inspection, compensation retry or skip decisions, and dynamic rate-limit
policy adjustments), the proxy requires a separation between normal agent
callers and administrative operators. Normal callers must not be permitted
to list dead letters, mutate rate limits, or retry compensations.

At the same time, administrative operations must remain strictly tenant-isolated:
an administrative operator for organization A must never inspect or mutate
dead letters or rate limits belonging to organization B.

Requirements:

- Administrative access must be explicitly partitioned from standard tenant
  traffic.
- Admin callers receive an administrative role that permits access to admin
  endpoints as well as their own organization tenant routes.
- Member callers must be denied access to admin endpoints by default.
- Administrative access is denied by default when no admin keys are
  configured.
- Admin keys remain bound to a single organization: an admin key cannot view
  or mutate resources of another tenant.
- Configuration must follow existing key map conventions without introducing
  unnecessary external dependencies or identity providers.

## Decision

Introduce an explicit administrative API key mapping:

1. **Configuration:** Support `UNDOLOG_PROXY_ADMIN_API_KEYS` (comma-separated
   `key=org` pairs) and a TOML `admin_api_keys` table matching the format of
   `api_keys`.
2. **Role Association:** Keys in `admin_api_keys` receive an internal `admin`
   role, while keys in `api_keys` receive a `member` role.
3. **Route Authorization:**
   - Admin routes (such as `/api/v1/admin/*`) require the `admin` role and
     are restricted to the authenticated organization.
   - Tenant routes (such as `/v1/tools/execute`, `/v1/approvals/*`) accept
     both `member` and `admin` roles for the authenticated organization.
   - Member keys requesting admin routes receive HTTP 403 Forbidden.
4. **Collision Prevention:** Any key present in both `api_keys` and
   `admin_api_keys` is treated as a configuration conflict and causes the
   proxy to fail startup with an informative error.
5. **Restart-Only Configuration:** Key maps, role bindings, and tenant
   associations are loaded at startup only. Dynamic key mutation without
   restarting the proxy is out of scope.
6. **Scope Boundary:** Administrative API keys provide lightweight role
   separation for operational proxy endpoints. They are not a substitute for
   a full multi-tenant RBAC system or an external OpenID Connect (OIDC)
   identity provider.

## Alternatives Considered

### Alternative 1: Explicit Admin Key Map with Attached Role (Chosen)

- **Pros:** Reuses existing key-to-org lookup structures and constant-time
  comparison routines. Minimizes code changes and introduces zero new external
  dependencies. Enforces role checks in middleware cleanly. Denies admin access
  by default when the map is empty.
- **Cons:** Operators manage two separate key lists when both member and admin
  access are needed.
- **Chosen?** Yes. It provides clear, leak-free authorization with minimal
  complexity while maintaining tenant isolation.

### Alternative 2: Role Prefix or Suffix on Existing Keys (Rejected)

- **Pros:** Single configuration map with role encoded in the key string
  (e.g. `admin_key1=org1`).
- **Cons:** Couples secret formatting with authorization logic. Increases
  the risk of accidental privilege escalation if prefixes are misparsed or
  truncated. Leaks role metadata in keys.
- **Chosen?** No. Secret values should remain opaque tokens rather than
  encoding permissions in their syntax.

### Alternative 3: Database-Backed RBAC or OIDC Provider (Rejected)

- **Pros:** Fine-grained permissions, user directory synchronization, and
  runtime key rotation without proxy restarts.
- **Cons:** Adds heavyweight dependencies (database schemas, token verification
  libraries, network hops to identity providers) to a lightweight proxy designed
  for high-throughput local or container deployments.
- **Chosen?** No. Disproportionate operational complexity for current proxy
  hardening requirements.

## Consequences

**Positive:**

- Administrative routes for dead-letter queues and rate limiting are protected
  behind explicit role verification.
- Member agent keys cannot invoke administrative endpoints.
- Admin keys remain strictly scoped to their assigned organization, preventing
  cross-tenant data leakage.
- Clear error handling: overlapping keys fail startup immediately before
  traffic is accepted.

**Negative:**

- Operators must maintain two configuration variables or TOML tables when
  provisioning both agent and admin keys.
- Key rotation requires a proxy process restart.

**Risks:**

- Operators who configure no admin keys cannot access administrative routes;
  mitigated by documenting this default-deny posture.
- An admin key that is compromised has administrative control over that
  organization; mitigated by recommending strong key entropy and distinct
  credentials.

## References

- [ADR 0002](0002-go-for-mcp-proxy.md): the proxy is a separate Go process
- [ADR 0018](0018-proxy-grpc-auth-method.md): proxy authentication to the engine
- [ADR 0020](0020-proxy-rate-limiting.md): per-key rate limiting and backpressure
- [Configuration reference](../reference/configuration.md): where proxy configuration variables are documented
