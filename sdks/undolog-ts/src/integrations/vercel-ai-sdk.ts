/** Integration with the Vercel AI SDK (``ai`` package).
 *
 * Provides ``undologTool()`` and ``wrapVercelAITools()`` to instrument Vercel
 * AI SDK tools and toolsets with UndoLog effect tracking, enabling
 * exactly-once execution, automated session lifecycle management, and
 * human-in-the-loop approval for LLM-powered tool calls.
 *
 * @module
 */

import { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition, WrapToolOptions } from "../decorators.js";
import { ToolTier, type CompensationDescriptor } from "../tier.js";
import {
  UndoLogSession,
  getCurrentSession,
  runWithSession,
  type SessionOptions,
} from "../session.js";

/** Options for wrapping a single Vercel AI SDK tool with UndoLog effect tracking. */
export interface UndologVercelToolOptions {
  /** Logical tool name registered with the UndoLog service. */
  readonly toolName: string;

  /** Effect tier classification. */
  readonly tier: ToolTier;

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

/** A Vercel AI SDK tool that has been wrapped with UndoLog effect tracking.
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
  readonly parameters?: { parse: (input: unknown) => TArgs } | Record<string, unknown>;

  /** The wrapped execute function with UndoLog effect tracking. */
  readonly execute: (args: TArgs, context?: unknown) => Promise<TResult>;
}

/** Options for wrapping an entire dictionary of Vercel AI SDK tools. */
export interface WrapVercelAIToolsOptions {
  /** Configured UndoLog client instance. If omitted, a default client is used. */
  readonly client?: UndoLogClient;

  /** Per-tool tier overrides keyed by tool name. Defaults to ``ToolTier.Compensable`` or ``defaultTier``. */
  readonly tiers?: Record<string, ToolTier>;

  /** Default tier for tools not explicitly specified in ``tiers``. Defaults to ``ToolTier.Compensable``. */
  readonly defaultTier?: ToolTier;

  /** Per-tool compensation descriptors keyed by tool name. */
  readonly compensations?: Record<string, CompensationDescriptor>;

  /**
   * Whether to automatically create an UndoLog session when a tool is called
   * outside an active session context. Defaults to true.
   */
  readonly autoSession?: boolean;

  /** Session options used when auto-creating sessions. */
  readonly sessionOptions?: SessionOptions;

  /** Explicit session ID to associate with all wrapped tools. */
  readonly sessionId?: string;
}

/** Mapped return type preserving tool record keys and parameter schemas while wrapping execute. */
export type WrappedVercelAITools<TTools extends Record<string, any>> = {
  [K in keyof TTools]: TTools[K] extends { execute: (...args: any[]) => any }
    ? Omit<TTools[K], "execute"> & {
        execute: (
          ...args: Parameters<TTools[K]["execute"]> extends []
            ? [args?: Record<string, unknown>, context?: unknown]
            : Parameters<TTools[K]["execute"]>
        ) => Promise<Awaited<ReturnType<TTools[K]["execute"]>>>;
      }
    : TTools[K];
};

/** Wraps a Vercel AI SDK tool definition with UndoLog effect tracking.
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
 * ```ts
 * const weatherTool = undologTool(
 *   client,
 *   {
 *     description: "Get the weather for a location",
 *     parameters: z.object({ location: z.string() }),
 *     execute: async ({ location }) => ({ temperature: 72, location }),
 *   },
 *   {
 *     toolName: "get_weather",
 *     tier: ToolTier.Safe,
 *   },
 * );
 * ```
 *
 * @typeParam TArgs - Parsed argument type of the tool.
 * @typeParam TResult - Return type of the tool's execute function.
 * @param client - Configured UndoLog client instance.
 * @param definition - Tool description, parameter schema, and execute function.
 * @param options - UndoLog metadata (tool name, tier, compensation).
 * @param wrapOptions - Optional wrapTool configuration overrides.
 * @returns A wrapped tool object compatible with the Vercel AI SDK.
 */
export function undologTool<
  TArgs extends Record<string, unknown>,
  TResult,
>(
  client: UndoLogClient,
  definition: {
    readonly description?: string;
    readonly parameters?: { parse: (input: unknown) => TArgs } | Record<string, unknown>;
    readonly execute: (args: TArgs, context?: unknown) => TResult | Promise<TResult>;
  },
  options: UndologVercelToolOptions,
  wrapOptions?: WrapToolOptions,
): UndologVercelTool<TArgs, TResult> {
  const { toolName, tier, compensation, sessionId, requireSession } = options;

  const toolDef: ToolDefinition<TArgs, TResult> = {
    name: toolName,
    description: definition.description,
    tier,
    fn: definition.execute,
    compensation,
    sessionId: sessionId ?? wrapOptions?.sessionId,
    requireSession: requireSession ?? wrapOptions?.requireSession,
  };

  const wrappedExecute = wrapTool(client, toolDef, wrapOptions);

  return {
    description: definition.description,
    parameters: definition.parameters,
    execute: wrappedExecute,
  };
}

