/** Unit tests for the TypeScript SDK test helper module.
 *
 * Covers the in-memory mock server (proxy MCP protocol: tool call, commit,
 * fail, approve, reject, get, clear, and unknown paths), the canonical JSON
 * and signature parity assertion helpers, and the session/effect factory
 * functions.
 *
 * @module
 */

import { describe, expect, it } from "vitest";
import { UndoLogClient } from "../../src/client.js";
import { AwaitingApprovalError, NotFoundError, ValidationError } from "../../src/errors.js";
import type { EffectRecord } from "../../src/client.js";
import { callSignature } from "../../src/signature.js";
import { ToolTier } from "../../src/tier.js";
import type { MockEffectEntry } from "../../src/testing/index.js";
import {
  assertCanonicalJsonParity,
  assertSignatureParity,
  createMockEffect,
  createMockSession,
  mockServer,
} from "../../src/testing/index.js";

const DEFAULT_SESSION_ID = "00000000-0000-0000-0000-000000000000";
const OVERRIDE_SESSION_ID = "550e8400-e29b-41d4-a716-446655440000";

/** Route a proxy MCP tool call through the mock server.
 *
 * Mirrors the real proxy protocol: a snake_case ``/mcp/tool_call`` body with
 * ``session_id``, ``tool_name``, ``step_index``, and ``args``. No tier is
 * sent by the client; the mock resolves it server-side from its registry.
 *
 * @param server - Mock server to route the request through.
 * @param overrides - Body fields to merge over the default intercept payload.
 * @returns The raw proxy response (``executed`` or ``pending_approval``).
 */
function toolCall(
  server: ReturnType<typeof mockServer>,
  overrides: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return server.httpClient.request<Record<string, unknown>>({
    method: "POST",
    path: "/mcp/tool_call",
    body: {
      session_id: DEFAULT_SESSION_ID,
      tool_name: "test_tool",
      step_index: 0,
      args: {},
      ...overrides,
    },
  });
}

/** Look up the stored entry for an effect in the mock server.
 *
 * @param server - Mock server whose effects map is inspected.
 * @param effectId - Effect identifier returned by the tool call.
 * @returns The stored mock entry, or undefined when absent.
 */
function storedEntry(
  server: ReturnType<typeof mockServer>,
  effectId: string,
): MockEffectEntry | undefined {
  return server.effects.get(effectId);
}

/** Return the sole stored approval, asserting the server holds exactly one.
 *
 * Tests that exercise the approval workflow create a single approval per
 * tool call, so this helper validates that invariant and narrows the type.
 *
 * @param server - Mock server whose approvals map is inspected.
 * @returns The single approval entry stored in the server.
 */
function soleApproval(
  server: ReturnType<typeof mockServer>,
): { approvalId: string; effectId: string } {
  const entries = Array.from(server.approvals.values());
  const first = entries[0];
  if (first === undefined) {
    throw new Error("Mock server expected exactly one approval");
  }
  return first;
}

describe("mockServer", () => {
  it("creates a server with empty effects and approvals maps", () => {
    const server = mockServer();
    expect(server.effects).toBeInstanceOf(Map);
    expect(server.effects.size).toBe(0);
    expect(server.approvals).toBeInstanceOf(Map);
    expect(server.approvals.size).toBe(0);
  });

  it("httpClient exposes a request method", () => {
    const server = mockServer();
    expect(server.httpClient.request).toBeInstanceOf(Function);
  });

  it("clear() removes all effects and approvals", async () => {
    const server = mockServer({
      tools: {
        test_tool: ToolTier.Irreversible,
        second_tool: ToolTier.Irreversible,
      },
    });
    await toolCall(server);
    await toolCall(server, { tool_name: "second_tool" });
    expect(server.effects.size).toBe(2);
    expect(server.approvals.size).toBe(2);
    server.clear();
    expect(server.effects.size).toBe(0);
    expect(server.approvals.size).toBe(0);
  });
});

