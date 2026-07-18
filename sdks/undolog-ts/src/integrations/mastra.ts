/** Integration with Mastra (`@mastra/core`).
 *
 * Provides the ``undologMastraTool()`` factory that wraps a Mastra tool
 * definition with UndoLog effect tracking.  The returned object is
 * structurally compatible with Mastra's ``Tool`` type and can be passed
 * directly to the ``tools`` property of a Mastra agent.
 *
 * @module
 */

import type { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import type { ToolTier, CompensationDescriptor } from "../tier.js";

/** Options for configuring an UndoLog-wrapped Mastra tool.
 *
 * @param toolName - Logical name used for effect registration in the
 *   UndoLog (may differ from the Mastra tool ``id``).
 * @param tier - Effect tier classification.
 * @param compensation - Optional compensation descriptor for
 *   Compensable-tier tools.
 */
export interface UndologMastraToolOptions {
  readonly toolName: string;
  readonly tier: ToolTier;
  readonly compensation?: CompensationDescriptor;
}

/** A Mastra-compatible tool wrapped with UndoLog effect tracking.
 *
 * @typeParam TInput - Shape of the input data accepted by the tool.
 * @typeParam TOutput - Shape of the output produced by the tool.
 */
export interface UndologMastraTool<
  TInput extends Record<string, unknown> = Record<string, unknown>,
  TOutput = unknown,
> {
  /** Unique identifier passed through from the original definition. */
  readonly id: string;

  /** Human-readable description of what the tool does. */
  readonly description: string;

  /** Input schema for validating tool arguments (pass-through). */
  readonly inputSchema?: unknown;

  /** Output schema for validating tool results (pass-through). */
  readonly outputSchema?: unknown;

  /** Executes the tool with UndoLog effect tracking.
   *
   * @param inputData - Validated input arguments.
   * @param context - Mastra execution context (passed through to the
   *   original execute function but ignored by the UndoLog wrapper;
   *   use the original ``createTool`` and wrap the result if you need
   *   full context access inside the tool body).
   * @returns The tool's output.
   */
  readonly execute: (
    inputData: TInput,
    context?: unknown,
  ) => Promise<TOutput>;
}

/** Wraps a Mastra tool definition with UndoLog effect tracking.
 *
 * The factory extracts the ``execute`` function from the raw definition
 * and decorates it with the complete effect lifecycle (intercept,
 * execute, commit / fail).  The returned object preserves the original
 * ``id``, ``description``, ``inputSchema``, and ``outputSchema`` so it
 * remains compatible with Mastra agents.
 *
 * @typeParam TInput - Shape of the tool's input arguments object.
 * @typeParam TOutput - Return type of the tool's execute function.
 * @param client - Configured UndoLog client instance.
 * @param definition - Raw Mastra tool definition (the same object you
 *   would pass to ``createTool()`` from ``@mastra/core/tools``).
 * @param options - UndoLog-specific configuration including the effect
 *   tier and optional compensation descriptor.
 * @returns A Mastra-compatible tool object with UndoLog effect tracking.
 *
 * @example
 * ```typescript
 * import { createTool } from "@mastra/core/tools";
 * import { z } from "zod";
 * import { undologMastraTool } from "@undobase/undolog-sdk/mastra";
 * import { UndoLogClient, ToolTier } from "@undobase/undolog-sdk";
 *
 * const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
 *
 * const myTool = undologMastraTool(
 *   client,
 *   {
 *     id: "reverse-string",
 *     description: "Reverse the input string",
 *     inputSchema: z.object({ input: z.string() }),
 *     outputSchema: z.object({ output: z.string() }),
 *     execute: async ({ input }) => ({ output: input.split("").reverse().join("") }),
 *   },
 *   { toolName: "reverse-string", tier: ToolTier.Compensable },
 * );
 * ```
 */
export function undologMastraTool<
  TInput extends Record<string, unknown>,
  TOutput,
>(
  client: UndoLogClient,
  definition: {
    readonly id: string;
    readonly description: string;
    readonly inputSchema?: unknown;
    readonly outputSchema?: unknown;
    readonly execute: (
      inputData: TInput,
      context?: unknown,
    ) => TOutput | Promise<TOutput>;
  },
  options: UndologMastraToolOptions,
): UndologMastraTool<TInput, TOutput> {
  const { toolName, tier, compensation } = options;
  const toolDef: ToolDefinition<TInput, TOutput> = {
    name: toolName,
    description: definition.description,
    tier,
    fn: definition.execute,
    compensation,
  };
  const wrappedExecute = wrapTool(client, toolDef);
  return {
    id: definition.id,
    description: definition.description,
    inputSchema: definition.inputSchema,
    outputSchema: definition.outputSchema,
    execute: async (
      inputData: TInput,
      _context?: unknown,
    ): Promise<TOutput> => wrappedExecute(inputData),
  };
}
