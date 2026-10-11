---
title: "ADR 0021: Single-Container Deployment"
description: "- **Date:** 2026-10-11 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0021: Single-Container Deployment

- **Date:** 2026-10-11
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

UndoLog ships as two programs in two languages: the engine in Rust
([ADR 0001](0001-rust-for-effect-engine.md)) and the proxy in Go
([ADR 0002](0002-go-for-mcp-proxy.md)). A deployment builds two
images, runs two containers, and wires the proxy to the engine's
gRPC address across the container network. The compose file hides
that wiring for local development with one `docker compose up`, but
a single-node user who wants no orchestration still operates two
units.

Users ask for one thing to deploy. The decision must weigh effort,
performance, and risk for each option, and it must end in a
deliverable rather than an open-ended evaluation. Any change must
leave the multi-container path working, since that is what larger
installations and the Helm chart use.

Requirements:

- Single-node installs get one deployable unit.
- The multi-container deployment keeps working unchanged.
- The engine and proxy keep their languages and their separate
  builds, versioned together.
- Whatever runs inside the unit is supervised so one failed process
  does not leave a half-running container.
- The database stays an operator decision; one unit is not zero
  dependencies.

## Decision

One container image holding both existing binaries, started by a
single entrypoint that launches the engine and the proxy, forwards
SIGTERM to both so each graceful shutdown path runs, and exits when
either process exits so the container runtime restarts both rather
than leaving a half-running unit. Inside the container the proxy
reaches the engine over loopback, which removes the network hop the
split deployment pays.

The combined image is selected by an override compose file rather
than a profile on the base file. A profile cannot do the job: the
base `engine` and `proxy` services are not profile-gated, so a
profile would either break the existing `docker compose up` or start
both topologies at once and collide on the published ports.

Deliverables: the combined image build, the supervising entrypoint,
the compose override, an end-to-end smoke run against the combined
container, and guide updates covering when to prefer the combined
unit and when to keep the split.

## Alternatives Considered

### Alternative 1: One Container Holding Both Binaries (Chosen)

- **Pros:** Single-node users build one image and run one container
  next to the database. No language is given up, no protocol surface
  is rewritten, and the loopback hop makes proxy-to-engine calls
  marginally cheaper. Effort is small: a multi-stage build over the
  existing Dockerfiles, an entrypoint script, and the override file.
- **Cons:** Two processes in one container is not the one-process
  ideal, and their log lines share one stream. Both binaries ship
  together, which is already how they version.
- **Chosen?** Yes. It removes the deployment burden without touching
  the language split, and the supervision rule makes failure
  handling explicit.

### Alternative 2: A Compose Profile on the Base File (Rejected)

- **Pros:** No new image and no entrypoint; selection stays inside
  one compose file.
- **Cons:** One `docker compose up` already starts the whole stack,
  so a profile changes nothing a user feels. Because the base
  services are not profile-gated, enabling the combined topology
  would start it alongside the split services and collide on the
  published ports, and gating the base services would break every
  existing invocation.
- **Chosen?** No. It either changes nothing or breaks the default,
  and it never reduces the number of units to one.

### Alternative 3: Rewrite the Proxy in Rust as One Binary (Rejected)

- **Pros:** A single binary with one toolchain and one language to
  operate, with no entrypoint supervision at all.
- **Cons:** The proxy's HTTP and Server-Sent Events surface,
  including flusher semantics, write-deadline handling, and the
  protocol layer, would be rebuilt in a second implementation and
  re-verified against every integration. Effort is weeks rather
  than days, the risk lands squarely on the request path, and the
  engine gains nothing either way. It also reverses ADR 0002, which
  chose Go for this component.
- **Chosen?** No. The HTTP edge is not the bottleneck; the engine
  already carries the heavy work, so the rewrite buys aesthetics at
  the cost of the safety surface.

## Consequences

**Positive:** Single-node installs build one image and run one
container beside the database. The proxy-to-engine call moves to
loopback. The split stack, and the Helm chart that drives it, are
untouched, and both topologies are covered by the smoke tests.

**Negative:** One container runs two processes, so their logs share
one stream. The unit still needs a database, which the operator
provides.

**Risks:** A supervising entrypoint can leak a child process on
shutdown or hide a crash. Mitigation: the entrypoint forwards
SIGTERM, exits when either child exits, and the smoke run asserts
both shutdown paths. Users could assume the combined unit is the
recommended topology for every size of installation. Mitigation: the
deployment guide states when to prefer each, and the split path
remains the default everywhere it exists today.

## References

- [ADR 0001](0001-rust-for-effect-engine.md): the engine is a
  separate Rust binary
- [ADR 0002](0002-go-for-mcp-proxy.md): the proxy is a separate Go
  binary, which this decision keeps
- [How to deploy with Docker](../guides/deploying-with-docker.md):
  where both topologies are documented
- [Helm installation](../guides/helm-installation.md): the
  multi-container path larger installations use
