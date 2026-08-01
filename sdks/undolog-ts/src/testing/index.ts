/** Test helpers for the UndoLog TypeScript SDK.
 *
 * Provides a fully in-memory mock server that mimics the UndoLog proxy MCP
 * protocol (``POST /mcp/tool_call``, effect commit/fail, and approval
 * endpoints), parity assertion helpers for cross-language signature testing,
 * and factory functions for creating session and effect test doubles.
 *
 * Import from ``@undolog/sdk/testing``.
 *
 * @example
 * ```ts
 * import { mockServer } from "@undolog/sdk/testing";
 * import { UndoLogClient, ToolTier } from "@undolog/sdk";
 *
 * const server = mockServer({
 *   tools: { send_email: ToolTier.Compensable },
 * });
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
import type { EffectRecord, EffectStatus, SessionRecord } from "../client.js";
import { callSignature, canonicalJson } from "../signature.js";
import { UndoLogSession } from "../session.js";
import type { SessionOptions } from "../session.js";
import { ToolTier } from "../tier.js";
import { NotFoundError, ValidationError } from "../errors.js";

// ── In-memory mock server ────────────────────────────────────────────────

/** Options for constructing a ``MockUndoLogServer``.
 *
 * The mock resolves a tool's tier from ``tools``, mirroring the real proxy
 * which resolves tiers server-side from its tool registry. The client does
 * not send a tier in the ``/mcp/tool_call`` request body.
 */
export interface MockServerOptions {
  /** Tool tier registry: maps tool name to its tier. Unknown tools fall back
   * to ``defaultTier``.
   */
  tools?: Record<string, ToolTier>;
  /** Tier applied to tools not present in ``tools``. Defaults to
   * ``ToolTier.Compensable``.
   */
  defaultTier?: ToolTier;
}

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
  /** Tool tier classification. */
  tier: ToolTier;
  /** Compensation descriptor, if provided at intercept time. */
  compensation: Record<string, unknown> | undefined;
  /** Error message set when the effect was failed. */
  error: string | undefined;
  /** Human-readable rejection reason, if provided. */
  reason: string | undefined;
  /** RFC 3339 timestamp of creation. */
  createdAt: string;
}

/** Internal representation of a pending approval in the mock server. */
export interface MockApprovalEntry {
  /** Approval identifier returned to the client. */
  approvalId: string;
  /** Identifier of the effect awaiting approval. */
  effectId: string;
  /** Human-readable rejection reason, if the approval was rejected. */
  reason: string | undefined;
  /** RFC 3339 timestamp of creation. */
  createdAt: string;
}

/** A fully in-memory mock of the UndoLog proxy API server.
 *
 * Emulates the proxy MCP protocol that ``UndoLogClient`` speaks:
 *
 * - ``POST /mcp/tool_call`` with a snake_case body
 *   ``{session_id, tool_name, tool_version, step_index, args}`` returns the
 *   proxy response ``{status: "executed"|"pending_approval", effect_id,
 *   approval_id?, retry_after?}``.
 * - ``PUT /effects/{id}/commit`` and ``PUT /effects/{id}/fail`` transition
 *   a stored effect.
 * - ``POST /approvals/{id}/approve`` and ``POST /approvals/{id}/reject``
 *   resolve a pending approval.
 * - ``GET /v1/effects/{id}`` and ``GET /v1/sessions/{id}`` return stored
 *   records.
 *
 * Stores effects in a ``Map`` keyed by effect identifier. Exposes an
 * ``HttpClient`` that can be injected directly into ``UndoLogClient``,
 * bypassing real HTTP. Use the public ``effects`` and ``approvals`` maps to
 * inspect or assert on the state of server-side data during tests.
 *
 * @example
 * ```ts
 * const server = mockServer({
 *   tools: { send_email: ToolTier.Compensable },
 * });
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost",
 *   httpClient: server.httpClient,
 * });
 * await client.intercept({ toolName: "send_email", args: {}, tier: ToolTier.Compensable });
 * expect(server.effects.size).toBe(1);
 * ```
 */
export class MockUndoLogServer {
  /** All effects stored in the mock server, keyed by effect ID. */
  readonly effects: Map<string, MockEffectEntry> = new Map();

