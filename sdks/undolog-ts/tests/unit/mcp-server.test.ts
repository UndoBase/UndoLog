import { describe, it, expect, vi } from "vitest";
import { createUndoLogMcpServer } from "../../src/mcp/server.js";
import { UndoLogClient } from "../../src/client.js";
import { AwaitingApprovalError } from "../../src/errors.js";
import { ToolTier } from "../../src/tier.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";

async function createLinkedClientServer(
  server: ReturnType<typeof createUndoLogMcpServer>,
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

describe("UndoLogMcpServer", () => {
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
    expect(tools[0].name).toBe("test_tool");
    expect(tools[0].description).toBe("A test tool");
    expect(tools[0].inputSchema).toEqual({
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
    expect(result.content[0]).toMatchObject({ type: "text" });
    expect(JSON.parse(result.content[0].text as string)).toEqual({
      result: "hello",
    });
    expect(fn).toHaveBeenCalledWith({ name: "world" }, undefined);
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
    const text = result.content[0].text as string;
    expect(text).toMatch(/Unknown tool/);
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
    const text = result.content[0].text as string;
    expect(text).toBe("boom");
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
    const text = result.content[0].text as string;
    const parsed = JSON.parse(text);
    expect(parsed).toMatchObject({
      type: "approval_required",
      toolName: "danger_tool",
      approvalId: "approval-001",
      message: "Manual approval needed",
    });
  });

  it("creates a Server instance", async () => {
    const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    const server = createUndoLogMcpServer(client, []);
    expect(server).toBeDefined();
    expect(typeof server.connect).toBe("function");
  });
});
