import { describe, it, expect, vi } from "vitest";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import {
  createUndoLogMcpServer,
  registerUndologMcpTool,
  connectStdio,
} from "../../src/mcp/server.js";
import * as mcpModule from "../../src/mcp/index.js";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { AwaitingApprovalError } from "../../src/errors.js";
import { ToolTier } from "../../src/tier.js";

interface TextToolResult {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
}

function asTextResult(result: unknown): TextToolResult {
  return result as TextToolResult;
}

function textFrom(result: TextToolResult): string {
  const block = result.content[0];
  if (block?.type !== "text") {
    throw new Error("expected a single text content block");
  }
  return block.text;
}

async function createLinkedClientServer(
  server: Server,
): Promise<Client> {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const client = new Client(
    { name: "test-client", version: "0.0.0" },
    { capabilities: {} },
  );
  await client.connect(clientTransport);
  return client;
}

function mockEffect(overrides?: Partial<EffectRecord>): EffectRecord {
  return {
    effectId: "eff_mcp_001",
    sessionId: "sess_mcp_001",
    stepIndex: 1,
    toolName: "test_tool",
    signature: "sig_001",
    status: "pending",
    tier: ToolTier.Compensable,
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

describe("UndoLogMcpServer", () => {
  it("exports registration functions from mcp index module", () => {
    expect(typeof mcpModule.createUndoLogMcpServer).toBe("function");
    expect(typeof mcpModule.registerUndologMcpTool).toBe("function");
    expect(typeof mcpModule.connectStdio).toBe("function");
  });

  it("creates a server instance and returns tool descriptors via listTools", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "test_tool",
        description: "A test tool",
        inputSchema: {
          type: "object",
          properties: { foo: { type: "string" } },
          required: ["foo"],
        },
        tier: ToolTier.Safe,
        execute: async () => ({ ok: true }),
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(1);
    const [tool] = tools;
    expect(tool?.name).toBe("test_tool");
    expect(tool?.description).toBe("A test tool");
    expect(tool?.inputSchema).toEqual({
      type: "object",
      properties: { foo: { type: "string" } },
      required: ["foo"],
    });
  });

  it("returns text content for a valid tool call", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const fn = vi.fn().mockResolvedValue({ result: "hello" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "greet",
        description: "Greet someone",
        tier: ToolTier.Safe,
        execute: fn,
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "greet",
      arguments: { name: "world" },
    });

    expect(result.content).toHaveLength(1);
    expect(JSON.parse(textFrom(asTextResult(result)))).toEqual({
      result: "hello",
    });
    expect(fn).toHaveBeenCalledWith({ name: "world" });
  });

  it("returns isError for an unknown tool name", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, []);
    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "nonexistent",
      arguments: {},
    });
    expect(result.isError).toBe(true);
    expect(textFrom(asTextResult(result))).toMatch(/Unknown tool/);
  });

  it("returns isError when the tool function throws", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const fn = vi.fn().mockRejectedValue(new Error("boom"));
    const server = createUndoLogMcpServer(client, [
      {
        name: "failing_tool",
        description: "Always fails",
        tier: ToolTier.Safe,
        execute: fn,
      },
    ]);
    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "failing_tool",
      arguments: {},
    });
    expect(result.isError).toBe(true);
    expect(textFrom(asTextResult(result))).toBe("boom");
  });

  it("returns structured JSON for AwaitingApprovalError", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    vi.spyOn(client, "intercept").mockRejectedValue(
      new AwaitingApprovalError(
        "danger_tool",
        { target: "prod" },
        "approval-001",
        "Manual approval needed",
      ),
    );
    const server = createUndoLogMcpServer(client, [
      {
        name: "danger_tool",
        description: "Requires approval",
        tier: ToolTier.Irreversible,
        execute: async () => ({ ok: true }),
      },
    ]);
    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "danger_tool",
      arguments: { target: "prod" },
    });
    expect(result.isError).toBe(true);
    const parsed = JSON.parse(textFrom(asTextResult(result)));
    expect(parsed).toMatchObject({
      type: "approval_required",
      toolName: "danger_tool",
      approvalId: "approval-001",
      message: "Manual approval needed",
    });
  });

  it("exposes approvalId as extractable string field", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    vi.spyOn(client, "intercept").mockRejectedValue(
      new AwaitingApprovalError(
        "drop_table",
        { table: "users" },
        "approval-042",
        "Confirm deletion",
      ),
    );
    const server = createUndoLogMcpServer(client, [
      {
        name: "drop_table",
        description: "Drop a table",
        tier: ToolTier.Irreversible,
        execute: async () => ({ ok: true }),
      },
    ]);
    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "drop_table",
      arguments: { table: "users" },
    });
    const parsed = JSON.parse(textFrom(asTextResult(result)));
    expect(typeof parsed.approvalId).toBe("string");
    expect(parsed.approvalId).toBe("approval-042");
  });

  it("creates a Server instance", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, []);
    expect(server).toBeDefined();
    expect(typeof server.connect).toBe("function");
  });

  it("skips intercept for Safe tier tools", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const interceptSpy = vi.spyOn(client, "intercept");
    const fn = vi.fn().mockResolvedValue({ ok: true });
    const server = createUndoLogMcpServer(client, [
      {
        name: "safe_tool",
        description: "No approval needed",
        tier: ToolTier.Safe,
        execute: fn,
      },
    ]);
    const mcpClient = await createLinkedClientServer(server);
    await mcpClient.callTool({ name: "safe_tool", arguments: { key: "value" } });
    expect(interceptSpy).not.toHaveBeenCalled();
    expect(fn).toHaveBeenCalledWith({ key: "value" });
  });

  it("calls multiple tools independently", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const fnA = vi.fn().mockResolvedValue({ from: "a" });
    const fnB = vi.fn().mockResolvedValue({ from: "b" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "tool_a",
        description: "First tool",
        tier: ToolTier.Safe,
        execute: fnA,
      },
      {
        name: "tool_b",
        description: "Second tool",
        tier: ToolTier.Safe,
        execute: fnB,
      },
    ]);
    const mcpClient = await createLinkedClientServer(server);
    const resultA = await mcpClient.callTool({ name: "tool_a", arguments: {} });
    const resultB = await mcpClient.callTool({ name: "tool_b", arguments: {} });
    expect(JSON.parse(textFrom(asTextResult(resultA)))).toEqual({ from: "a" });
    expect(JSON.parse(textFrom(asTextResult(resultB)))).toEqual({ from: "b" });
    expect(fnA).toHaveBeenCalledTimes(1);
    expect(fnB).toHaveBeenCalledTimes(1);
  });
});