  /** All approvals created by the mock server, keyed by approval ID. */
  readonly approvals: Map<string, MockApprovalEntry> = new Map();

  /** Tool tier registry and default tier for unknown tools. */
  readonly options: Required<Pick<MockServerOptions, "defaultTier">> & MockServerOptions;

  /** An ``HttpClient`` that routes requests to this mock server instance.
   *
   * Pass this as the ``httpClient`` option when constructing an
   * ``UndoLogClient`` to replace real HTTP calls with in-memory operations.
   */
  get httpClient(): HttpClient {
    return { request: (opts) => this.#handleRequest(opts) };
  }

  /**
   * @param options - Tool tier registry and default tier.
   */
  constructor(options: MockServerOptions = {}) {
    this.options = { defaultTier: ToolTier.Compensable, ...options };
  }

  async #handleRequest<T>(opts: RequestOptions): Promise<T> {
    const body = (opts.body ?? {}) as Record<string, unknown>;
    const method = opts.method ?? "GET";
    const path = opts.path;

    if (path === "/mcp/tool_call") {
      return this.#toolCall(body, method) as T;
    }

    const commitMatch = /^\/effects\/([^/]+)\/commit$/.exec(path);
    const commitId = commitMatch?.[1];
    if (commitId !== undefined) {
      return this.#commit(commitId, method) as T;
    }

    const failMatch = /^\/effects\/([^/]+)\/fail$/.exec(path);
    const failId = failMatch?.[1];
    if (failId !== undefined) {
      return this.#fail(failId, body, method) as T;
    }

    const approveMatch = /^\/approvals\/([^/]+)\/approve$/.exec(path);
    const approveId = approveMatch?.[1];
    if (approveId !== undefined) {
      return this.#approve(approveId, method) as T;
    }

    const rejectMatch = /^\/approvals\/([^/]+)\/reject$/.exec(path);
    const rejectId = rejectMatch?.[1];
    if (rejectId !== undefined) {
      return this.#reject(rejectId, body, method) as T;
    }

    const effectMatch = /^\/v1\/effects\/([^/]+)$/.exec(path);
    const effectId = effectMatch?.[1];
    if (effectId !== undefined) {
      return this.#getEffect(effectId, method) as T;
    }

    const sessionMatch = /^\/v1\/sessions\/([^/]+)$/.exec(path);
    const sessionId = sessionMatch?.[1];
    if (sessionId !== undefined) {
      return this.#getSession(sessionId, method) as T;
    }

    throw new TypeError(`Mock server: unknown path "${path}"`);
  }

  #toolCall(body: Record<string, unknown>, method: string): Record<string, unknown> {
    this.#assertMethod(method, "POST", "/mcp/tool_call");
    const sessionId = body.session_id as string;
    const toolName = body.tool_name as string;
    const stepIndex = body.step_index as number;
    const args = (body.args ?? {}) as Record<string, unknown>;

    if (typeof sessionId !== "string" || sessionId === "") {
      throw new ValidationError("Mock server: session_id is required", "session_id");
    }
    if (typeof toolName !== "string" || toolName === "") {
      throw new ValidationError("Mock server: tool_name is required", "tool_name");
    }

    const tier = this.#resolveTier(toolName);
    const effectId = randomUUID();
    const now = new Date().toISOString();
    const computedSignature = callSignature(sessionId, stepIndex, toolName, args);

    if (tier === ToolTier.Irreversible) {
      const entry: MockEffectEntry = {
        effectId,
        sessionId,
        stepIndex,
        toolName,
        args,
        signature: computedSignature,
        status: "pending",
        tier,
        compensation: undefined,
        error: undefined,
        reason: undefined,
        createdAt: now,
      };
      this.effects.set(effectId, entry);

      const approvalId = randomUUID();
      this.approvals.set(approvalId, {
        approvalId,
        effectId,
        reason: undefined,
        createdAt: now,
      });

      return { status: "pending_approval", approval_id: approvalId, retry_after: 5 };
    }

