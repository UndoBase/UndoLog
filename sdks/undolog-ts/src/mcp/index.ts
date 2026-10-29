/**
 * MCP (Model Context Protocol) integration module.
 *
 * Provides the ``createUndoLogMcpServer()`` factory,
 * ``registerUndologMcpTool()`` registration helper, and ``connectStdio()``
 * transport utility for exposing UndoLog-wrapped tools on MCP servers.
 *
 * @module
 */

export {
  createUndoLogMcpServer,
  registerUndologMcpTool,
  connectStdio,
} from "./server.js";
export type {
  UndoLogMcpToolRegistration,
  UndoLogMcpServerOptions,
  McpServerInstance,
} from "./server.js";