describe("mockServer /mcp/tool_call", () => {
  it("returns executed with an effect id for a compensable tool", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const resp = await toolCall(server, { args: { to: "bob" } });
    expect(resp.status).toBe("executed");
    expect(typeof resp.effect_id).toBe("string");
    expect(resp.approval_id).toBeUndefined();
  });

  it("stores the effect with a committed status and resolved tier", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const resp = await toolCall(server, { tool_name: "test_tool", args: { to: "bob" } });
    const entry = storedEntry(server, resp.effect_id as string);
    expect(entry).toBeDefined();
    expect(entry?.status).toBe("committed");
    expect(entry?.tier).toBe(ToolTier.Compensable);
    expect(entry?.toolName).toBe("test_tool");
    expect(entry?.args).toEqual({ to: "bob" });
  });

  it("resolves the tier from the registry rather than the request body", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Safe },
    });
    // The client sends no tier; even a hostile tier field in the body must be
    // ignored because the proxy owns tier resolution.
    const resp = await toolCall(server, { tier: ToolTier.Irreversible });
    expect(storedEntry(server, resp.effect_id as string)?.tier).toBe(ToolTier.Safe);
  });

  it("returns pending_approval for an irreversible tool", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Irreversible },
    });
    const resp = await toolCall(server);
    expect(resp.status).toBe("pending_approval");
    expect(typeof resp.approval_id).toBe("string");
    expect(resp.retry_after).toBe(5);
    const entry = storedEntry(server, resp.effect_id as string);
    expect(entry).toBeUndefined();
    expect(server.approvals.size).toBe(1);
  });

  it("stores a pending effect for an irreversible tool", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Irreversible },
    });
    await toolCall(server);
    const approval = soleApproval(server);
    const entry = storedEntry(server, approval.effectId);
    expect(entry?.status).toBe("pending");
    expect(entry?.tier).toBe(ToolTier.Irreversible);
  });

  it("falls back to the default tier for unknown tools", async () => {
    const server = mockServer({ defaultTier: ToolTier.Safe });
    const resp = await toolCall(server, { tool_name: "unregistered_tool" });
    expect(storedEntry(server, resp.effect_id as string)?.tier).toBe(ToolTier.Safe);
  });

  it("defaults unknown tools to Compensable", async () => {
    const server = mockServer();
    const resp = await toolCall(server, { tool_name: "unregistered_tool" });
    expect(storedEntry(server, resp.effect_id as string)?.tier).toBe(ToolTier.Compensable);
  });

  it("throws ValidationError when session_id is missing", async () => {
    const server = mockServer();
    await expect(toolCall(server, { session_id: undefined })).rejects.toThrow(ValidationError);
  });

  it("throws ValidationError when tool_name is missing", async () => {
    const server = mockServer();
    await expect(toolCall(server, { tool_name: "" })).rejects.toThrow(ValidationError);
  });

  it("throws TypeError for a non-POST method", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({ method: "GET", path: "/mcp/tool_call" }),
    ).rejects.toThrow(TypeError);
  });
});

describe("mockServer commit", () => {
  it("transitions a stored effect to committed", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Irreversible },
    });
    await toolCall(server);
    const approval = soleApproval(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "PUT",
      path: `/effects/${approval.effectId}/commit`,
      body: {},
    });
    expect(result.status).toBe("committed");
    expect(storedEntry(server, approval.effectId)?.status).toBe("committed");
  });

  it("throws NotFoundError for an unknown effectId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({
        method: "PUT",
        path: "/effects/unknown_id/commit",
        body: {},
      }),
    ).rejects.toThrow(NotFoundError);
  });
});

describe("mockServer fail", () => {
  it("transitions a stored effect to failed with an error message", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const resp = await toolCall(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "PUT",
      path: `/effects/${resp.effect_id}/fail`,
      body: { error: "something broke" },
    });
    expect(result.status).toBe("failed");
    expect(storedEntry(server, resp.effect_id as string)?.error).toBe("something broke");
  });

  it("throws NotFoundError for an unknown effectId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({
        method: "PUT",
        path: "/effects/unknown_id/fail",
        body: { error: "boom" },
      }),
    ).rejects.toThrow(NotFoundError);
  });
});

