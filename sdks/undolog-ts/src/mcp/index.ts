/**
 * MCP (Model Context Protocol) integration module.
 *
 * Provides the ``createUndoLogMcpServer()`` factory for exposing
 * UndoLog-wrapped tools as MCP tools over stdio transport.
 *
 * @module
 */

export { createUndoLogMcpServer, connectStdio } from "./server.js";
export type {
  UndoLogMcpToolRegistration,
  UndoLogMcpServerOptions,
} from "./server.js";
