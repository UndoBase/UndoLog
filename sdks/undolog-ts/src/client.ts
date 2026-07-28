/** Main client for the UndoLog effect-tracking API.
 *
 * Provides the UndoLogClient class with intercept, commit, fail, approve,
 * and reject methods for managing tool effect lifecycle.
 *
 * @module
 */

import { randomUUID } from "node:crypto";
import { createHttpClient } from "./http.js";
import type { HttpClient } from "./http.js";
import { getCurrentSession } from "./session.js";
import { callSignature } from "./signature.js";
import type { ToolTier } from "./tier.js";
import type { CompensationDescriptor } from "./tier.js";
import { AwaitingApprovalError, NotFoundError } from "./errors.js";

/** Lifecycle status of an effect in the UndoLog. */
export type EffectStatus = "pending" | "approved" | "rejected" | "committed" | "failed";

/** Record returned by the UndoLog server for an effect. */
export interface EffectRecord {
  /** Server-assigned effect identifier. */
  effectId: string;
  /** UUID of the session this effect belongs to. */
  sessionId: string;
  /** Step index within the session. */
  stepIndex: number;
  /** Tool name as provided at intercept time. */
  toolName: string;
  /** BLAKE3 signature of (sessionId, stepIndex, toolName, args). */
  signature: string;
  /** Current lifecycle status. */
  status: EffectStatus;
  /** Tool tier classification. */
  tier: ToolTier;
  /** RFC 3339 timestamp of creation. */
  createdAt: string;
}

/** Record returned by the UndoLog server for a session query. */
export interface SessionRecord {
  /** Session UUID. */
  sessionId: string;
  /** Current step count. */
  stepCount: number;
  /** RFC 3339 timestamp of session creation. */
  createdAt: string;
  /** Arbitrary metadata attached at creation time. */
  metadata: Record<string, unknown>;
}

/** Options for constructing an UndoLogClient. */
export interface UndoLogClientOptions {
  /** Base URL of the UndoLog API server. */
  baseUrl: string;
  /** API key sent as an ``X-Api-Key`` header on every request. */
  apiKey?: string;
  /** Per-request timeout in milliseconds. Defaults to 30000. */
  timeout?: number;
  /** Maximum retry attempts for retryable statuses. Defaults to 3. */
  maxRetries?: number;
  /** Additional headers included on every request. */
  headers?: Record<string, string>;
  /** Pre-configured HttpClient instance. Overrides other HTTP options. */
  httpClient?: HttpClient;
}

/** Parameters for ``UndoLogClient.intercept()``. */
export interface InterceptParams {
  /** Logical tool name. */
  toolName: string;
  /** Arguments passed to the tool. */
  args: Record<string, unknown>;
  /** Tool tier classification. */
  tier: ToolTier;
  /** Compensation descriptor for Compensable tier tools. */
  compensation?: CompensationDescriptor;
  /** Explicit session UUID. Uses the active async context session if omitted. */
  sessionId?: string;
  /** Explicit step index. Reads from the session counter if omitted. */
  stepIndex?: number;
}

/** Main client for the UndoLog effect-tracking API.
 *
 * Create an instance with ``new UndoLogClient(options)`` and use its methods
 * to intercept, commit, fail, approve, and reject tool effects. Every mutating
 * request includes an auto-generated idempotency key for exactly-once
 * semantics.
 *
 * @example
 * ```ts
 * const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
 * const effect = await client.intercept({
 *   toolName: "send_email",
 *   args: { to: "user@example.com" },
 *   tier: ToolTier.Compensable,
 * });
 * await client.commit(effect.effectId);
 * ```
 */
export class UndoLogClient {
  readonly #http: HttpClient;

  /**
   * @param options - Server URL, credentials, and HTTP behaviour.
   */
  constructor(options: UndoLogClientOptions) {
    this.#http =
      options.httpClient ??
      createHttpClient({
        baseUrl: options.baseUrl,
        apiKey: options.apiKey,
        timeout: options.timeout,
        maxRetries: options.maxRetries,
        headers: options.headers,
      });
  }