    // Safe and Compensable tools execute inline and commit server-side, so the
    // stored effect reflects the committed state the proxy would persist.
    const entry: MockEffectEntry = {
      effectId,
      sessionId,
      stepIndex,
      toolName,
      args,
      signature: computedSignature,
      status: "committed",
      tier,
      compensation: undefined,
      error: undefined,
      reason: undefined,
      createdAt: now,
    };
    this.effects.set(effectId, entry);
    return { status: "executed", effect_id: effectId, result: null };
  }

  #commit(effectId: string, method: string): EffectRecord {
    this.#assertMethod(method, "PUT", `/effects/${effectId}/commit`);
    const entry = this.#requireEffect(effectId, "/effects/commit");
    entry.status = "committed";
    return this.#toRecord(entry);
  }

  #fail(effectId: string, body: Record<string, unknown>, method: string): EffectRecord {
    this.#assertMethod(method, "PUT", `/effects/${effectId}/fail`);
    const entry = this.#requireEffect(effectId, "/effects/fail");
    entry.status = "failed";
    entry.error = (body.error as string) ?? undefined;
    return this.#toRecord(entry);
  }

  #approve(approvalId: string, method: string): EffectRecord {
    this.#assertMethod(method, "POST", `/approvals/${approvalId}/approve`);
    const approval = this.approvals.get(approvalId);
    if (approval === undefined) {
      throw new NotFoundError("approval", approvalId);
    }
    const entry = this.#requireEffect(approval.effectId, "/approvals/approve");
    entry.status = "approved";
    return this.#toRecord(entry);
  }

  #reject(approvalId: string, body: Record<string, unknown>, method: string): EffectRecord {
    this.#assertMethod(method, "POST", `/approvals/${approvalId}/reject`);
    const approval = this.approvals.get(approvalId);
    if (approval === undefined) {
      throw new NotFoundError("approval", approvalId);
    }
    const entry = this.#requireEffect(approval.effectId, "/approvals/reject");
    entry.status = "rejected";
    entry.reason = (body.reason as string) ?? undefined;
    return this.#toRecord(entry);
  }

  #getEffect(effectId: string, method: string): EffectRecord {
    this.#assertMethod(method, "GET", `/v1/effects/${effectId}`);
    return this.#toRecord(this.#requireEffect(effectId, "/v1/effects"));
  }

  #getSession(sessionId: string, method: string): SessionRecord {
    this.#assertMethod(method, "GET", `/v1/sessions/${sessionId}`);
    const now = new Date().toISOString();
    return {
      sessionId,
      stepCount: 0,
      createdAt: now,
      metadata: {},
    };
  }

  #resolveTier(toolName: string): ToolTier {
    return this.options.tools?.[toolName] ?? this.options.defaultTier;
  }

  #requireEffect(effectId: string, endpoint: string): MockEffectEntry {
    const entry = this.effects.get(effectId);
    if (entry === undefined) {
      throw new NotFoundError(
        "effect",
        effectId,
        `Mock server: effect "${effectId}" not found via ${endpoint}`,
      );
    }
    return entry;
  }

  #assertMethod(actual: string, expected: string, path: string): void {
    if (actual !== expected) {
      throw new TypeError(`Mock server: ${path} requires ${expected}, got ${actual}`);
    }
  }

  #toRecord(entry: MockEffectEntry): EffectRecord {
    return {
      effectId: entry.effectId,
      sessionId: entry.sessionId,
      stepIndex: entry.stepIndex,
      toolName: entry.toolName,
      signature: entry.signature,
      status: entry.status,
      tier: entry.tier,
      createdAt: entry.createdAt,
    };
  }

  /** Remove all effects and approvals from the mock server. */
  clear(): void {
    this.effects.clear();
    this.approvals.clear();
  }
}

/** Create a new ``MockUndoLogServer`` instance.
 *
 * @param options - Tool tier registry and default tier.
 * @returns A mock server ready to handle UndoLog proxy API requests.
 *
 * @example
 * ```ts
 * const server = mockServer({
 *   tools: { send_email: ToolTier.Compensable },
 * });
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost",
 *   httpClient: server.httpClient,
 * });
 * ```
 */
export function mockServer(options?: MockServerOptions): MockUndoLogServer {
  return new MockUndoLogServer(options);
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