describe("registerUndologMcpTool", () => {
  it("registers tool definition directly on an MCP Server instance", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = new Server(
      { name: "custom-mcp-server", version: "1.0.0" },
      { capabilities: { tools: {} } },
    );

    registerUndologMcpTool(server, client, {
      name: "custom_tool",
      description: "Directly registered tool",
      tier: ToolTier.Safe,
      execute: async () => ({ registered: true }),
    });

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(1);
    expect(tools[0]?.name).toBe("custom_tool");
    expect(tools[0]?.description).toBe("Directly registered tool");

    const callResult = await mcpClient.callTool({
      name: "custom_tool",
      arguments: {},
    });
    expect(JSON.parse(textFrom(asTextResult(callResult)))).toEqual({
      registered: true,
    });
  });

  it("supports multiple tool registrations on the same server instance", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = new Server(
      { name: "multi-server", version: "1.0.0" },
      { capabilities: { tools: {} } },
    );

    registerUndologMcpTool(server, client, {
      name: "first_tool",
      tier: ToolTier.Safe,
      execute: async () => ({ num: 1 }),
    });

    registerUndologMcpTool(server, client, {
      name: "second_tool",
      tier: ToolTier.Safe,
      execute: async () => ({ num: 2 }),
    });

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(2);
    expect(tools.map((t) => t.name)).toEqual(["first_tool", "second_tool"]);
  });

  it("supports inverted argument order (client, server, reg)", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = new Server(
      { name: "inverted-order-server", version: "1.0.0" },
      { capabilities: { tools: {} } },
    );

    registerUndologMcpTool(client, server, {
      name: "inverted_tool",
      tier: ToolTier.Safe,
      execute: async () => ({ ok: true }),
    });

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(1);
    expect(tools[0]?.name).toBe("inverted_tool");
  });

  it("throws when passed an invalid server instance", () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    expect(() =>
      registerUndologMcpTool({} as any, client, {
        name: "bad_tool",
        tier: ToolTier.Safe,
        execute: async () => ({}),
      }),
    ).toThrow(/requires a valid MCP Server instance/);
  });

  it("unwraps higher-level McpServer instance via .server property", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const underlying = new Server(
      { name: "underlying-server", version: "1.0.0" },
      { capabilities: { tools: {} } },
    );
    const wrappedServerLike = { server: underlying };

    registerUndologMcpTool(wrappedServerLike as any, client, {
      name: "unwrapped_tool",
      tier: ToolTier.Safe,
      execute: async () => ({ unwrapped: true }),
    });

    const mcpClient = await createLinkedClientServer(underlying);
    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(1);
    expect(tools[0]?.name).toBe("unwrapped_tool");
  });
});

