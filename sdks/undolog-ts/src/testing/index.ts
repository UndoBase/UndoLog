/** Test helpers for the UndoLog TypeScript SDK.
 *
 * Provides a fully in-memory mock server that mimics the UndoLog HTTP API,
 * parity assertion helpers for cross-language signature testing, and factory
 * functions for creating session and effect test doubles.
 *
 * Import from ``@undolog/sdk/testing``.
 *
 * @example
 * ```ts
 * import { mockServer, createMockEffect } from "@undolog/sdk/testing";
 * import { UndoLogClient, ToolTier } from "@undolog/sdk";
 *
 * const server = mockServer();
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost",
 *   httpClient: server.httpClient,
 * });
 * ```
 *
 * @module
 */

import { randomUUID } from "node:crypto";
import type { HttpClient, RequestOptions } from "../http.js";
import type { EffectRecord, EffectStatus } from "../client.js";
import { callSignature, canonicalJson } from "../signature.js";
import { UndoLogSession } from "../session.js";
import type { SessionOptions } from "../session.js";
import { ToolTier } from "../tier.js";

// ── In-memory mock server ────────────────────────────────────────────────

/** Internal representation of an effect tracked by the mock server.
 *
 * Stores the complete request data plus the computed signature and current
 * lifecycle status. The ``effects`` map on ``MockUndoLogServer`` exposes
 * these entries for test assertions.
 */
export interface MockEffectEntry {
  /** Server-assigned effect identifier. */
  effectId: string;
  /** UUID of the session this effect belongs to. */
  sessionId: string;
  /** Step index within the session. */
  stepIndex: number;
  /** Tool name as provided at intercept time. */
  toolName: string;
  /** Tool arguments as provided at intercept time. */
  args: Record<string, unknown>;
  /** BLAKE3 signature of (sessionId, stepIndex, toolName, args). */
  signature: string;
  /** Current lifecycle status. */
  status: EffectStatus;
  /** Tool tier classification string. */
  tier: string;
  /** Compensation descriptor, if provided at intercept time. */
  compensation: Record<string, unknown> | undefined;
  /** Error message set when the effect was failed. */
  error: string | undefined;
  /** Human-readable rejection reason, if provided. */
  reason: string | undefined;
  /** RFC 3339 timestamp of creation. */
  createdAt: string;
}

/** A fully in-memory mock of the UndoLog API server.
 *
 * Stores effects in a ``Map`` keyed by effect identifier. Exposes an
 * ``HttpClient`` that can be injected directly into ``UndoLogClient``,
 * bypassing real HTTP. Use the public ``effects`` map to inspect or assert
 * on the state of server-side data during tests.
 *
 * @example
 * ```ts
 * const server = mockServer();
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost",
 *   httpClient: server.httpClient,
 * });
 * await client.intercept({ toolName: "t", args: {}, tier: ToolTier.Safe });
 * expect(server.effects.size).toBe(1);
 * ```
 */
export class MockUndoLogServer {
  /** All effects stored in the mock server, keyed by effect ID. */
  readonly effects: Map<string, MockEffectEntry> = new Map();

  /** An ``HttpClient`` that routes requests to this mock server instance.
   *
   * Pass this as the ``httpClient`` option when constructing an
   * ``UndoLogClient`` to replace real HTTP calls with in-memory operations.
   */
  get httpClient(): HttpClient {
    return { request: (opts) => this.#handleRequest(opts) };
  }

  async #handleRequest<T>(opts: RequestOptions): Promise<T> {
    const body = (opts.body ?? {}) as Record<string, unknown>;
    switch (opts.path) {
      case "/v1/effects/intercept":
        return this.#intercept(body) as T;
      case "/v1/effects/commit":
        return this.#commit(body) as T;
      case "/v1/effects/fail":
        return this.#fail(body) as T;
      case "/v1/effects/approve":
        return this.#approve(body) as T;
      case "/v1/effects/reject":
        return this.#reject(body) as T;
      default:
        throw new TypeError(`Mock server: unknown path "${opts.path}"`);
    }
  }

