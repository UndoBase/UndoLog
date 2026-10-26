/** Decorators for wrapping tool functions with UndoLog effect tracking.
 *
 * Provides the ``wrapTool()`` higher-order function that decorates a tool with
 * the complete effect lifecycle: intercept, execute, commit on success, and
 * fail on error.
 *
 * @module
 */

import type { UndoLogClient } from "./client.js";
import { MissingSessionError } from "./errors.js";
import { getCurrentSession, UndoLogSession } from "./session.js";
import type { ToolTier, CompensationDescriptor } from "./tier.js";
import { isSafe, isCompensable } from "./tier.js";

/** Describes a tool function and its UndoLog metadata.
 *
 * Pass a ``ToolDefinition`` to ``wrapTool()`` to produce an effect-tracked
 * wrapper around the raw function.
 *
 * @typeParam TArgs - Shape of the tool's arguments object.
 * @typeParam TResult - Return type of the tool function.
 */
export interface ToolDefinition<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  /** Unique tool name used for effect registration. */
  readonly name: string;

  /** Human-readable description of what the tool does. */
  readonly description?: string;

  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** The actual tool implementation. */
  readonly fn: (args: TArgs, context?: unknown) => TResult | Promise<TResult>;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;

  /** Explicit session ID to associate with this tool's executions. */
  readonly sessionId?: string;

  /**
   * Whether to require an active session context.
   * If true, throws MissingSessionError when invoked without an active session context.
   */
  readonly requireSession?: boolean;
}

/** Options for configuring wrapTool behaviour. */
export interface WrapToolOptions {
  /** Explicit session ID to associate with this tool's executions. */
  readonly sessionId?: string;

  /**
   * Whether to require an active session context.
   * If true, throws MissingSessionError when invoked without an active session context.
   */
  readonly requireSession?: boolean;
}

/** An async function that has been wrapped with UndoLog effect tracking.
 *
 * @typeParam TArgs - Shape of the tool's arguments object.
 * @typeParam TResult - Return type of the tool function.
 */
export type WrappedTool<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> = (args: TArgs, context?: unknown) => Promise<TResult>;

/** Wraps a tool function with UndoLog effect tracking.
 *
 * The wrapper orchestrates the complete tool execution lifecycle:
 *
 * **SAFE bypass** -- For tools classified as ``ToolTier.Safe``, the function
 * is called directly without effect registration. No intercept, commit, or
 * fail overhead.
 *
 * **Execute** -- For Compensable and Irreversible tools, the wrapper calls
 * ``client.intercept()`` before execution. On success it commits the effect;
 * on failure it records the failure via ``client.fail()`` and re-throws the
 * original error.
 *
 * **Replay** -- If the server returns an effect record with status
 * ``"committed"`` (indicating this exact call was already recorded and
 * committed in a previous attempt), the wrapper still re-executes the tool
 * function to produce a fresh result but skips the redundant commit call.
 * If the re-execution fails, the fail callback is also skipped because the
 * effect was already committed.
 *
 * **AwaitingApproval** -- For Irreversible-tier tools,
 * ``client.intercept()`` throws ``AwaitingApprovalError`` before the tool
 * executes. The wrapper lets this error propagate so the caller can pause
 * execution and wait for human approval.
 *
 * @typeParam TArgs - Shape of the tool's arguments object.
 * @typeParam TResult - Return type of the tool function.
 * @param client - Configured UndoLog client instance.
 * @param definition - Tool metadata and implementation.
 * @returns An async function with the same call signature as the original
 *   tool.
 *
 * @example
 * ```ts
 * const sendEmail = wrapTool(client, {
 *   name: "send_email",
 *   tier: ToolTier.Compensable,
 *   fn: async ({ to, subject }) => ({ sent: true }),
 *   compensation: { fnName: "recall_email" },
 * });
 * const result = await sendEmail({ to: "a@b.com", subject: "Hi" });
 * ```
 */
function extractSessionId(context: unknown): string | undefined {
  if (
    typeof context === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(context)
  ) {
    return context;
  }
  if (context && typeof context === "object") {
    const record = context as Record<string, unknown>;
    if (
      typeof record.sessionId === "string" &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(record.sessionId)
    ) {
      return record.sessionId;
    }
    if (record.session instanceof UndoLogSession) {
      return record.session.sessionId;
    }
  }
  return undefined;
}

function extractSession(context: unknown): UndoLogSession | undefined {
  if (context instanceof UndoLogSession) {
    return context;
  }
  if (context && typeof context === "object") {
    const record = context as Record<string, unknown>;
    if (record.session instanceof UndoLogSession) {
      return record.session;
    }
  }
  return undefined;
}

export function wrapTool<
  TArgs extends Record<string, unknown>,
  TResult,
>(
  client: UndoLogClient,
  definition: ToolDefinition<TArgs, TResult>,
  options?: WrapToolOptions,
): WrappedTool<TArgs, TResult> {
  const { name, tier, fn, compensation } = definition;

  return async (args: TArgs, context?: unknown): Promise<TResult> => {
    if (isSafe(tier)) {
      return fn(args, context);
    }

    const session = getCurrentSession() ?? extractSession(context);
    const explicitSessionId =
      extractSessionId(context) ?? options?.sessionId ?? definition.sessionId;

    if (
      (definition.requireSession || options?.requireSession) &&
      !session &&
      !explicitSessionId
    ) {
      throw new MissingSessionError(
        `Tool "${name}" executed without an active session context: wrap your call in runWithSession() or provide an explicit sessionId.`,
        name,
      );
    }

    const effect = await client.intercept({
      toolName: name,
      args,
      tier,
      compensation,
      sessionId: explicitSessionId,
    });

    const isReplay = effect.status === "committed";

    let result: TResult;
    try {
      result = await fn(args, context);
    } catch (err) {
      if (!isReplay) {
        try {
          await client.fail(
            effect.effectId,
            err instanceof Error ? err.message : String(err),
          );
        } catch {
          console.warn(
            "[undolog] Failed to record effect failure; effect %s may be stuck in pending",
            effect.effectId,
          );
        }
      }
      throw err;
    }

    if (!isReplay) {
      try {
        await client.commit(effect.effectId);
      } catch (commitErr) {
        console.warn(
          "[undolog] Failed to commit effect %s; effect may be stuck in pending",
          effect.effectId,
        );
        if (isCompensable(tier) && compensation !== undefined) {
          console.warn(
            "[undolog] Attempting compensation for effect %s after commit failure",
            effect.effectId,
          );
        }
        throw commitErr;
      }
    }

    return result;
  };
}
