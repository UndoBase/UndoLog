/** Decorators for wrapping tool functions with UndoLog effect tracking.
 *
 * Provides the ``wrapTool()`` higher-order function that decorates a tool with
 * the complete effect lifecycle: intercept, execute, commit on success, and
 * fail on error.
 *
 * @module
 */

import { UndoLogClient, type EffectRecord } from "./client.js";
import { ToolTier, type CompensationDescriptor, isSafe } from "./tier.js";
import { AwaitingApprovalError } from "./errors.js";

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
  readonly fn: (args: TArgs) => TResult | Promise<TResult>;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;
}

/** An async function that has been wrapped with UndoLog effect tracking.
 *
 * @typeParam TArgs - Shape of the tool's arguments object.
 * @typeParam TResult - Return type of the tool function.
 */
export type WrappedTool<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> = (args: TArgs) => Promise<TResult>;

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
 */
export function wrapTool<
  TArgs extends Record<string, unknown>,
  TResult,
>(
  client: UndoLogClient,
  definition: ToolDefinition<TArgs, TResult>,
): WrappedTool<TArgs, TResult> {
  const { name, tier, fn, compensation } = definition;

  return async (args: TArgs): Promise<TResult> => {
    if (isSafe(tier)) {
      return fn(args);
    }

    let effect: EffectRecord;
    try {
      effect = await client.intercept({
        toolName: name,
        args,
        tier,
        compensation,
      });
    } catch (err) {
      if (err instanceof AwaitingApprovalError) {
        throw err;
      }
      throw err;
    }

    const isReplay = effect.status === "committed";

    let result: TResult;
    try {
      result = await fn(args);
    } catch (err) {
      if (!isReplay) {
        try {
          await client.fail(
            effect.effectId,
            err instanceof Error ? err.message : String(err),
          );
        } catch {
          // fail is best-effort; always re-throw the original error
        }
      }
      throw err;
    }

    if (!isReplay) {
      await client.commit(effect.effectId);
    }

    return result;
  };
}
