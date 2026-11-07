import { describe, it, expect, vi } from "vitest";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import {
  UndoLogAdminClient,
  type DeadLetterRecord,
  type ListDeadLettersResponse,
} from "../../src/admin.js";
import { createUndoLogAdminMcpServer } from "../../src/mcp/admin.js";
import * as mcpModule from "../../src/mcp/index.js";
import * as rootModule from "../../src/index.js";
import type { HttpClient } from "../../src/http.js";
import {
  NotFoundError,
  PermissionError,
  ServerError,
  ValidationError,
} from "../../src/errors.js";

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

async function createLinkedClientServer(server: Server): Promise<Client> {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  const client = new Client(
    { name: "test-admin-mcp-client", version: "0.0.0" },
    { capabilities: {} },
  );
  await client.connect(clientTransport);
  return client;
}

const SAMPLE_UUID = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d";

function sampleRecord(overrides?: Partial<DeadLetterRecord>): DeadLetterRecord {
  return {
    deadLetterId: SAMPLE_UUID,
    orgId: "org-123",
    sessionId: "sess-456",
    effectId: "eff-789",
    undoId: "undo-101",
    compensationFn: "cancel_reservation",
    compensationVersion: "1.0.0",
    compensationArgs: { reservationId: "res-99" },
    errorMessage: "upstream 503",
    retryCount: 0,
    state: "failed",
    createdAt: "2026-11-07T12:00:00Z",
    updatedAt: "2026-11-07T12:00:00Z",
    ...overrides,
  };
}

describe("UndoLogAdminClient", () => {
  it("exports admin client and types from root and mcp modules", () => {
    expect(typeof rootModule.UndoLogAdminClient).toBe("function");
    expect(typeof mcpModule.createUndoLogAdminMcpServer).toBe("function");
  });

  it("throws ValidationError when baseUrl is missing or empty", () => {
    expect(() => new UndoLogAdminClient({ baseUrl: "" })).toThrow(ValidationError);
    expect(() => new UndoLogAdminClient(undefined as any)).toThrow(ValidationError);
  });

  it("lists dead letters and maps snake_case wire responses to camelCase", async () => {
    const mockHttp: HttpClient = {
      request: vi.fn().mockResolvedValue({
        records: [
          {
            dead_letter_id: SAMPLE_UUID,
            org_id: "org-1",
            session_id: "sess-1",
            effect_id: "eff-1",
            undo_id: "undo-1",
            compensation_fn: "rollback_tx",
            compensation_version: "2.0.0",
            compensation_args: { amount: 50 },
            error_message: "timeout",
            retry_count: 1,
            state: "failed",
            created_at: "2026-11-07T10:00:00Z",
            updated_at: "2026-11-07T10:05:00Z",
          },
        ],
        count: 1,
      }),
    };

    const client = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      apiKey: "admin-key",
      httpClient: mockHttp,
    });

    const result = await client.listDeadLetters({ state: "failed" });
    expect(mockHttp.request).toHaveBeenCalledWith({
      method: "GET",
      path: "/api/v1/admin/dead-letters",
      query: { state: "failed" },
    });
    expect(result.count).toBe(1);
    expect(result.records[0]).toEqual({
      deadLetterId: SAMPLE_UUID,
      orgId: "org-1",
      sessionId: "sess-1",
      effectId: "eff-1",
      undoId: "undo-1",
      compensationFn: "rollback_tx",
      compensationVersion: "2.0.0",
      compensationArgs: { amount: 50 },
      errorMessage: "timeout",
      retryCount: 1,
      state: "failed",
      createdAt: "2026-11-07T10:00:00Z",
      updatedAt: "2026-11-07T10:05:00Z",
    });
  });

  it("retries a dead-letter record and returns updated record", async () => {
    const mockHttp: HttpClient = {
      request: vi.fn().mockResolvedValue({
        dead_letter_id: SAMPLE_UUID,
        org_id: "org-1",
        session_id: "sess-1",
        effect_id: "eff-1",
        undo_id: "undo-1",
        compensation_fn: "rollback_tx",
        compensation_version: "1.0.0",
        compensation_args: {},
        error_message: "timeout",
        retry_count: 1,
        state: "retrying",
        created_at: "2026-11-07T10:00:00Z",
        updated_at: "2026-11-07T10:10:00Z",
      }),
    };

    const client = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: mockHttp,
    });

    const rec = await client.retryDeadLetter(SAMPLE_UUID);
    expect(mockHttp.request).toHaveBeenCalledWith({
      method: "POST",
      path: `/api/v1/admin/dead-letters/${SAMPLE_UUID}/retry`,
      body: {},
    });
    expect(rec.state).toBe("retrying");
    expect(rec.retryCount).toBe(1);
  });

  it("skips a dead-letter record and returns updated record", async () => {
    const mockHttp: HttpClient = {
      request: vi.fn().mockResolvedValue({
        dead_letter_id: SAMPLE_UUID,
        org_id: "org-1",
        session_id: "sess-1",
        effect_id: "eff-1",
        undo_id: "undo-1",
        compensation_fn: "rollback_tx",
        compensation_version: "1.0.0",
        compensation_args: {},
        error_message: "unrecoverable",
        retry_count: 0,
        state: "skipped",
        created_at: "2026-11-07T10:00:00Z",
        updated_at: "2026-11-07T10:15:00Z",
      }),
    };

    const client = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: mockHttp,
    });

    const rec = await client.skipDeadLetter(SAMPLE_UUID);
    expect(mockHttp.request).toHaveBeenCalledWith({
      method: "POST",
      path: `/api/v1/admin/dead-letters/${SAMPLE_UUID}/skip`,
      body: {},
    });
    expect(rec.state).toBe("skipped");
  });

  it("throws ValidationError when deadLetterId is empty in retry or skip", async () => {
    const client = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });

    await expect(client.retryDeadLetter("")).rejects.toThrow(ValidationError);
    await expect(client.skipDeadLetter("")).rejects.toThrow(ValidationError);
  });
});

