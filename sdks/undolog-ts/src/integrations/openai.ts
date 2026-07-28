/** Integration with the OpenAI Agents SDK (`openai` / `openai-agents`).
 *
 * Provides the ``undologFunctionTool()`` factory that wraps a tool
 * definition with UndoLog effect tracking, enabling exactly-once execution
 * and human-in-the-loop approval for OpenAI Agents SDK tool calls.
 *
 * @module
 */

import { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import { ToolTier, type CompensationDescriptor } from "../tier.js";

/**
 * Options for wrapping an OpenAI Agents SDK function tool with UndoLog
 * effect tracking.
 */
export interface UndologOpenAIToolOptions {
  /** Logical tool name registered with the UndoLog service. */
  readonly toolName: string;

  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;
}

/**
 * A tool object compatible with the OpenAI Agents SDK ``functionTool``
 * shape.
 *
 * @typeParam TArgs - Parsed argument type of the tool.
 * @typeParam TResult - Return type of the tool's execute function.
 */
export interface UndologOpenAITool<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  /** The tool name shown to the model. */
  readonly name: string;

  /** Human-readable description of what the tool does. */
  readonly description?: string;

  /** JSON Schema for tool parameter validation. */
  readonly parameters?: Record<string, unknown>;

  /** The wrapped execute function with UndoLog effect tracking. */
  readonly execute: (args: TArgs) => Promise<TResult>;
}

/**
 * Wraps a tool definition with UndoLog effect tracking, producing an
 * object compatible with the OpenAI Agents SDK ``functionTool`` shape.
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
 *     import { UndoLogClient } from "@undolog/sdk";
 *     import { undologFunctionTool } from "@undolog/sdk/openai";
 *     import { ToolTier } from "@undolog/sdk";
 *
 *     const client = new UndoLogClient({
 *       baseUrl: "http://localhost:8080",
 *     });
 *
 *     const weatherTool = undologFunctionTool(
 *       client,
 *       {
 *         name: "get_weather",
 *         description: "Get the weather for a location",
 *         parameters: {
 *           type: "object",
 *           properties: { location: { type: "string" } },
 *           required: ["location"],
 *         },
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
 *     // Use with OpenAI Agents SDK:
 *     // const agent = new Agent({ name: "WeatherBot", tools: [weatherTool] });
 *
 * @typeParam TArgs - Parsed argument type of the tool (must extend
 *   ``Record<string, unknown>``).
 * @typeParam TResult - Return type of the tool's execute function.
 * @param client - Configured UndoLog client instance.
 * @param definition - Tool name, description, parameters schema, and
 *   execute function.
 * @param options - UndoLog metadata (tool name, tier, compensation).
 * @returns A tool object compatible with the OpenAI Agents SDK.
 */
export function undologFunctionTool<
  TArgs extends Record<string, unknown>,
  TResult,
>(
  client: UndoLogClient,
  definition: {
    readonly name: string;
    readonly description?: string;
    readonly parameters?: Record<string, unknown>;
    readonly execute: (args: TArgs) => TResult | Promise<TResult>;
  },
  options: UndologOpenAIToolOptions,
): UndologOpenAITool<TArgs, TResult> {
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
    name: definition.name,
    description: definition.description,
    parameters: definition.parameters,
    execute: wrappedExecute,
  };
}
