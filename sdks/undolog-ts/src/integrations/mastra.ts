/** Integration with Mastra (`mastra`).
 *
 * Provides ``undologMastraTool()``, ``wrapMastraAgent()``, and
 * ``wrapMastraTools()`` to instrument Mastra tools, agents, and
 * workflows with UndoLog effect tracking, enabling exactly-once execution,
 * automated session lifecycle management, and clean suspension states
 * for human approval gates.
 *
 * @module
 */

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
import { AwaitingApprovalError } from "../errors.js";

/** Options for configuring an UndoLog-wrapped Mastra tool. */
export interface UndologMastraToolOptions {
  readonly toolName: string;
  readonly tier: ToolTier;
  readonly compensation?: CompensationDescriptor;
  readonly sessionId?: string;
  readonly requireSession?: boolean;
}

/** A Mastra-compatible tool wrapped with UndoLog effect tracking. */
export interface UndologMastraTool<
  TInput extends Record<string, unknown> = Record<string, unknown>,
  TOutput = unknown,
> {
  readonly id: string;
  readonly description: string;
  readonly inputSchema?: unknown;
  readonly outputSchema?: unknown;
  readonly execute: (inputData: TInput, context?: unknown) => Promise<TOutput>;
  readonly _undologWrapped?: boolean;
}

/** Options for configuring Mastra agent and toolset auto-instrumentation. */
export interface WrapMastraAgentOptions {
  readonly client?: UndoLogClient;
  readonly tiers?: Record<string, ToolTier>;
  readonly defaultTier?: ToolTier;
  readonly compensations?: Record<string, CompensationDescriptor>;
  readonly autoSession?: boolean;
  readonly sessionOptions?: SessionOptions;
  readonly sessionId?: string;
  readonly requireSession?: boolean;
  readonly suspendOnApproval?: boolean;
}

/** Workflow suspension state returned when an irreversible operation awaits approval. */
export interface MastraSuspensionState {
  readonly status: "suspended";
  readonly suspended: true;
  readonly reason: "awaiting_approval";
  readonly approvalId: string;
  readonly toolName: string;
  readonly args?: Record<string, unknown>;
  readonly error: AwaitingApprovalError;
}

/** Type guard verifying if a returned value represents a workflow suspension state. */
export function isSuspensionState(v: unknown): v is MastraSuspensionState {
  return Boolean(
    v &&
      typeof v === "object" &&
      (v as Record<string, unknown>).status === "suspended" &&
      (v as Record<string, unknown>).reason === "awaiting_approval",
  );
}

/** Duck-typed interface for Mastra tools exposing execute and schemas. */
export interface MastraToolLike {
  readonly id?: string;
  readonly name?: string;
  readonly description?: string;
  readonly inputSchema?: unknown;
  readonly outputSchema?: unknown;
  execute?: (inputData: any, context?: any) => Promise<any> | any;
  [key: string]: any;
}

/** Duck-typed interface for Mastra agents or workflows exposing execution loops. */
export interface MastraAgentLike {
  name?: string;
  tools?: Record<string, MastraToolLike> | MastraToolLike[] | Map<string, MastraToolLike>;
  generate?: (promptOrMessages: any, options?: any) => Promise<any>;
  stream?: (promptOrMessages: any, options?: any) => Promise<any>;
  execute?: (input: any, context?: any) => Promise<any>;
  [key: string]: any;
}

/** Mapped return type preserving agent methods and attaching instrumented tools. */
export type WrappedMastraAgent<T extends MastraAgentLike> = T & {
  readonly _undologWrapped: boolean;
};

/** Mapped return type for an individual instrumented Mastra tool. */
export type WrappedMastraTool<T = any> = T & {
  readonly _undologWrapped: boolean;
};

function isClient(v: unknown): v is UndoLogClient {
  return Boolean(v && typeof v === "object" && (v as any).intercept && (v as any).commit);
}

