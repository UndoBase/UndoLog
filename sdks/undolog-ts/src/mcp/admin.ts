/** Dedicated MCP server for administrative dead-letter queue management.
 *
 * Provides ``createUndoLogAdminMcpServer()`` to expose dead-letter inspection,
 * retry, and skip operations over MCP without creating effect tracking records.
 *
 * @module
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { Tool } from "@modelcontextprotocol/sdk/types.js";
import type { DeadLetterState, UndoLogAdminClient } from "../admin.js";

/** Options for configuring the dead-letter administrative MCP server. */
export interface UndoLogAdminMcpServerOptions {
  readonly name?: string;
  readonly version?: string;
}

const textResp = (text: string, isError?: boolean) => ({
  content: [{ type: "text" as const, text }],
  ...(isError ? { isError: true } : {}),
});

const UUID_REGEX =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

const ADMIN_TOOLS: readonly Tool[] = [
  {
    name: "undolog_list_dead_letters",
    description: "List dead-letter compensation records for the authenticated organization.",
    inputSchema: {
      type: "object",
      properties: {
        state: {
          type: "string",
          enum: ["failed", "retrying", "skipped"],
          description: "Lifecycle state filter for dead-letter records. Defaults to failed.",
        },
      },
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
  {
    name: "undolog_retry_dead_letter",
    description: "Retry a failed dead-letter compensation record.",
    inputSchema: {
      type: "object",
      properties: {
        deadLetterId: {
          type: "string",
          description: "Unique UUID of the dead-letter record to retry.",
        },
      },
      required: ["deadLetterId"],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: false,
    },
  },
  {
    name: "undolog_skip_dead_letter",
    description: "Skip a failed dead-letter compensation record without retrying.",
    inputSchema: {
      type: "object",
      properties: {
        deadLetterId: {
          type: "string",
          description: "Unique UUID of the dead-letter record to mark as skipped.",
        },
      },
      required: ["deadLetterId"],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: false,
    },
  },
];

/** Creates an MCP server pre-configured with administrative dead-letter tools. */
export function createUndoLogAdminMcpServer(
  client: UndoLogAdminClient,
  options?: UndoLogAdminMcpServerOptions,
): Server {
  const server = new Server(
    {
      name: options?.name ?? "undolog-admin-mcp",
      version: options?.version ?? "0.1.0",
    },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [...ADMIN_TOOLS],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const { name, arguments: rawArgs } = req.params;
    const args = (rawArgs ?? {}) as Record<string, unknown>;

    switch (name) {
      case "undolog_list_dead_letters": {
        const stateArg = args.state;
        if (
          stateArg !== undefined &&
          (typeof stateArg !== "string" || !["failed", "retrying", "skipped"].includes(stateArg))
        ) {
          return textResp(
            "Validation error: state must be one of: failed, retrying, skipped",
            true,
          );
        }
        try {
          const resp = await client.listDeadLetters({
            state: stateArg as DeadLetterState | undefined,
          });
          return textResp(JSON.stringify(resp));
        } catch (err) {
          return textResp(err instanceof Error ? err.message : String(err), true);
        }
      }

      case "undolog_retry_dead_letter": {
        const id = args.deadLetterId ?? args.dead_letter_id;
        if (typeof id !== "string" || !id.trim()) {
          return textResp("Validation error: deadLetterId is required and must be a string", true);
        }
        if (!UUID_REGEX.test(id)) {
          return textResp("Validation error: deadLetterId must be a valid UUID", true);
        }
        try {
          const resp = await client.retryDeadLetter(id);
          return textResp(JSON.stringify(resp));
        } catch (err) {
          return textResp(err instanceof Error ? err.message : String(err), true);
        }
      }

      case "undolog_skip_dead_letter": {
        const id = args.deadLetterId ?? args.dead_letter_id;
        if (typeof id !== "string" || !id.trim()) {
          return textResp("Validation error: deadLetterId is required and must be a string", true);
        }
        if (!UUID_REGEX.test(id)) {
          return textResp("Validation error: deadLetterId must be a valid UUID", true);
        }
        try {
          const resp = await client.skipDeadLetter(id);
          return textResp(JSON.stringify(resp));
        } catch (err) {
          return textResp(err instanceof Error ? err.message : String(err), true);
        }
      }

      default:
        return textResp(`Unknown tool: ${name}`, true);
    }
  });

  return server;
}