  #intercept(body: Record<string, unknown>): EffectRecord {
    const sessionId = body.sessionId as string;
    const stepIndex = body.stepIndex as number;
    const toolName = body.toolName as string;
    const args = (body.args ?? {}) as Record<string, unknown>;
    const tier = body.tier as string;
    const compensation = body.compensation as Record<string, unknown> | undefined;

    if (!Object.values(ToolTier).includes(tier as ToolTier)) {
      throw new TypeError(`Invalid tier: "${tier}"`);
    }

    const effectId = randomUUID();
    const now = new Date().toISOString();
    const computedSignature = callSignature(sessionId, stepIndex, toolName, args);

    const entry: MockEffectEntry = {
      effectId,
      sessionId,
      stepIndex,
      toolName,
      args,
      signature: computedSignature,
      status: "pending",
      tier,
      compensation,
      error: undefined,
      reason: undefined,
      createdAt: now,
    };
    this.effects.set(effectId, entry);
    return this.#toRecord(entry);
  }

  #commit(body: Record<string, unknown>): EffectRecord {
    const effectId = body.effectId as string;
    const entry = this.effects.get(effectId);
    if (entry === undefined) {
      throw new TypeError(`Mock server: effect "${effectId}" not found`);
    }
    entry.status = "committed";
    return this.#toRecord(entry);
  }

  #fail(body: Record<string, unknown>): EffectRecord {
    const effectId = body.effectId as string;
    const entry = this.effects.get(effectId);
    if (entry === undefined) {
      throw new TypeError(`Mock server: effect "${effectId}" not found`);
    }
    entry.status = "failed";
    entry.error = (body.error as string) ?? undefined;
    return this.#toRecord(entry);
  }

  #approve(body: Record<string, unknown>): EffectRecord {
    const approvalId = body.approvalId as string;
    const entry = this.effects.get(approvalId);
    if (entry === undefined) {
      throw new TypeError(`Mock server: effect "${approvalId}" not found`);
    }
    entry.status = "approved";
    return this.#toRecord(entry);
  }

  #reject(body: Record<string, unknown>): EffectRecord {
    const approvalId = body.approvalId as string;
    const entry = this.effects.get(approvalId);
    if (entry === undefined) {
      throw new TypeError(`Mock server: effect "${approvalId}" not found`);
    }
    entry.status = "rejected";
    entry.reason = (body.reason as string) ?? undefined;
    return this.#toRecord(entry);
  }

  #toRecord(entry: MockEffectEntry): EffectRecord {
    return {
      effectId: entry.effectId,
      sessionId: entry.sessionId,
      stepIndex: entry.stepIndex,
      toolName: entry.toolName,
      signature: entry.signature,
      status: entry.status,
      tier: entry.tier as ToolTier,
      createdAt: entry.createdAt,
    };
  }

  /** Remove all effects from the mock server. */
  clear(): void {
    this.effects.clear();
  }
}

/** Create a new ``MockUndoLogServer`` instance.
 *
 * @returns A mock server ready to handle UndoLog API requests.
 *
 * @example
 * ```ts
 * const server = mockServer();
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost",
 *   httpClient: server.httpClient,
 * });
 * ```
 */
export function mockServer(): MockUndoLogServer {
  return new MockUndoLogServer();
}

// ── Constant-time comparison ────────────────────────────────────────────

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

// ── Parity assertion helpers ─────────────────────────────────────────────

/** Result of a parity assertion.
 *
 * Designed to be compatible with vitest and Jest custom matchers. Use
 * ``expect(result.pass).toBe(true)`` or integrate with ``expect.extend()``.
 */
export interface ParityResult {
  /** Whether the assertion passed. */
  pass: boolean;
  /** Human-readable description of the result.
   *
   * @returns Explanation of why the assertion passed or failed.
   */
  message(): string;
}

