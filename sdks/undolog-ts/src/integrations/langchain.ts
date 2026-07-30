/** Integration with LangChain (`@langchain/core`).
 *
 * Provides the ``createUndologTool()`` factory that wraps tool configuration
 * with UndoLog effect tracking, enabling exactly-once execution and
 * human-in-the-loop approval for LangChain-powered tool calls.
 *
 * @module
 */

import { DynamicStructuredTool } from "@langchain/core/tools";
import type { z } from "zod";
import type { ZodTypeAny } from "zod";
import type { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import type { ToolTier, CompensationDescriptor } from "../tier.js";

/**
 * Options for wrapping a LangChain tool with UndoLog effect tracking.
 */
export interface UndologLangchainToolOptions {
  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;
}

/**
 * Wraps a LangChain ``DynamicStructuredTool`` configuration with UndoLog
 * effect tracking.
 *
 * The factory wraps the tool's ``func`` function via the core ``wrapTool()``
 * higher-order function. Each invocation creates an effect record in the
 * UndoLog service:
 *
 * - **Safe** tier tools bypass effect registration entirely (zero overhead).
 * - **Compensable** tier tools register a compensation descriptor so the
 *   action can be undone later.
 * - **Irreversible** tier tools require explicit human approval through the
 *   UndoLog approval workflow before ``func`` runs.
 *
 * @example
 *     import { z } from "zod";
 *     import { UndoLogClient } from "@undolog/sdk";
 *     import { createUndologTool } from "@undolog/sdk/langchain";
 *     import { ToolTier } from "@undolog/sdk";
 *
 *     const client = new UndoLogClient({
 *       baseUrl: "http://localhost:8080",
 *     });
 *
 *     const weatherTool = createUndologTool(
 *       client,
 *       {
 *         name: "get_weather",
 *         description: "Get the weather for a location",
 *         schema: z.object({ location: z.string() }),
 *         func: async ({ location }) => {
 *           return `It is 72\u00b0F in ${location}`;
 *         },
 *       },
 *       {
 *         tier: ToolTier.Safe,
 *       },
 *     );
 *
 *     // Use anywhere a LangChain DynamicStructuredTool is expected.
 *
 * @typeParam T - Zod schema type for the tool's parameters.
 * @param client - Configured UndoLog client instance.
 * @param config - LangChain tool configuration (name, description, schema,
 *   func).
 * @param options - UndoLog metadata (tier, compensation).
 * @returns A DynamicStructuredTool instance with UndoLog effect tracking.
 */
export function createUndologTool<T extends ZodTypeAny>(
  client: UndoLogClient,
  config: {
    readonly name: string;
    readonly description: string;
    readonly schema: T;
    readonly func: (input: z.infer<T>) => unknown | Promise<unknown>;
  },
  options: UndologLangchainToolOptions,
): DynamicStructuredTool<T> {
  const { tier, compensation } = options;

  const toolDef: ToolDefinition = {
    name: config.name,
    description: config.description,
    tier,
    fn: config.func as (args: Record<string, unknown>) => unknown | Promise<unknown>,
    compensation,
  };

  const wrappedExecute = wrapTool(client, toolDef);

  return new DynamicStructuredTool({
    name: config.name,
    description: config.description,
    schema: config.schema,
    func: wrappedExecute as (input: z.infer<T>) => Promise<unknown>,
  });
}