describe("mockServer approve", () => {
  it("transitions a pending effect to approved", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Irreversible },
    });
    const resp = await toolCall(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: `/approvals/${resp.approval_id}/approve`,
      body: {},
    });
    expect(result.status).toBe("approved");
    const approval = soleApproval(server);
    expect(storedEntry(server, approval.effectId)?.status).toBe("approved");
  });

  it("throws NotFoundError for an unknown approvalId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({
        method: "POST",
        path: "/approvals/unknown_id/approve",
        body: {},
      }),
    ).rejects.toThrow(NotFoundError);
  });
});

describe("mockServer reject", () => {
  it("transitions a pending effect to rejected with a reason", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Irreversible },
    });
    const resp = await toolCall(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: `/approvals/${resp.approval_id}/reject`,
      body: { reason: "not needed" },
    });
    expect(result.status).toBe("rejected");
    const approval = soleApproval(server);
    expect(storedEntry(server, approval.effectId)?.reason).toBe("not needed");
  });

  it("throws NotFoundError for an unknown approvalId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({
        method: "POST",
        path: "/approvals/unknown_id/reject",
        body: { reason: "nope" },
      }),
    ).rejects.toThrow(NotFoundError);
  });
});

describe("mockServer get", () => {
  it("returns a stored effect record via /v1/effects/{id}", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const resp = await toolCall(server);
    const record = await server.httpClient.request<EffectRecord>({
      method: "GET",
      path: `/v1/effects/${resp.effect_id}`,
    });
    expect(record.effectId).toBe(resp.effect_id);
    expect(record.status).toBe("committed");
  });

  it("returns a session record via /v1/sessions/{id}", async () => {
    const server = mockServer();
    const record = await server.httpClient.request<EffectRecord>({
      method: "GET",
      path: `/v1/sessions/${DEFAULT_SESSION_ID}`,
    });
    expect(record.sessionId).toBe(DEFAULT_SESSION_ID);
  });

  it("throws NotFoundError for an unknown effectId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({ method: "GET", path: "/v1/effects/unknown_id" }),
    ).rejects.toThrow(NotFoundError);
  });
});

describe("mockServer unknown path", () => {
  it("throws TypeError for an unrecognised path", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({ method: "GET", path: "/v1/unknown" }),
    ).rejects.toThrow(TypeError);
  });
});

