/** MCP (Model Context Protocol) server integration for UndoLog.
 *
 * Provides the ``createUndoLogMcpServer()`` factory that exposes
 * UndoLog-wrapped tools as MCP tools using the official
 * ``@modelcontextprotocol/sdk``. The returned server is pre-configured
 * with stdio transport support (call ``server.connect(transport)`` or
 * use the ``connectStdio()`` helper).
 *
 * @module
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import type { ServerOptions } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { Tool } from "@modelcontextprotocol/sdk/types.js";
import type { UndoLogClient } from "../client.js";
import { wrapTool } from "../decorators.js";
import type { ToolDefinition } from "../decorators.js";
import type { ToolTier, CompensationDescriptor } from "../tier.js";

/**
 * A tool registration accepted by ``createUndoLogMcpServer()``.
 *
 * Mirrors the core ``ToolDefinition`` but carries an explicit
 * ``inputSchema`` in JSON Schema format that is advertised to the MCP
 * client during the ``tools/list`` handshake.
 */
export interface UndoLogMcpToolRegistration<
  TArgs extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  /** Tool name exposed to the MCP client (and used for UndoLog effect
   *  registration). */
  readonly name: string;

  /** Human-readable description of what the tool does. */
  readonly description?: string;

  /** JSON Schema object describing the tool's input parameters.
   *
   * Defaults to ``{ type: "object" }`` (no properties) when omitted. */
  readonly inputSchema?: Record<string, unknown>;

  /** Effect tier classification. */
  readonly tier: ToolTier;

  /** The actual tool implementation. */
  readonly execute: (args: TArgs) => TResult | Promise<TResult>;

  /** Compensation descriptor for Compensable-tier tools. */
  readonly compensation?: CompensationDescriptor;
}

/**
 * Options for creating an UndoLog MCP server.
 */
export interface UndoLogMcpServerOptions {
  /** Server name advertised during initialization. Defaults to
   *  ``"undolog-mcp"``. */
  readonly name?: string;

  /** Server version advertised during initialization. Defaults to
   *  ``"0.1.0"``. */
  readonly version?: string;
}

/**
 * Creates an MCP server that exposes UndoLog-wrapped tools.
 *
 * The returned ``Server`` instance handles ``tools/list`` and
 * ``tools/call`` requests. Each tool call is routed through the core
 * ``wrapTool()`` function, providing the full effect lifecycle:
 *
 * - **Safe** tier tools bypass effect registration entirely.
 * - **Compensable** tier tools register a compensation descriptor for
 *   later rollback.
 * - **Irreversible** tier tools require explicit human approval before
 *   execution (``AwaitingApprovalError`` is thrown if approval has not
 *   been granted).
 *
 * Connect to a transport (e.g. stdio) after creation:
 *
 * ```typescript
 * const transport = new StdioServerTransport();
 * await server.connect(transport);
 * ```
 *
 * @example
 * ```typescript
 * import { UndoLogClient, ToolTier } from "@undobase/undolog-sdk";
 * import {
 *   createUndoLogMcpServer,
 *   StdioServerTransport,
 * } from "@undobase/undolog-sdk/mcp";
 *
 * const client = new UndoLogClient({
 *   baseUrl: "http://localhost:8080",
 * });
 *
 * const server = createUndoLogMcpServer(client, [
 *   {
 *     name: "get_weather",
 *     description: "Get the current weather for a location",
 *     inputSchema: {
 *       type: "object",
 *       properties: {
 *         location: { type: "string" },
 *       },
 *       required: ["location"],
 *     },
 *     tier: ToolTier.Safe,
 *     execute: async ({ location }: { location: string }) => {
 *       return { temperature: 72, condition: "sunny", location };
 *     },
 *   },
 * ]);
 *
 * const transport = new StdioServerTransport();
 * await server.connect(transport);
 * ```
 *
 * @param client - Configured UndoLog client instance.
 * @param registrations - Array of tool registrations to expose.
 * @param options - Server metadata (name, version).
 * @returns A ready-to-use MCP ``Server`` instance.
 */
export function createUndoLogMcpServer(
  client: UndoLogClient,
  registrations: readonly UndoLogMcpToolRegistration[],
  options?: UndoLogMcpServerOptions,
): Server {
  const name = options?.name ?? "undolog-mcp";
  const version = options?.version ?? "0.1.0";

  const toolMap = new Map<string, UndoLogMcpToolRegistration>();
  const toolDescriptors: Tool[] = [];

  for (const reg of registrations) {
    toolMap.set(reg.name, reg);
    toolDescriptors.push({
      name: reg.name,
      description: reg.description,
      inputSchema: (reg.inputSchema ?? { type: "object" }) as Tool["inputSchema"],
    });
  }

  const serverOptions: ServerOptions = {
    capabilities: {
      tools: {},
    },
  };

  const server = new Server(
    { name, version },
    serverOptions,
  );

  server.setRequestHandler(
    ListToolsRequestSchema,
    async () => ({
      tools: toolDescriptors,
    }),
  );

  server.setRequestHandler(
    CallToolRequestSchema,
    async (request) => {
      const { name: toolName, arguments: args } = request.params;
      const registration = toolMap.get(toolName);

      if (registration === undefined) {
        return {
          content: [
            {
              type: "text" as const,
              text: `Unknown tool: ${toolName}`,
            },
          ],
          isError: true,
        };
      }

      const toolDef: ToolDefinition<Record<string, unknown>, unknown> = {
        name: registration.name,
        description: registration.description,
        tier: registration.tier,
        fn: registration.execute as (
          args: Record<string, unknown>,
        ) => unknown | Promise<unknown>,
        compensation: registration.compensation,
      };

      const wrappedFn = wrapTool(client, toolDef);

      try {
        const result = await wrappedFn(
          (args ?? {}) as Record<string, unknown>,
        );
        return {
          content: [
            {
              type: "text" as const,
              text:
                typeof result === "string"
                  ? result
                  : JSON.stringify(result),
            },
          ],
        };
      } catch (err) {
        const message =
          err instanceof Error ? err.message : String(err);
        return {
          content: [
            {
              type: "text" as const,
              text: message,
            },
          ],
          isError: true,
        };
      }
    },
  );

  return server;
}

/**
 * Connects an MCP server to stdio transport in a single call.
 *
 * @example
 * ```typescript
 * const server = createUndoLogMcpServer(client, tools);
 * await connectStdio(server);
 * ```
 *
 * @param server - An MCP ``Server`` instance.
 * @returns A promise that resolves once the transport is connected.
 */
export async function connectStdio(
  server: Server,
): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
