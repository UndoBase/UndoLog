---
title: "ADR 0020: Proxy Rate Limiting and Backpressure"
description: "- **Date:** 2026-10-11 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0020: Proxy Rate Limiting and Backpressure

- **Date:** 2026-10-11
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

Every tool call the proxy forwards becomes engine work that touches
Postgres: session lookup, signature check, advisory lock acquisition,
and the effect insert. Today nothing meters any of it. There is no
per-caller limit at the edge, no cap on in-flight engine RPCs, and no
way to stop calling an engine that is already failing. One hot agent
can exhaust the database connection pool, and when it does, every
caller sees failures instead of only the caller causing them.

Two different problems hide behind the word "overload". Fairness is
one caller exceeding its own budget; capacity is the engine and its
database being unable to take more work. They need different
mechanisms, different scopes, and different status codes. Conflating
them produces client behavior that is wrong in both directions:
slowing down when the correct answer is "retry later", or retrying
when the correct answer is "you are going too fast".

The engine is a separate process that cannot be configured from the
proxy. It limits its own concurrency internally today, but every
engine error currently surfaces as an internal gRPC status, so
overload is indistinguishable from failure for clients.

Requirements:

- One caller cannot starve the others.
- In-flight engine RPCs are structurally capped, so the connection
  pool cannot exhaust regardless of how many callers are active.
- Short bursts absorb through bounded waiting, not unbounded queues
  or instant failures.
- Overload reaches clients as a retryable signal that is distinct
  from a per-caller rate violation.
- Every mechanism is off unless configured: existing deployments
  behave exactly as before.
- Each rejection reason is visible on `/metrics`.

## Decision

Three mechanisms, each with a fixed scope and status code.

**Per-key token bucket at the edge.** Each resolved API key gets a
token bucket, metered by the constant-time-matched key digest rather
than the raw key, configured by requests per second and burst size.
A request over its bucket answers 429 with `Retry-After`. This
limiter is the only source of 429 in the proxy. It ships as a plugin
in the chain from [ADR 0019](0019-proxy-plugin-system.md), is
implemented in package rather than pulling in an external rate
library, and counts rejections on `/metrics`.

**Queue, gate, and breaker on the engine RPC path.** The proxy's
engine client wraps the five client-facing RPCs (`Intercept`,
`Commit`, `Fail`, `Approve`, `Reject`) in a bounded wait queue that
admits a request when a slot frees inside a small wait budget, a
concurrency gate that caps in-flight RPCs, and a circuit breaker
that stops calling a failing engine and probes it again on a timer.
Background reconciliation calls are exempt so approvals are never
blocked behind a busy tool path. Every rejection from this layer
answers 503 with `Retry-After` and carries a reason label on
`/metrics`. The cap on in-flight RPCs is the structural reason the
database pool cannot exhaust: fewer requests can be waiting on
Postgres than the pool can serve.

**Engine-side admission control stays in the engine.** The engine
limits concurrency internally today, and its queue, breaker, and
overload mapping are its own to add independently. The proxy never
configures engine internals; it translates `RESOURCE_EXHAUSTED`
into the same 503 with `Retry-After`, replacing the generic 502 an
engine failure answers today, so genuine engine-side overload
reaches clients as backpressure instead of a bad-gateway error.
Until the engine emits that signal, the proxy's own queue, gate,
and breaker produce the 503s.

**Status-code contract.** 429 means the caller exceeded its own
rate; only the per-key limiter sends it, and a client that fixes its
rate never sees it again. 503 with `Retry-After` means server
capacity: full queue, expired wait, full gate, open breaker, or an
overloaded engine. Queue overflow answers 503, not 429, because
overflow is capacity: the caller may be well within its own rate.
Both carry `Retry-After`, so SDK retry behavior is identical either
way; the split exists for operators and dashboards, where "clients
are too fast" and "the server is saturated" must not share a
counter.

**Disabled by default.** Every setting arrives as environment
variables for the per-second rate, burst, in-flight cap, queue
depth, wait budget, breaker threshold, and breaker reset interval.
With all of them unset there is no bucket, no queue, no gate, and
no breaker, which is exactly today's behaviour.

## Alternatives Considered

### Alternative 1: Three Layers With Distinct Status Codes (Chosen)

- **Pros:** Fairness and capacity are metered where each is known:
  identity at the edge, in-flight work at the narrowest point before
  the database, pool state inside the engine. Each rejection tells
  the client something actionable, and every layer is independently
  configurable and testable.
- **Cons:** Seven environment variables and a status-code contract
  operators must learn. Three rejection reasons to label and chart.
- **Chosen?** Yes. It is the only shape that protects the pool
  structurally while still telling a hot client apart from a
  saturated server.

### Alternative 2: One Limit for Everything (Rejected)

- **Pros:** One knob and one status code to document.
- **Cons:** A single per-key bucket cannot cap aggregate in-flight
  work: many individually compliant keys still exhaust the pool. A
  single global cap punishes a well-behaved client for a hot one,
  and its rejections cannot say which problem occurred.
- **Chosen?** No. Both failure modes are the ones this design
  exists to prevent.

### Alternative 3: Reject-Only Backpressure, No Queue (Rejected)

- **Pros:** No wait-budget tuning and no requests held in the
  proxy; the failure path is immediate and obvious.
- **Cons:** A single agent step issues parallel tool calls, so
  micro-bursts become 503s that a short wait would have completed.
  The SDKs do not retry mutating calls, so each rejected burst loses
  work rather than delaying it.
- **Chosen?** No. A bounded wait with a small budget absorbs bursts
  while keeping outages fast-failing.

### Alternative 4: Enforcement Only Inside the Engine (Rejected)

- **Pros:** One enforcement point, closest to the resource being
  protected.
- **Cons:** By the time a request crosses the network the hop is
  already spent, and the engine cannot see API-key identity, so it
  cannot meter per caller. A slow engine accepts work into its
  socket backlog before its own limiter ever runs.
- **Chosen?** No. The proxy must cap what leaves it, and fairness
  requires the identity the proxy alone resolves.

## Consequences

**Positive:** A hot key is identifiable by its own 429s while pool
protection applies regardless of identity. Bursts absorb instead of
failing, engine outages stop being hammered, and overload reaches
clients as retryable 503s instead of 502s. Dashboards separate
caller fault from capacity. Deployments that configure nothing see
no change.

**Negative:** The queue adds a wait budget to tune, and operators
must learn the 429 versus 503 split. The engine client now wraps
its RPCs with three mechanisms it did not have before.

**Risks:** A wait budget set too high holds requests and
connections during an outage. Mitigation: the budget is small by
default and queue depth and in-flight counts are gauged on
`/metrics`. A breaker threshold set too low marks a healthy engine
down during a blip. Mitigation: the threshold and reset interval
are configurable, and half-open probing restores service without a
restart. Clients could ignore `Retry-After` and retry immediately.
Mitigation: the gate and breaker keep the engine reachable at a
bounded rate even while retries arrive.

## References

- [ADR 0019](0019-proxy-plugin-system.md): the per-key limiter
  ships as a plugin in the interceptor chain
- [Configuration reference](../reference/configuration.md): where
  the settings and the status-code table are documented