  /**
   * Register an effect before tool execution.
   *
   * For calls routed through the UndoLog proxy, this sends a
   * ``POST /mcp/tool_call`` request. The proxy intercepts the effect,
   * executes the upstream tool, and commits inline. The returned
   * ``EffectRecord`` reflects the server-side state.
   *
   * @param params - Tool name, arguments, tier, and optional session
   *   metadata.
   * @returns The server-created effect record.
   * @throws {AwaitingApprovalError} If the proxy indicates the tool
   *   requires human approval.
   *
   * @example
   * ```ts
   * const effect = await client.intercept({
   *   toolName: "send_email",
   *   args: { to: "user@example.com" },
   *   tier: ToolTier.Compensable,
   * });
   * ```
   */
  async intercept(params: InterceptParams): Promise<EffectRecord> {
    const session = getCurrentSession();
    const sessionId = params.sessionId ?? session?.sessionId ?? randomUUID();

    let stepIndex: number;
    if (params.stepIndex !== undefined) {
      stepIndex = params.stepIndex;
    } else if (session !== undefined) {
      stepIndex = session.claimStepIndex();
    } else {
      stepIndex = 0;
    }

    const signature = callSignature(sessionId, stepIndex, params.toolName, params.args);

    const body: Record<string, unknown> = {
      session_id: sessionId,
      tool_name: params.toolName,
      tool_version: "1.0.0",
      step_index: stepIndex,
      args: params.args,
    };

    const proxyResp = await this.#http.request<Record<string, unknown>>({
      method: "POST",
      path: "/mcp/tool_call",
      body,
    });

    const proxyStatus = proxyResp.status as string | undefined;

    if (proxyStatus === "pending_approval") {
      throw new AwaitingApprovalError(
        params.toolName,
        params.args,
        proxyResp.approval_id as string,
      );
    }

    const isReplay = proxyStatus === "replayed";

    return {
      effectId: proxyResp.effect_id as string ?? randomUUID(),
      sessionId,
      stepIndex,
      toolName: params.toolName,
      signature,
      status: isReplay ? "committed" : "pending",
      tier: params.tier,
      createdAt: new Date().toISOString(),
    };
  }

  /**
   * Mark a previously intercepted effect as successfully committed.
   *
   * For calls routed through ``POST /mcp/tool_call`` the proxy commits
   * inline and this method is a safe no-op. It sends a
   * ``PUT /effects/{effectId}/commit`` to the proxy; if the endpoint
   * is not implemented (404), the commit is assumed successful.
   *
   * @param effectId - Effect identifier returned by ``intercept()``.
   * @returns Updated effect record reflecting committed status.
   *
   * @example
   * ```ts
   * const effect = await client.intercept({ /* ... *\/ });
   * await client.commit(effect.effectId);
   * ```
   */
  async commit(effectId: string): Promise<EffectRecord> {
    try {
      return await this.#http.request<EffectRecord>({
        method: "PUT",
        path: `/effects/${encodeURIComponent(effectId)}/commit`,
        body: {},
      });
    } catch (err) {
      if (err instanceof NotFoundError) {
        return { effectId, status: "committed" } as EffectRecord;
      }
      throw err;
    }
  }

  /**
   * Mark a previously intercepted effect as failed.
   *
   * For calls routed through ``POST /mcp/tool_call`` the proxy handles
   * failures inline and this method is a safe no-op. It sends a
   * ``PUT /effects/{effectId}/fail`` to the proxy; if the endpoint
   * is not implemented (404), the failure is assumed recorded.
   *
   * @param effectId - Effect identifier returned by ``intercept()``.
   * @param errorMessage - Human-readable description of the failure.
   * @returns Updated effect record reflecting failed status.
   *
   * @example
   * ```ts
   * await client.fail(effect.effectId, "Tool threw: connection refused");
   * ```
   */
  async fail(effectId: string, errorMessage: string): Promise<EffectRecord> {
    try {
      return await this.#http.request<EffectRecord>({
        method: "PUT",
        path: `/effects/${encodeURIComponent(effectId)}/fail`,
        body: { error: errorMessage },
      });
    } catch (err) {
      if (err instanceof NotFoundError) {
        return { effectId, status: "failed" } as EffectRecord;
      }
      throw err;
    }
  }

  /**
   * Approve a pending irreversible effect for execution.
   *
   * @param approvalId - Approval identifier from the original
   *   ``AwaitingApprovalError``.
   * @returns Updated effect record reflecting approved status.
   *
   * @example
   * ```ts
   * await client.approve("550e8400-e29b-41d4-a716-446655440000");
   * ```
   */
  async approve(approvalId: string): Promise<EffectRecord> {
    return this.#http.request<EffectRecord>({
      method: "POST",
      path: `/approvals/${encodeURIComponent(approvalId)}/approve`,
      body: {},
    });
  }

  /**
   * Reject a pending irreversible effect.
   *
   * @param approvalId - Approval identifier from the original
   *   ``AwaitingApprovalError``.
   * @param reason - Optional human-readable justification for the rejection.
   * @returns Updated effect record reflecting rejected status.
   *
   * @example
   * ```ts
   * await client.reject(approvalId, "User declined the operation");
   * ```
   */
  async reject(approvalId: string, reason?: string): Promise<EffectRecord> {
    const body: Record<string, unknown> = {};
    if (reason !== undefined) {
      body.reason = reason;
    }
    return this.#http.request<EffectRecord>({
      method: "POST",
      path: `/approvals/${encodeURIComponent(approvalId)}/reject`,
      body,
    });
  }

  /**
   * Query the server for a specific effect record.
   *
   * @param effectId - Effect identifier returned by ``intercept()``.
   * @returns The effect record with current status.
   *
   * @example
   * ```ts
   * const effect = await client.getEffect(effectId);
   * ```
   */
  async getEffect(effectId: string): Promise<EffectRecord> {
    return this.#http.request<EffectRecord>({
      method: "GET",
      path: `/v1/effects/${encodeURIComponent(effectId)}`,
    });
  }

  /**
   * Query the server for a specific session record.
   *
   * @param sessionId - Session UUID.
   * @returns The session record with metadata and step count.
   *
   * @example
   * ```ts
   * const session = await client.getSession(sessionId);
   * ```
   */
  async getSession(sessionId: string): Promise<SessionRecord> {
    return this.#http.request<SessionRecord>({
      method: "GET",
      path: `/v1/sessions/${encodeURIComponent(sessionId)}`,
    });
  }
}