/** Wraps an entire dictionary of Vercel AI SDK tools with UndoLog effect tracking.
 *
 * Instruments every tool in the provided record with UndoLog interception,
 * automatic session lifecycle management, tier classification, and approval
 * gating while preserving original parameter validation schemas and
 * descriptions.
 *
 * @example
 * ```ts
 * import { wrapVercelAITools } from "@undolog/sdk/vercel-ai-sdk";
 * import { ToolTier } from "@undolog/sdk";
 *
 * const tools = wrapVercelAITools({
 *   get_weather: weatherTool,
 *   cancel_subscription: cancelTool,
 * }, {
 *   tiers: {
 *     get_weather: ToolTier.Safe,
 *     cancel_subscription: ToolTier.Irreversible,
 *   },
 * });
 * ```
 *
 * @typeParam TTools - Record mapping tool names to Vercel AI SDK tool definitions.
 * @param client - Configured UndoLog client instance.
 * @param tools - Record of tool definitions to wrap.
 * @param options - Configuration for tiers, compensations, and session management.
 * @returns Instrumented tool dictionary ready for ``generateText`` or ``streamText``.
 */
export function wrapVercelAITools<TTools extends Record<string, any>>(
  client: UndoLogClient,
  tools: TTools,
  options?: WrapVercelAIToolsOptions,
): WrappedVercelAITools<TTools>;

/** Wraps an entire dictionary of Vercel AI SDK tools with UndoLog effect tracking.
 *
 * Overload accepting tools dictionary and options directly.
 *
 * @typeParam TTools - Record mapping tool names to Vercel AI SDK tool definitions.
 * @param tools - Record of tool definitions to wrap.
 * @param options - Configuration including optional client, tiers, and session management.
 * @returns Instrumented tool dictionary ready for ``generateText`` or ``streamText``.
 */
export function wrapVercelAITools<TTools extends Record<string, any>>(
  tools: TTools,
  options?: WrapVercelAIToolsOptions,
): WrappedVercelAITools<TTools>;

export function wrapVercelAITools<TTools extends Record<string, any>>(
  clientOrTools: UndoLogClient | TTools,
  toolsOrOptions?: TTools | WrapVercelAIToolsOptions,
  maybeOptions?: WrapVercelAIToolsOptions,
): WrappedVercelAITools<TTools> {
  let client: UndoLogClient;
  let rawTools: TTools;
  let options: WrapVercelAIToolsOptions | undefined;

  if (clientOrTools instanceof UndoLogClient) {
    client = clientOrTools;
    rawTools = (toolsOrOptions ?? {}) as TTools;
    options = maybeOptions;
  } else {
    rawTools = (clientOrTools ?? {}) as TTools;
    options = toolsOrOptions as WrapVercelAIToolsOptions | undefined;
    client =
      options?.client ??
      new UndoLogClient({
        baseUrl:
          typeof process !== "undefined" && process.env?.UNDOLOG_BASE_URL
            ? process.env.UNDOLOG_BASE_URL
            : "http://localhost:8080",
      });
  }

  const defaultTier = options?.defaultTier ?? ToolTier.Compensable;
  const autoSession = options?.autoSession ?? true;
  const wrappedTools = {} as WrappedVercelAITools<TTools>;

  for (const [key, toolDef] of Object.entries(rawTools)) {
    if (!toolDef || typeof toolDef !== "object") {
      (wrappedTools as any)[key] = toolDef;
      continue;
    }

    if (typeof toolDef.execute !== "function") {
      (wrappedTools as any)[key] = { ...toolDef };
      continue;
    }

    const tier = options?.tiers?.[key] ?? defaultTier;
    const compensation = options?.compensations?.[key];

    const instrumented = undologTool(
      client,
      {
        description: toolDef.description,
        parameters: toolDef.parameters,
        execute: toolDef.execute,
      },
      {
        toolName: key,
        tier,
        compensation,
        sessionId: options?.sessionId,
      },
    );

    const baseExecute = instrumented.execute;
    const executeWithSession = async (args?: Record<string, unknown>, context?: unknown) => {
      const resolvedArgs = (args ?? {}) as Record<string, unknown>;
      const activeSession = getCurrentSession();
      if (activeSession !== undefined || !autoSession) {
        return baseExecute(resolvedArgs, context);
      }

      const session = new UndoLogSession({
        sessionId: options?.sessionId,
        ...options?.sessionOptions,
      });

      return runWithSession(session, () => baseExecute(resolvedArgs, context));
    };

    (wrappedTools as any)[key] = {
      ...toolDef,
      description: toolDef.description,
      parameters: toolDef.parameters,
      execute: executeWithSession,
    };
  }

  return wrappedTools;
}