function toSuspension(err: AwaitingApprovalError, toolName?: string): MastraSuspensionState {
  return {
    status: "suspended",
    suspended: true,
    reason: "awaiting_approval",
    approvalId: err.approvalId,
    toolName: err.toolName ?? toolName ?? "unknown",
    args: err.args,
    error: err,
  };
}

/** Wraps a Mastra tool definition with UndoLog effect tracking. */
export function undologMastraTool<
  TInput extends Record<string, unknown>,
  TOutput,
>(
  client: UndoLogClient,
  def: {
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
  const toolDef: ToolDefinition<TInput, TOutput> = {
    name: options.toolName,
    description: def.description,
    tier: options.tier,
    fn: (d, c) => def.execute(d, c),
    compensation: options.compensation,
    sessionId: options.sessionId,
    requireSession: options.requireSession,
  };
  const wrapped = wrapTool(client, toolDef);
  return {
    id: def.id,
    description: def.description,
    inputSchema: def.inputSchema,
    outputSchema: def.outputSchema,
    execute: (d: TInput, c?: unknown): Promise<TOutput> => wrapped(d, c),
    _undologWrapped: true,
  };
}

/** Instruments a single Mastra tool in-place with UndoLog effect tracking. */
function instrumentMastraTool(
  client: UndoLogClient,
  tool: any,
  options?: WrapMastraAgentOptions,
): any {
  if (!tool || typeof tool !== "object" || tool._undologWrapped) return tool;

  const id = tool.id ?? tool.name ?? "unnamed_tool";
  const tier =
    options?.tiers?.[id] ??
    (tool.name ? options?.tiers?.[tool.name] : undefined) ??
    options?.defaultTier ??
    ToolTier.Compensable;
  const compensation =
    options?.compensations?.[id] ??
    (tool.name ? options?.compensations?.[tool.name] : undefined);

  if (typeof tool.execute !== "function") return tool;

  const orig = tool.execute.bind(tool);
  const wrapped = wrapTool(client, {
    name: id,
    description: tool.description,
    tier,
    fn: (a: Record<string, unknown>, c?: unknown) => orig(a, c),
    compensation,
    sessionId: options?.sessionId,
    requireSession: options?.requireSession,
  });

  tool.execute = async (input: any, ctx?: any) => {
    try {
      const norm =
        typeof input === "object" && input !== null
          ? input
          : input !== undefined
            ? { input }
            : {};
      return await wrapped(norm, ctx);
    } catch (err) {
      if (err instanceof AwaitingApprovalError && options?.suspendOnApproval) {
        return toSuspension(err, id);
      }
      throw err;
    }
  };
  tool._undologWrapped = true;
  return tool;
}

/** Wraps a collection of Mastra tools (dictionary, array, or Map) with UndoLog effect tracking. */
export function wrapMastraTools<
  T extends Record<string, MastraToolLike> | MastraToolLike[] | Map<string, MastraToolLike>,
>(
  client: UndoLogClient,
  tools: T,
  options?: WrapMastraAgentOptions,
): T extends Array<infer U>
  ? Array<WrappedMastraTool<U>>
  : T extends Map<infer K, infer V>
    ? Map<K, WrappedMastraTool<V>>
    : { [K in keyof T]: WrappedMastraTool<T[K]> };

export function wrapMastraTools<
  T extends Record<string, MastraToolLike> | MastraToolLike[] | Map<string, MastraToolLike>,
>(
  tools: T,
  options?: WrapMastraAgentOptions,
): T extends Array<infer U>
  ? Array<WrappedMastraTool<U>>
  : T extends Map<infer K, infer V>
    ? Map<K, WrappedMastraTool<V>>
    : { [K in keyof T]: WrappedMastraTool<T[K]> };

export function wrapMastraTools<
  T extends Record<string, MastraToolLike> | MastraToolLike[] | Map<string, MastraToolLike>,
>(
  clientOrTools: UndoLogClient | T,
  toolsOrOptions?: T | WrapMastraAgentOptions,
  maybeOptions?: WrapMastraAgentOptions,
): any {
  let client: UndoLogClient;
  let raw: T;
  let opt: WrapMastraAgentOptions | undefined;

  if (isClient(clientOrTools)) {
    client = clientOrTools;
    raw = (toolsOrOptions ?? (Array.isArray(toolsOrOptions) ? [] : {})) as T;
    opt = maybeOptions;
  } else {
    raw = (clientOrTools ?? (Array.isArray(clientOrTools) ? [] : {})) as T;
    opt = toolsOrOptions as WrapMastraAgentOptions | undefined;
    if (!opt?.client) {
      throw new Error("wrapMastraTools requires an UndoLogClient instance");
    }
    client = opt.client;
  }

  if (Array.isArray(raw)) {
    for (let i = 0; i < raw.length; i++) {
      raw[i] = instrumentMastraTool(client, raw[i], opt);
    }
  } else if (raw instanceof Map) {
    for (const [k, v] of raw.entries()) {
      raw.set(k, instrumentMastraTool(client, v, opt));
    }
  } else if (raw && typeof raw === "object") {
    for (const [k, v] of Object.entries(raw)) {
      (raw as any)[k] = instrumentMastraTool(client, v, opt);
    }
  }

  return raw;
}

/** Wraps a Mastra agent or workflow with UndoLog effect tracking. */
export function wrapMastraAgent<T extends MastraAgentLike>(
  client: UndoLogClient,
  agent: T,
  options?: WrapMastraAgentOptions,
): WrappedMastraAgent<T>;

export function wrapMastraAgent<T extends MastraAgentLike>(
  agent: T,
  options?: WrapMastraAgentOptions,
): WrappedMastraAgent<T>;

export function wrapMastraAgent<T extends MastraAgentLike>(
  clientOrAgent: UndoLogClient | T,
  agentOrOptions?: T | WrapMastraAgentOptions,
  maybeOptions?: WrapMastraAgentOptions,
): WrappedMastraAgent<T> {
  let client: UndoLogClient;
  let raw: T;
  let opt: WrapMastraAgentOptions | undefined;

  if (isClient(clientOrAgent)) {
    client = clientOrAgent;
    raw = (agentOrOptions ?? {}) as T;
    opt = maybeOptions;
  } else {
    raw = (clientOrAgent ?? {}) as T;
    opt = agentOrOptions as WrapMastraAgentOptions | undefined;
    if (!opt?.client) {
      throw new Error("wrapMastraAgent requires an UndoLogClient instance");
    }
    client = opt.client;
  }

  const autoSession = opt?.autoSession ?? true;
  if (raw.tools) {
    wrapMastraTools(client, raw.tools as any, opt);
  }

  const exec = (fn: Function, ...args: any[]) => {
    const run = () => {
      try {
        const res = fn(...args);
        return res instanceof Promise
          ? res.catch((err) =>
              err instanceof AwaitingApprovalError && opt?.suspendOnApproval
                ? toSuspension(err)
                : Promise.reject(err),
            )
          : res;
      } catch (err) {
        if (err instanceof AwaitingApprovalError && opt?.suspendOnApproval) {
          return toSuspension(err);
        }
        throw err;
      }
    };

    if (getCurrentSession() !== undefined || !autoSession) {
      return run();
    }

    return runWithSession(
      new UndoLogSession({ sessionId: opt?.sessionId, ...opt?.sessionOptions }),
      run,
    );
  };

  const wrapped = Object.create(Object.getPrototypeOf(raw));
  Object.assign(wrapped, raw);
  wrapped._undologWrapped = true;

  for (const m of ["generate", "stream", "execute"] as const) {
    const fn = (raw as any)[m];
    if (typeof fn === "function") {
      (wrapped as any)[m] = (...args: any[]) => exec(fn.bind(raw), ...args);
    }
  }

  return wrapped;
}