/** Verify that two values produce identical canonical JSON output.
 *
 * Useful for cross-language parity tests where the same input must produce
 * the same canonical JSON regardless of the language runtime. Each value
 * is canonicalised internally before comparison.
 *
 * @param actual - Raw value produced by the local implementation.
 * @param expected - Raw value produced by the reference implementation.
 * @returns A result object with ``pass`` and a descriptive ``message``.
 *
 * @example
 * ```ts
 * const result = assertCanonicalJsonParity(
 *   { b: 1, a: 2 },
 *   { a: 2, b: 1 },
 * );
 * expect(result.pass).toBe(true);
 * ```
 */
export function assertCanonicalJsonParity(
  actual: unknown,
  expected: unknown,
): ParityResult {
  const actualJson = canonicalJson(actual);
  const expectedJson = canonicalJson(expected);
  const pass = actualJson === expectedJson;
  return {
    pass,
    message: () =>
      pass
        ? "Canonical JSON matches expected output"
        : `Canonical JSON mismatch:\nExpected: ${expectedJson}\nActual:   ${actualJson}`,
  };
}

/** Verify that a computed call signature matches an expected value.
 *
 * Computes the BLAKE3 call signature from the provided inputs and compares
 * it against an expected 64-character hex string. Useful for cross-language
 * signature parity tests where the expected value was generated by the
 * Python or Rust SDK.
 *
 * @param sessionId - UUID string identifying the session.
 * @param stepIndex - Monotonically increasing step counter.
 * @param toolName - Logical name of the tool.
 * @param args - Tool arguments (canonicalised internally before hashing).
 * @param expectedSignature - Expected 64-character lowercase hex signature.
 * @returns A result object with ``pass`` and a descriptive ``message``.
 *
 * @example
 * ```ts
 * const result = assertSignatureParity(
 *   "550e8400-e29b-41d4-a716-446655440000",
 *   1,
 *   "send_email",
 *   { to: "alice@example.com", subject: "Hello" },
 *   "8f20ad25773b270753b417b05437f5644997cb43e70a11a9e3b4e6d9a9d32546",
 * );
 * expect(result.pass).toBe(true);
 * ```
 */
export function assertSignatureParity(
  sessionId: string,
  stepIndex: number,
  toolName: string,
  args: unknown,
  expectedSignature: string,
): ParityResult {
  const actual = callSignature(sessionId, stepIndex, toolName, args);
  const pass = constantTimeEqual(actual, expectedSignature);
  return {
    pass,
    message: () =>
      pass
        ? "Signature matches expected value"
        : `Signature mismatch:\nExpected: ${expectedSignature}\nActual:   ${actual}`,
  };
}

// ── Factory helpers ──────────────────────────────────────────────────────

/** Create an ``UndoLogSession`` with a predictable default session ID.
 *
 * Defaults the ``sessionId`` to ``00000000-0000-0000-0000-000000000000`` so
 * that tests produce reproducible signatures. Pass ``options`` to override
 * any field.
 *
 * @param options - Optional overrides (sessionId, metadata).
 * @returns A new ``UndoLogSession`` instance.
 *
 * @example
 * ```ts
 * const session = createMockSession();
 * expect(session.sessionId).toBe("00000000-0000-0000-0000-000000000000");
 * ```
 */
export function createMockSession(options?: SessionOptions): UndoLogSession {
  return new UndoLogSession({
    sessionId: "00000000-0000-0000-0000-000000000000",
    ...options,
  });
}

/** Create an ``EffectRecord`` with predictable defaults for testing.
 *
 * Every field has a sensible default so tests only need to override the
 * fields relevant to the behaviour under test.
 *
 * @param overrides - Partial ``EffectRecord`` fields to override.
 * @returns A complete ``EffectRecord`` with defaults filled in.
 *
 * @example
 * ```ts
 * const effect = createMockEffect({ status: "committed" });
 * expect(effect.status).toBe("committed");
 * expect(effect.toolName).toBe("test_tool");
 * ```
 */
export function createMockEffect(overrides?: Partial<EffectRecord>): EffectRecord {
  return {
    effectId: "00000000-0000-0000-0000-000000000001",
    sessionId: "00000000-0000-0000-0000-000000000000",
    stepIndex: 0,
    toolName: "test_tool",
    signature: "0000000000000000000000000000000000000000000000000000000000000000",
    status: "pending",
    tier: ToolTier.Safe,
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}