describe("createUndoLogAdminMcpServer", () => {
  it("exposes all three admin tools with proper annotations and hints", async () => {
    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const { tools } = await mcpClient.listTools();
    expect(tools).toHaveLength(3);

    const listTool = tools.find((t) => t.name === "undolog_list_dead_letters");
    expect(listTool).toBeDefined();
    expect(listTool?.annotations?.readOnlyHint).toBe(true);
    expect(listTool?.annotations?.destructiveHint).toBe(false);
    expect(listTool?.inputSchema).toEqual({
      type: "object",
      properties: {
        state: {
          type: "string",
          enum: ["failed", "retrying", "skipped"],
          description: "Lifecycle state filter for dead-letter records. Defaults to failed.",
        },
      },
      additionalProperties: false,
    });

    const retryTool = tools.find((t) => t.name === "undolog_retry_dead_letter");
    expect(retryTool).toBeDefined();
    expect(retryTool?.annotations?.readOnlyHint).toBe(false);
    expect(retryTool?.annotations?.destructiveHint).toBe(true);
    expect(retryTool?.inputSchema).toEqual({
      type: "object",
      properties: {
        deadLetterId: {
          type: "string",
          description: "Unique UUID of the dead-letter record to retry.",
        },
      },
      required: ["deadLetterId"],
      additionalProperties: false,
    });

    const skipTool = tools.find((t) => t.name === "undolog_skip_dead_letter");
    expect(skipTool).toBeDefined();
    expect(skipTool?.annotations?.readOnlyHint).toBe(false);
    expect(skipTool?.annotations?.destructiveHint).toBe(true);
    expect(skipTool?.inputSchema).toEqual({
      type: "object",
      properties: {
        deadLetterId: {
          type: "string",
          description: "Unique UUID of the dead-letter record to mark as skipped.",
        },
      },
      required: ["deadLetterId"],
      additionalProperties: false,
    });
  });

  it("successfully executes undolog_list_dead_letters", async () => {
    const expectedResponse: ListDeadLettersResponse = {
      records: [sampleRecord()],
      count: 1,
    };
    const listSpy = vi.fn().mockResolvedValue(expectedResponse);

    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });
    adminClient.listDeadLetters = listSpy;

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const result = asTextResult(
      await mcpClient.callTool({
        name: "undolog_list_dead_letters",
        arguments: { state: "failed" },
      }),
    );

    expect(result.isError).toBeUndefined();
    expect(listSpy).toHaveBeenCalledWith({ state: "failed" });
    const parsed = JSON.parse(textFrom(result));
    expect(parsed.count).toBe(1);
    expect(parsed.records[0].deadLetterId).toBe(SAMPLE_UUID);
  });

  it("successfully executes undolog_retry_dead_letter", async () => {
    const retriedRecord = sampleRecord({ state: "retrying", retryCount: 1 });
    const retrySpy = vi.fn().mockResolvedValue(retriedRecord);

    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });
    adminClient.retryDeadLetter = retrySpy;

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const result = asTextResult(
      await mcpClient.callTool({
        name: "undolog_retry_dead_letter",
        arguments: { deadLetterId: SAMPLE_UUID },
      }),
    );

    expect(result.isError).toBeUndefined();
    expect(retrySpy).toHaveBeenCalledWith(SAMPLE_UUID);
    const parsed = JSON.parse(textFrom(result));
    expect(parsed.state).toBe("retrying");
    expect(parsed.retryCount).toBe(1);
  });

  it("successfully executes undolog_skip_dead_letter", async () => {
    const skippedRecord = sampleRecord({ state: "skipped" });
    const skipSpy = vi.fn().mockResolvedValue(skippedRecord);

    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });
    adminClient.skipDeadLetter = skipSpy;

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const result = asTextResult(
      await mcpClient.callTool({
        name: "undolog_skip_dead_letter",
        arguments: { deadLetterId: SAMPLE_UUID },
      }),
    );

    expect(result.isError).toBeUndefined();
    expect(skipSpy).toHaveBeenCalledWith(SAMPLE_UUID);
    const parsed = JSON.parse(textFrom(result));
    expect(parsed.state).toBe("skipped");
  });

  it("returns validation error on malformed inputs without calling the client", async () => {
    const listSpy = vi.fn();
    const retrySpy = vi.fn();
    const skipSpy = vi.fn();

    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });
    adminClient.listDeadLetters = listSpy;
    adminClient.retryDeadLetter = retrySpy;
    adminClient.skipDeadLetter = skipSpy;

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    // Invalid state in list
    const invalidListResult = asTextResult(
      await mcpClient.callTool({
        name: "undolog_list_dead_letters",
        arguments: { state: "invalid-state" },
      }),
    );
    expect(invalidListResult.isError).toBe(true);
    expect(textFrom(invalidListResult)).toContain("Validation error");
    expect(listSpy).not.toHaveBeenCalled();

    // Missing deadLetterId in retry
    const missingIdResult = asTextResult(
      await mcpClient.callTool({
        name: "undolog_retry_dead_letter",
        arguments: {},
      }),
    );
    expect(missingIdResult.isError).toBe(true);
    expect(textFrom(missingIdResult)).toContain("Validation error");
    expect(retrySpy).not.toHaveBeenCalled();

    // Malformed UUID in retry
    const malformedIdResult = asTextResult(
      await mcpClient.callTool({
        name: "undolog_retry_dead_letter",
        arguments: { deadLetterId: "not-a-valid-uuid" },
      }),
    );
    expect(malformedIdResult.isError).toBe(true);
    expect(textFrom(malformedIdResult)).toContain("valid UUID");
    expect(retrySpy).not.toHaveBeenCalled();

    // Malformed UUID in skip
    const malformedSkipResult = asTextResult(
      await mcpClient.callTool({
        name: "undolog_skip_dead_letter",
        arguments: { deadLetterId: "12345" },
      }),
    );
    expect(malformedSkipResult.isError).toBe(true);
    expect(textFrom(malformedSkipResult)).toContain("valid UUID");
    expect(skipSpy).not.toHaveBeenCalled();
  });

  it("translates proxy 4xx and 5xx errors to tool errors with isError: true", async () => {
    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });

    // 404 Not Found error
    adminClient.retryDeadLetter = vi
      .fn()
      .mockRejectedValue(
        new NotFoundError("dead_letter", SAMPLE_UUID, "dead-letter record not found"),
      );

    // 403 Forbidden error
    adminClient.listDeadLetters = vi
      .fn()
      .mockRejectedValue(new PermissionError("member key cannot access admin routes"));

    // 503 Server Error
    adminClient.skipDeadLetter = vi
      .fn()
      .mockRejectedValue(new ServerError(503, "engine unavailable"));

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const res404 = asTextResult(
      await mcpClient.callTool({
        name: "undolog_retry_dead_letter",
        arguments: { deadLetterId: SAMPLE_UUID },
      }),
    );
    expect(res404.isError).toBe(true);
    expect(textFrom(res404)).toContain("dead-letter record not found");

    const res403 = asTextResult(
      await mcpClient.callTool({
        name: "undolog_list_dead_letters",
        arguments: {},
      }),
    );
    expect(res403.isError).toBe(true);
    expect(textFrom(res403)).toContain("member key cannot access admin routes");

    const res503 = asTextResult(
      await mcpClient.callTool({
        name: "undolog_skip_dead_letter",
        arguments: { deadLetterId: SAMPLE_UUID },
      }),
    );
    expect(res503.isError).toBe(true);
    expect(textFrom(res503)).toContain("engine unavailable");
  });

  it("verifies effect logging client is never invoked and tools are not wrapped", async () => {
    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: {
        request: vi.fn().mockResolvedValue({ records: [], count: 0 }),
      },
    });

    // Mock an effect client to ensure none of its methods are called
    const effectClientMock = {
      intercept: vi.fn(),
      commit: vi.fn(),
      fail: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
    };

    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const result = asTextResult(
      await mcpClient.callTool({
        name: "undolog_list_dead_letters",
        arguments: {},
      }),
    );
    expect(result.isError).toBeUndefined();

    expect(effectClientMock.intercept).not.toHaveBeenCalled();
    expect(effectClientMock.commit).not.toHaveBeenCalled();
    expect(effectClientMock.fail).not.toHaveBeenCalled();
    expect(effectClientMock.approve).not.toHaveBeenCalled();
    expect(effectClientMock.reject).not.toHaveBeenCalled();
  });

  it("returns error for unknown tool call", async () => {
    const adminClient = new UndoLogAdminClient({
      baseUrl: "http://localhost:8080",
      httpClient: { request: vi.fn() },
    });
    const server = createUndoLogAdminMcpServer(adminClient);
    const mcpClient = await createLinkedClientServer(server);

    const result = asTextResult(
      await mcpClient.callTool({
        name: "unknown_admin_tool",
        arguments: {},
      }),
    );
    expect(result.isError).toBe(true);
    expect(textFrom(result)).toContain("Unknown tool: unknown_admin_tool");
  });
});
