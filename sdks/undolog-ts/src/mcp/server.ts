/** MCP (Model Context Protocol) server integration for UndoLog.
 *
 * Provides ``createUndoLogMcpServer()`` and ``registerUndologMcpTool()``
 * to expose UndoLog-wrapped tools on MCP servers using the official
 * ``@modelcontextprotocol/sdk``.
 *
 * @module
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { Tool, ToolAnnotations } from "@modelcontextprotocol/sdk/types.js";
import type { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import { ToolTier, type CompensationDescriptor } from "../tier.js";
import { AwaitingApprovalError } from "../errors.js";

/** A tool registration accepted by MCP server registration functions. */
export interface UndoLogMcpToolRegistration<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  readonly name: string;
  readonly description?: string;
  readonly inputSchema?: Record<string, unknown>;
  readonly outputSchema?: Record<string, unknown>;
  readonly tier: ToolTier;
  readonly execute: (args: TArgs) => TResult | Promise<TResult>;
  readonly compensation?: CompensationDescriptor;
  readonly annotations?: ToolAnnotations;
  readonly _meta?: Record<string, unknown>;
  readonly sessionId?: string;
  readonly requireSession?: boolean;
}

/** Options for creating an UndoLog MCP server. */
export interface UndoLogMcpServerOptions {
  readonly name?: string;
  readonly version?: string;
}

/** Duck-typed MCP server interface supporting request handler registration. */
export type McpServerInstance = {
  setRequestHandler: Server["setRequestHandler"];
  [key: string]: any;
};

const REG = Symbol.for("undolog.mcp.registry");

interface ServerRegistry {
  client: UndoLogClient;
  tools: Map<string, UndoLogMcpToolRegistration>;
}

const isClient = (v: unknown): v is UndoLogClient =>
  Boolean(v && typeof v === "object" && (v as any).intercept && (v as any).commit);

const toServer = (s: any): McpServerInstance =>
  s && "server" in s && s.server?.setRequestHandler ? s.server : s;

const textResp = (text: string, isError?: boolean) => ({
  content: [{ type: "text" as const, text }],
  ...(isError ? { isError: true } : {}),
});

const buildDesc = (r: UndoLogMcpToolRegistration): Tool => ({
  name: r.name,
  description: r.description,
  inputSchema: (r.inputSchema ?? { type: "object" }) as Tool["inputSchema"],
  outputSchema: r.outputSchema as any,
  annotations: {
    readOnlyHint: r.tier === ToolTier.Safe,
    destructiveHint: r.tier === ToolTier.Irreversible,
    idempotentHint: r.tier === ToolTier.Safe,
    openWorldHint: false,
    ...r.annotations,
  },
  _meta: {
    tier: r.tier,
    reversibility: r.tier,
    compensation: r.compensation,
    ...r._meta,
  },
});

function initServer(server: McpServerInstance, client: UndoLogClient): ServerRegistry {
  let reg: ServerRegistry = (server as any)[REG];
  if (!reg) {
    (server as any)[REG] = reg = { client, tools: new Map() };

    server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: Array.from(reg.tools.values()).map(buildDesc),
    }));

    server.setRequestHandler(CallToolRequestSchema, async (req) => {
      const tool = reg.tools.get(req.params.name);
      if (!tool) return textResp(`Unknown tool: ${req.params.name}`, true);

      const wrapped = wrapTool(reg.client, { ...tool, fn: (a) => tool.execute(a as any) });
      try {
        const res = await wrapped((req.params.arguments ?? {}) as Record<string, unknown>);
        return textResp(typeof res === "string" ? res : JSON.stringify(res));
      } catch (err) {
        return textResp(
          err instanceof AwaitingApprovalError
            ? JSON.stringify({
                type: "approval_required",
                toolName: err.toolName,
                approvalId: err.approvalId,
                message: err.message,
              })
            : err instanceof Error
              ? err.message
              : String(err),
          true,
        );
      }
    });
  }
  return reg;
}

/** Registers an UndoLog-protected tool directly on an MCP Server instance. */
export function registerUndologMcpTool(
  server: McpServerInstance,
  client: UndoLogClient,
  registration: UndoLogMcpToolRegistration,
): void;

export function registerUndologMcpTool(
  client: UndoLogClient,
  server: McpServerInstance,
  registration: UndoLogMcpToolRegistration,
): void;

export function registerUndologMcpTool(
  first: McpServerInstance | UndoLogClient,
  second: McpServerInstance | UndoLogClient,
  registration: UndoLogMcpToolRegistration,
): void {
  const client = isClient(first) ? first : (second as UndoLogClient);
  const server = toServer(isClient(first) ? second : first);
  if (!server?.setRequestHandler) {
    throw new Error("registerUndologMcpTool requires a valid MCP Server instance");
  }

  const reg = initServer(server, client);
  reg.tools.set(registration.name, registration);
}

/** Creates an MCP server pre-configured with UndoLog-wrapped tools. */
export function createUndoLogMcpServer(
  client: UndoLogClient,
  registrations: readonly UndoLogMcpToolRegistration[],
  options?: UndoLogMcpServerOptions,
): Server {
  const server = new Server(
    { name: options?.name ?? "undolog-mcp", version: options?.version ?? "0.1.0" },
    { capabilities: { tools: {} } },
  );
  initServer(server, client);
  for (const r of registrations) registerUndologMcpTool(server, client, r);
  return server;
}

/** Connects an MCP server to stdio transport in a single call. */
export const connectStdio = (server: Server): Promise<void> =>
  server.connect(new StdioServerTransport());
