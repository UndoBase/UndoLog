/** Integration with LangChain (``@langchain/core``).
 *
 * Provides ``createUndologTool()``, ``wrapLangChainAgent()``, and
 * ``wrapLangChainTools()`` to instrument LangChain tools, agents, and
 * executors with UndoLog effect tracking, enabling exactly-once execution,
 * automated session lifecycle management, and human-in-the-loop approval
 * for LangChain-powered tool calls.
 *
 * @module
 */

import { DynamicStructuredTool } from "@langchain/core/tools";
import type { z } from "zod";
import type { ZodTypeAny } from "zod";
import type { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import { ToolTier, type CompensationDescriptor } from "../tier.js";
import {
  UndoLogSession,
  getCurrentSession,
  runWithSession,
  type SessionOptions,
} from "../session.js";

/** Options for wrapping a single LangChain tool with UndoLog effect tracking. */
export interface UndologLangchainToolOptions {
  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;

  /** Explicit session ID to associate with this tool's executions. */
  readonly sessionId?: string;
}

/** Options for configuring LangChain agent and tool auto-instrumentation. */
export interface WrapLangChainAgentOptions {
  /** Configured UndoLog client instance. If omitted, a default client is used. */
  readonly client?: UndoLogClient;

  /** Per-tool tier overrides keyed by tool name. Defaults to ``ToolTier.Compensable`` or ``defaultTier``. */
  readonly tiers?: Record<string, ToolTier>;

  /** Default tier for tools not explicitly specified in ``tiers``. Defaults to ``ToolTier.Compensable``. */
  readonly defaultTier?: ToolTier;

  /** Per-tool compensation descriptors keyed by tool name. */
  readonly compensations?: Record<string, CompensationDescriptor>;

  /**
   * Whether to automatically establish an UndoLogSession context when the
   * agent executes outside of an active session. Defaults to true.
   */
  readonly autoSession?: boolean;

  /** Session configuration options used when auto-creating sessions. */
  readonly sessionOptions?: SessionOptions;

  /** Explicit session ID to associate with all tool calls. */
  readonly sessionId?: string;

  /**
   * Whether to require an active session context.
   * If true, throws MissingSessionError when invoked without an active session context.
   */
  readonly requireSession?: boolean;
}

/** Duck-typed interface for LangChain tools exposing call, invoke, or func. */
export interface LangChainToolLike {
  readonly name: string;
  readonly description?: string;
  readonly schema?: any;
  call?: (input: any, options?: any) => Promise<any> | any;
  invoke?: (input: any, options?: any) => Promise<any> | any;
  func?: (input: any, options?: any) => Promise<any> | any;
  [key: string]: any;
}

/** Duck-typed interface for LangChain agent executors or runnables. */
export interface LangChainAgentExecutorLike {
  tools?: any[];
  invoke?: (input: any, options?: any) => Promise<any>;
  call?: (input: any, options?: any) => Promise<any>;
  [key: string]: any;
}

/** Mapped return type preserving executor methods and attaching instrumented tools. */
export type WrappedLangChainAgent<T extends LangChainAgentExecutorLike> = Omit<
  T,
  "invoke" | "call" | "tools"
> & {
  tools: any[];
  readonly _undologWrapped: boolean;
} & (T extends { invoke: (...args: any[]) => any }
  ? {
      invoke: (
        ...args: Parameters<T["invoke"]> extends []
          ? [input?: unknown, runOptions?: unknown]
          : Parameters<T["invoke"]>
      ) => ReturnType<T["invoke"]>;
    }
  : { invoke?: (input?: unknown, runOptions?: unknown) => Promise<unknown> }) &
  (T extends { call: (...args: any[]) => any }
    ? {
        call: (
          ...args: Parameters<T["call"]> extends []
            ? [input?: unknown, runOptions?: unknown]
            : Parameters<T["call"]>
        ) => ReturnType<T["call"]>;
      }
    : { call?: (input?: unknown, runOptions?: unknown) => Promise<unknown> });

/** Wraps a LangChain ``DynamicStructuredTool`` configuration with UndoLog effect tracking.
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
 * ```ts
 * const weatherTool = createUndologTool(
 *   client,
 *   {
 *     name: "get_weather",
 *     description: "Get the weather for a location",
 *     schema: z.object({ location: z.string() }),
 *     func: async ({ location }) => `It is 72F in ${location}`,
 *   },
 *   {
 *     tier: ToolTier.Safe,
 *   },
 * );
 * ```
 *
 * @typeParam T - Zod schema type for the tool's parameters.
 * @param client - Configured UndoLog client instance.
 * @param config - LangChain tool configuration (name, description, schema, func).
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
  const { tier, compensation, sessionId } = options;

  const toolDef: ToolDefinition = {
    name: config.name,
    description: config.description,
    tier,
    fn: config.func as (args: Record<string, unknown>) => unknown | Promise<unknown>,
    compensation,
    sessionId,
  };

  const wrappedExecute = wrapTool(client, toolDef);

  return new DynamicStructuredTool({
    name: config.name,
    description: config.description,
    schema: config.schema,
    func: wrappedExecute as (input: z.infer<T>) => Promise<unknown>,
  });
}

const RAW_INPUT = Symbol("RAW_INPUT");

function normalizeInput(input: unknown): Record<string, unknown> {
  if (input === undefined) {
    return { [RAW_INPUT]: undefined } as Record<string, unknown>;
  }
  if (input === null) {
    return { [RAW_INPUT]: null } as Record<string, unknown>;
  }
  if (typeof input === "object") {
    return input as Record<string, unknown>;
  }
  return { input, [RAW_INPUT]: input } as Record<string, unknown>;
}

/** Instruments a single LangChain tool with UndoLog effect tracking.
 *
 * @param client - Configured UndoLog client instance.
 * @param tool - Raw LangChain tool object.
 * @param options - Optional configuration for tiers, compensations, and session ID.
 * @returns An instrumented tool clone wrapping invocation methods.
 */
function instrumentLangChainTool(
  client: UndoLogClient,
  tool: any,
  options?: WrapLangChainAgentOptions,
): any {
  if (!tool || typeof tool !== "object") {
    return tool;
  }

  if (tool._undologWrapped) {
    return tool;
  }

  const name = tool.name ?? "unnamed_tool";
  const tier = options?.tiers?.[name] ?? options?.defaultTier ?? ToolTier.Compensable;
  const compensation = options?.compensations?.[name];

  const targetFn =
    typeof tool.func === "function"
      ? tool.func.bind(tool)
      : typeof tool.invoke === "function"
        ? tool.invoke.bind(tool)
        : typeof tool.call === "function"
          ? tool.call.bind(tool)
          : undefined;

  if (!targetFn) {
    return tool;
  }

  const toolDef: ToolDefinition = {
    name,
    description: tool.description,
    tier,
    fn: async (args: Record<string, unknown>, context?: unknown) => {
      const callArg =
        args && typeof args === "object" && RAW_INPUT in args
          ? (args as Record<string | symbol, unknown>)[RAW_INPUT]
          : args;
      return targetFn(callArg, context);
    },
    compensation,
    sessionId: options?.sessionId,
    requireSession: options?.requireSession,
  };

  const wrappedFn = wrapTool(client, toolDef);

  const wrappedCall = async (input: unknown, runOptions?: unknown) => {
    return wrappedFn(normalizeInput(input), runOptions);
  };
  const wrappedInvoke = async (input: unknown, runOptions?: unknown) => {
    return wrappedFn(normalizeInput(input), runOptions);
  };
  const wrappedFunc = async (input: unknown, runOptions?: unknown) => {
    return wrappedFn(normalizeInput(input), runOptions);
  };

  tool._undologWrapped = true;
  if (typeof tool.call === "function") {
    tool.call = wrappedCall;
  }
  if (typeof tool.invoke === "function") {
    tool.invoke = wrappedInvoke;
  }
  if (typeof tool.func === "function") {
    tool.func = wrappedFunc;
  }

  return tool;
}

/** Mapped return type for an individual instrumented LangChain tool. */
export type WrappedLangChainTool<T = any> = T & {
  readonly _undologWrapped: boolean;
};

function isClient(value: unknown): value is UndoLogClient {
  return Boolean(
    value &&
      typeof value === "object" &&
      typeof (value as any).intercept === "function" &&
      typeof (value as any).commit === "function",
  );
}

/** Wraps an array of LangChain tools with UndoLog effect tracking.
 *
 * @param client - Configured UndoLog client instance.
 * @param tools - Array of LangChain tool objects.
 * @param options - Configuration options for tiers, compensations, and session ID.
 * @returns Array of instrumented tools.
 */
export function wrapLangChainTools<T extends any[]>(
  client: UndoLogClient,
  tools: T,
  options?: WrapLangChainAgentOptions,
): Array<WrappedLangChainTool<T[number]>>;

/** Wraps an array of LangChain tools with UndoLog effect tracking.
 *
 * Overload accepting tools array and options directly.
 *
 * @param tools - Array of LangChain tool objects.
 * @param options - Configuration options including optional client.
 * @returns Array of instrumented tools.
 */
export function wrapLangChainTools<T extends any[]>(
  tools: T,
  options?: WrapLangChainAgentOptions,
): Array<WrappedLangChainTool<T[number]>>;

export function wrapLangChainTools<T extends any[]>(
  clientOrTools: UndoLogClient | T,
  toolsOrOptions?: T | WrapLangChainAgentOptions,
  maybeOptions?: WrapLangChainAgentOptions,
): Array<WrappedLangChainTool<T[number]>> {
  let client: UndoLogClient;
  let rawTools: T;
  let options: WrapLangChainAgentOptions | undefined;

  if (isClient(clientOrTools)) {
    client = clientOrTools;
    rawTools = (toolsOrOptions ?? []) as T;
    options = maybeOptions;
  } else {
    rawTools = (clientOrTools ?? []) as T;
    options = toolsOrOptions as WrapLangChainAgentOptions | undefined;
    if (!options?.client) {
      throw new Error(
        "wrapLangChainTools requires an UndoLogClient instance: pass client as the first argument or in options.client.",
      );
    }
    client = options.client;
  }

  return rawTools.map((t) => instrumentLangChainTool(client, t, options)) as T;
}

/** Wraps a LangChain agent executor or runnable with UndoLog effect tracking.
 *
 * Automatically inspects the executor's registered tools, binds UndoLog
 * effect interception, establishes ambient session context across the
 * agent execution loop, and handles approval gates.
 *
 * @example
 * ```ts
 * const wrappedExecutor = wrapLangChainAgent(client, executor, {
 *   tiers: {
 *     search_database: ToolTier.Safe,
 *     delete_record: ToolTier.Irreversible,
 *   },
 * });
 * const result = await wrappedExecutor.invoke({ input: "Delete user 42" });
 * ```
 *
 * @typeParam T - Agent executor or runnable type.
 * @param client - Configured UndoLog client instance.
 * @param executor - LangChain agent executor or runnable instance.
 * @param options - Configuration options for tiers, compensations, and session lifecycle.
 * @returns An instrumented agent executor with ambient session management and wrapped tools.
 */
export function wrapLangChainAgent<T extends LangChainAgentExecutorLike>(
  client: UndoLogClient,
  executor: T,
  options?: WrapLangChainAgentOptions,
): WrappedLangChainAgent<T>;

/** Wraps a LangChain agent executor or runnable with UndoLog effect tracking.
 *
 * Overload accepting executor and options directly.
 *
 * @typeParam T - Agent executor or runnable type.
 * @param executor - LangChain agent executor or runnable instance.
 * @param options - Configuration options including optional client.
 * @returns An instrumented agent executor with ambient session management and wrapped tools.
 */
export function wrapLangChainAgent<T extends LangChainAgentExecutorLike>(
  executor: T,
  options?: WrapLangChainAgentOptions,
): WrappedLangChainAgent<T>;

export function wrapLangChainAgent<T extends LangChainAgentExecutorLike>(
  clientOrExecutor: UndoLogClient | T,
  executorOrOptions?: T | WrapLangChainAgentOptions,
  maybeOptions?: WrapLangChainAgentOptions,
): WrappedLangChainAgent<T> {
  let client: UndoLogClient;
  let rawExecutor: T;
  let options: WrapLangChainAgentOptions | undefined;

  if (isClient(clientOrExecutor)) {
    client = clientOrExecutor;
    rawExecutor = (executorOrOptions ?? {}) as T;
    options = maybeOptions;
  } else {
    rawExecutor = (clientOrExecutor ?? {}) as T;
    options = executorOrOptions as WrapLangChainAgentOptions | undefined;
    if (!options?.client) {
      throw new Error(
        "wrapLangChainAgent requires an UndoLogClient instance: pass client as the first argument or in options.client.",
      );
    }
    client = options.client;
  }

  const autoSession = options?.autoSession ?? true;
  const existingTools = Array.isArray(rawExecutor.tools) ? rawExecutor.tools : [];
  const instrumentedTools = wrapLangChainTools(client, existingTools, options);

  const originalInvoke =
    typeof rawExecutor.invoke === "function" ? rawExecutor.invoke.bind(rawExecutor) : undefined;
  const originalCall =
    typeof rawExecutor.call === "function" ? rawExecutor.call.bind(rawExecutor) : undefined;

  const executeWithSessionContext = async (
    targetMethod: (input: any, opts?: any) => Promise<any>,
    input: any,
    runOptions?: any,
  ) => {
    const activeSession = getCurrentSession();
    if (activeSession !== undefined || !autoSession) {
      return targetMethod(input, runOptions);
    }

    const session = new UndoLogSession({
      sessionId: options?.sessionId,
      ...options?.sessionOptions,
    });

    return runWithSession(session, () => targetMethod(input, runOptions));
  };

  const wrapped = Object.create(Object.getPrototypeOf(rawExecutor));
  Object.assign(wrapped, rawExecutor);
  wrapped.tools = instrumentedTools;
  wrapped._undologWrapped = true;

  if (originalInvoke) {
    wrapped.invoke = async (input: any, runOptions?: any) => {
      return executeWithSessionContext(originalInvoke, input, runOptions);
    };
  }

  if (originalCall) {
    wrapped.call = async (input: any, runOptions?: any) => {
      return executeWithSessionContext(originalCall, input, runOptions);
    };
  }

  return wrapped as WrappedLangChainAgent<T>;
}
