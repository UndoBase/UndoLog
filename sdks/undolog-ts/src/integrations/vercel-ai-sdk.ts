/** Integration with the Vercel AI SDK (``ai`` package).
 *
 * Provides the ``undologTool()`` factory that wraps a tool definition with
 * UndoLog effect tracking, enabling exactly-once execution and human-in-the-loop
 * approval for LLM-powered tool calls.
 *
 * @module
 */

import { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import { ToolTier, type CompensationDescriptor } from "../tier.js";

/**
 * Options for wrapping a Vercel AI SDK tool with UndoLog effect tracking.
 */
export interface UndologVercelToolOptions {
  /** Logical tool name registered with the UndoLog service. */
  readonly toolName: string;

  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;
}

/**
 * A Vercel AI SDK tool that has been wrapped with UndoLog effect tracking.
 *
 * Compatible with the ``tools`` parameter of ``generateText`` and
 * ``streamText``.
 *
 * @typeParam TArgs - Parsed argument type of the tool.
 * @typeParam TResult - Return type of the tool's execute function.
 */
export interface UndologVercelTool<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  /** Human-readable description shown to the language model. */
  readonly description?: string;

  /** Zod or JSON schema for tool parameter validation. */
  readonly parameters?: { parse: (input: unknown) => TArgs };

  /** The wrapped execute function with UndoLog effect tracking. */
  readonly execute: (args: TArgs) => Promise<TResult>;
}

/**
 * Wraps a Vercel AI SDK tool definition with UndoLog effect tracking.
 *
 * The factory wraps the tool's ``execute`` function via the core
 * ``wrapTool()`` higher-order function. Each invocation creates an effect
 * record in the UndoLog service:
 *
 * - **Safe** tier tools bypass effect registration entirely (zero overhead).
 * - **Compensable** tier tools register a compensation descriptor so the
 *   action can be undone later.
 * - **Irreversible** tier tools require explicit human approval through the
 *   UndoLog approval workflow before ``execute`` runs.
 *
 * @example
 *     import { tool } from "ai";
 *     import { z } from "zod";
 *     import { UndoLogClient } from "@undobase/undolog-sdk";
 *     import { undologTool } from "@undobase/undolog-sdk/vercel-ai-sdk";
 *     import { ToolTier } from "@undobase/undolog-sdk";
 *
 *     const client = new UndoLogClient({
 *       baseUrl: "http://localhost:8080",
 *     });
 *
 *     const weatherTool = undologTool(
 *       client,
 *       {
 *         description: "Get the weather for a location",
 *         parameters: z.object({ location: z.string() }),
 *         execute: async ({ location }) => {
 *           return { temperature: 72, location };
 *         },
 *       },
 *       {
 *         toolName: "get_weather",
 *         tier: ToolTier.Safe,
 *       },
 *     );
 *
 *     // Use anywhere a Vercel AI SDK `tool()` result is expected:
 *     // generateText({ model, tools: { weather: weatherTool }, prompt });
 *
 * @typeParam TArgs - Parsed argument type of the tool (must extend
 *   ``Record<string, unknown>``).
 * @typeParam TResult - Return type of the tool's execute function.
 * @param client - Configured UndoLog client instance.
 * @param definition - Tool description, parameter schema, and execute function.
 * @param options - UndoLog metadata (tool name, tier, compensation).
 * @returns A wrapped tool object compatible with the Vercel AI SDK.
 */
export function undologTool<
  TArgs extends Record<string, unknown>,
  TResult,
>(
  client: UndoLogClient,
  definition: {
    readonly description?: string;
    readonly parameters?: { parse: (input: unknown) => TArgs };
    readonly execute: (args: TArgs) => TResult | Promise<TResult>;
  },
  options: UndologVercelToolOptions,
): UndologVercelTool<TArgs, TResult> {
  const { toolName, tier, compensation } = options;

  const toolDef: ToolDefinition<TArgs, TResult> = {
    name: toolName,
    description: definition.description,
    tier,
    fn: definition.execute,
    compensation,
  };

  const wrappedExecute = wrapTool(client, toolDef);

  return {
    description: definition.description,
    parameters: definition.parameters,
    execute: wrappedExecute,
  };
}