describe("Tier metadata and reversibility annotations", () => {
  it("correctly publishes Safe tier annotations and metadata in tool list", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "read_query",
        description: "Read query",
        tier: ToolTier.Safe,
        execute: async () => ({ count: 10 }),
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    const tool = tools[0] as any;

    expect(tool.name).toBe("read_query");
    expect(tool.annotations).toMatchObject({
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    });
    expect(tool._meta).toMatchObject({
      tier: "safe",
      reversibility: "safe",
    });
  });

  it("correctly publishes Compensable tier annotations and compensation metadata", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "transfer_funds",
        description: "Move balance",
        tier: ToolTier.Compensable,
        compensation: {
          fnName: "rollback_transfer",
          fnVersion: "1.0.0",
          maxRetries: 3,
        },
        execute: async () => ({ transferred: 50 }),
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    const tool = tools[0] as any;

    expect(tool.name).toBe("transfer_funds");
    expect(tool.annotations).toMatchObject({
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: false,
    });
    expect(tool._meta).toMatchObject({
      tier: "compensable",
      reversibility: "compensable",
      compensation: {
        fnName: "rollback_transfer",
        fnVersion: "1.0.0",
        maxRetries: 3,
      },
    });
  });

  it("correctly publishes Irreversible tier annotations", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "delete_prod_table",
        description: "Permanently drop table",
        tier: ToolTier.Irreversible,
        execute: async () => ({ dropped: true }),
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    const tool = tools[0] as any;

    expect(tool.name).toBe("delete_prod_table");
    expect(tool.annotations).toMatchObject({
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: false,
    });
    expect(tool._meta).toMatchObject({
      tier: "irreversible",
      reversibility: "irreversible",
    });
  });

  it("merges custom annotations and custom _meta seamlessly", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, [
      {
        name: "custom_annotated",
        tier: ToolTier.Safe,
        annotations: {
          title: "Custom Title",
          openWorldHint: true,
        },
        _meta: {
          auditTag: "security-audit-01",
        },
        execute: async () => ({ ok: true }),
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const { tools } = await mcpClient.listTools();
    const tool = tools[0] as any;

    expect(tool.annotations).toMatchObject({
      title: "Custom Title",
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: true,
    });
    expect(tool._meta).toMatchObject({
      tier: "safe",
      reversibility: "safe",
      auditTag: "security-audit-01",
    });
  });
});

describe("Interception order and transport verification", () => {
  it("executes interception before MCP tool handler invocation", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const executionEvents: string[] = [];

    vi.spyOn(client, "intercept").mockImplementation(async (params) => {
      executionEvents.push(`intercept:${params.toolName}`);
      return mockEffect({ toolName: params.toolName });
    });

    vi.spyOn(client, "commit").mockImplementation(async (effectId) => {
      executionEvents.push(`commit:${effectId}`);
      return mockEffect({ effectId, status: "committed" });
    });

    const handler = vi.fn().mockImplementation(async (args) => {
      executionEvents.push(`execute:${args.action}`);
      return { status: "success" };
    });

    const server = createUndoLogMcpServer(client, [
      {
        name: "modify_resource",
        tier: ToolTier.Compensable,
        execute: handler,
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "modify_resource",
      arguments: { action: "update" },
    });

    expect(JSON.parse(textFrom(asTextResult(result)))).toEqual({
      status: "success",
    });
    expect(executionEvents).toEqual([
      "intercept:modify_resource",
      "execute:update",
      "commit:eff_mcp_001",
    ]);
  });

  it("pauses execution and does not invoke handler when approval is required", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const handler = vi.fn().mockResolvedValue({ performed: true });

    vi.spyOn(client, "intercept").mockRejectedValue(
      new AwaitingApprovalError(
        "wipe_data",
        { confirmed: true },
        "appr-999",
        "Wipe requires senior human authorization",
      ),
    );

    const server = createUndoLogMcpServer(client, [
      {
        name: "wipe_data",
        tier: ToolTier.Irreversible,
        execute: handler,
      },
    ]);

    const mcpClient = await createLinkedClientServer(server);
    const result = await mcpClient.callTool({
      name: "wipe_data",
      arguments: { confirmed: true },
    });

    expect(result.isError).toBe(true);
    expect(handler).not.toHaveBeenCalled();

    const parsed = JSON.parse(textFrom(asTextResult(result)));
    expect(parsed).toEqual({
      type: "approval_required",
      toolName: "wipe_data",
      approvalId: "appr-999",
      message: "Wipe requires senior human authorization",
    });
  });

  it("connects to stdio transport via connectStdio helper", async () => {
    const server = new Server(
      { name: "stdio-server", version: "1.0.0" },
      { capabilities: {} },
    );
    const connectSpy = vi.spyOn(server, "connect").mockResolvedValue(undefined as never);
    await connectStdio(server);
    expect(connectSpy).toHaveBeenCalledTimes(1);
  });
});