describe("UndoLogClient through mock server", () => {
  function clientWith(server: ReturnType<typeof mockServer>): UndoLogClient {
    return new UndoLogClient({
      baseUrl: "http://localhost",
      httpClient: server.httpClient,
    });
  }

  it("intercepts a compensable tool end to end", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const client = clientWith(server);

    const effect = await client.intercept({
      toolName: "test_tool",
      args: { to: "bob" },
      tier: ToolTier.Compensable,
      sessionId: DEFAULT_SESSION_ID,
      stepIndex: 0,
    });

    expect(effect.status).toBe("pending");
    expect(server.effects.size).toBe(1);
    const entry = storedEntry(server, effect.effectId);
    expect(entry?.toolName).toBe("test_tool");
    expect(entry?.signature).toBe(effect.signature);
  });

  it("matches the client-computed signature with the server-side entry", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const client = clientWith(server);

    const effect = await client.intercept({
      toolName: "test_tool",
      args: { to: "bob" },
      tier: ToolTier.Compensable,
      sessionId: DEFAULT_SESSION_ID,
      stepIndex: 3,
    });

    const expected = callSignature(DEFAULT_SESSION_ID, 3, "test_tool", { to: "bob" });
    expect(effect.signature).toBe(expected);
    expect(storedEntry(server, effect.effectId)?.signature).toBe(expected);
  });

  it("throws AwaitingApprovalError for an irreversible tool", async () => {
    const server = mockServer({
      tools: { delete_user: ToolTier.Irreversible },
    });
    const client = clientWith(server);

    const err = await client
      .intercept({
        toolName: "delete_user",
        args: { userId: "42" },
        tier: ToolTier.Irreversible,
        sessionId: DEFAULT_SESSION_ID,
        stepIndex: 0,
      })
      .then(
        () => undefined,
        (e: unknown) => e,
      );

    expect(err).toBeInstanceOf(AwaitingApprovalError);
    const approvalErr = err as AwaitingApprovalError;
    expect(server.approvals.get(approvalErr.approvalId)).toBeDefined();
  });

  it("approves a pending irreversible effect end to end", async () => {
    const server = mockServer({
      tools: { delete_user: ToolTier.Irreversible },
    });
    const client = clientWith(server);

    const err = await client
      .intercept({
        toolName: "delete_user",
        args: { userId: "42" },
        tier: ToolTier.Irreversible,
        sessionId: DEFAULT_SESSION_ID,
        stepIndex: 0,
      })
      .then(
        () => undefined,
        (e: unknown) => e,
      );

    const approvalId = (err as AwaitingApprovalError).approvalId;
    const record = await client.approve(approvalId);
    expect(record.status).toBe("approved");
    const approval = soleApproval(server);
    expect(storedEntry(server, approval.effectId)?.status).toBe("approved");
  });

  it("fetches an effect record after intercept", async () => {
    const server = mockServer({
      tools: { test_tool: ToolTier.Compensable },
    });
    const client = clientWith(server);

    const effect = await client.intercept({
      toolName: "test_tool",
      args: {},
      tier: ToolTier.Compensable,
      sessionId: DEFAULT_SESSION_ID,
      stepIndex: 0,
    });

    const fetched = await client.getEffect(effect.effectId);
    expect(fetched.effectId).toBe(effect.effectId);
    expect(fetched.status).toBe("committed");
  });
});

describe("assertCanonicalJsonParity", () => {
  it("passes when canonical JSON matches", () => {
    const result = assertCanonicalJsonParity({ b: 1, a: 2 }, { a: 2, b: 1 });
    expect(result.pass).toBe(true);
  });

  it("fails when canonical JSON differs", () => {
    const result = assertCanonicalJsonParity({ a: 1 }, { a: 2 });
    expect(result.pass).toBe(false);
  });
});

describe("assertSignatureParity", () => {
  it("passes when signatures match", () => {
    const args = { to: "alice@example.com", subject: "Hello" };
    const expected = callSignature(OVERRIDE_SESSION_ID, 1, "send_email", args);
    const result = assertSignatureParity(OVERRIDE_SESSION_ID, 1, "send_email", args, expected);
    expect(result.pass).toBe(true);
  });

  it("fails when signatures differ", () => {
    const result = assertSignatureParity(
      OVERRIDE_SESSION_ID,
      1,
      "my_tool",
      { arg: 1 },
      "0000000000000000000000000000000000000000000000000000000000000000",
    );
    expect(result.pass).toBe(false);
  });
});

describe("createMockSession", () => {
  it("creates a session with the default UUID", () => {
    const session = createMockSession();
    expect(session.sessionId).toBe(DEFAULT_SESSION_ID);
  });

  it("accepts a sessionId override", () => {
    const session = createMockSession({ sessionId: OVERRIDE_SESSION_ID });
    expect(session.sessionId).toBe(OVERRIDE_SESSION_ID);
  });
});

describe("createMockEffect", () => {
  it("creates an effect with sensible defaults", () => {
    const effect = createMockEffect();
    expect(effect.effectId).toBe("00000000-0000-0000-0000-000000000001");
    expect(effect.status).toBe("pending");
    expect(effect.tier).toBe(ToolTier.Safe);
  });

  it("accepts partial overrides", () => {
    const effect = createMockEffect({ status: "committed", toolName: "my_tool" });
    expect(effect.status).toBe("committed");
    expect(effect.toolName).toBe("my_tool");
    expect(effect.effectId).toBe("00000000-0000-0000-0000-000000000001");
  });
});
