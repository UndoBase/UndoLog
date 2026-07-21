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
import { ToolTier, requiresApproval } from "./tier.js";
import type { CompensationDescriptor } from "./tier.js";
import { AwaitingApprovalError } from "./errors.js";

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
   * Computes the BLAKE3 call signature over (session, step, name, args) and
   * persists the effect on the server. For ``Irreversible`` tier tools, the
   * effect is created in pending state and an ``AwaitingApprovalError`` is
   * thrown so the caller can suspend execution until a human approves or
   * rejects the request.
   *
   * @param params - Tool name, arguments, tier, and optional session or
   *   compensation metadata.
   * @returns The server-created effect record.
   * @throws {AwaitingApprovalError} If the tool tier is Irreversible. The
   *   effect is persisted on the server before the error is thrown.
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
    const stepIndex = params.stepIndex ?? session?.stepIndex ?? 0;

    if (params.stepIndex === undefined && session !== undefined) {
      session.nextStep();
    }

    const signature = callSignature(sessionId, stepIndex, params.toolName, params.args);

    const body: Record<string, unknown> = {
      sessionId,
      stepIndex,
      toolName: params.toolName,
      args: params.args,
      tier: params.tier,
      signature,
    };

    if (params.compensation !== undefined) {
      body.compensation = params.compensation;
    }

    const record = await this.#http.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/intercept",
      body,
    });

    if (requiresApproval(params.tier)) {
      throw new AwaitingApprovalError(params.toolName, params.args, record.effectId);
    }

    return record;
  }

  /**
   * Mark a previously intercepted effect as successfully committed.
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
    return this.#http.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/commit",
      body: { effectId },
    });
  }

  /**
   * Mark a previously intercepted effect as failed.
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
    return this.#http.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/fail",
      body: { effectId, error: errorMessage },
    });
  }

  /**
   * Approve a pending irreversible effect for execution.
   *
   * Called by an external approval system after a human reviews and authorises
   * an effect that was previously intercepted with ``Irreversible`` tier.
   *
   * @param approvalId - Approval identifier from the original
   *   ``AwaitingApprovalError`` (same as the effect identifier).
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
      path: "/v1/effects/approve",
      body: { approvalId },
    });
  }

  /**
   * Reject a pending irreversible effect.
   *
   * Called by an external approval system after a human declines to authorise
   * an effect that was previously intercepted with ``Irreversible`` tier.
   *
   * @param approvalId - Approval identifier from the original
   *   ``AwaitingApprovalError`` (same as the effect identifier).
   * @param reason - Optional human-readable justification for the rejection.
   * @returns Updated effect record reflecting rejected status.
   *
   * @example
   * ```ts
   * await client.reject(approvalId, "User declined the operation");
   * ```
   */
  async reject(approvalId: string, reason?: string): Promise<EffectRecord> {
    const body: Record<string, unknown> = { approvalId };
    if (reason !== undefined) {
      body.reason = reason;
    }
    return this.#http.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/reject",
      body,
    });
  }
}
