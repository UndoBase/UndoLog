/** Unit tests for the TypeScript SDK test helper module.
 *
 * Covers the in-memory mock server (intercept, commit, fail, approve, reject,
 * clear, and unknown paths), the canonical JSON and signature parity assertion
 * helpers, and the session/effect factory functions.
 *
 * @module
 */

import { describe, expect, it } from "vitest";
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

/** Route an intercept request through the mock server.
 *
 * @param server - Mock server to route the request through.
 * @param overrides - Body fields to merge over the default intercept payload.
 * @returns The effect record produced by the mock server.
 */
function interceptEffect(
  server: ReturnType<typeof mockServer>,
  overrides: Record<string, unknown> = {},
): Promise<EffectRecord> {
  return server.httpClient.request({
    method: "POST",
    path: "/v1/effects/intercept",
    body: {
      sessionId: DEFAULT_SESSION_ID,
      stepIndex: 0,
      toolName: "test_tool",
      tier: ToolTier.Safe,
      ...overrides,
    },
  });
}

/** Look up the stored entry for an effect in the mock server.
 *
 * @param server - Mock server whose effects map is inspected.
 * @param effectId - Effect identifier returned by the intercept request.
 * @returns The stored mock entry, or undefined when absent.
 */
function storedEntry(
  server: ReturnType<typeof mockServer>,
  effectId: string,
): MockEffectEntry | undefined {
  return server.effects.get(effectId);
}

describe("mockServer", () => {
  it("creates a server with an empty effects map", () => {
    const server = mockServer();
    expect(server.effects).toBeInstanceOf(Map);
    expect(server.effects.size).toBe(0);
  });

  it("httpClient exposes a request method", () => {
    const server = mockServer();
    expect(server.httpClient.request).toBeInstanceOf(Function);
  });

  it("clear() removes all effects", async () => {
    const server = mockServer();
    await interceptEffect(server);
    await interceptEffect(server, { toolName: "second_tool" });
    expect(server.effects.size).toBe(2);
    server.clear();
    expect(server.effects.size).toBe(0);
  });
});

describe("mockServer intercept", () => {
  it("returns a pending effect record", async () => {
    const server = mockServer();
    const result = await interceptEffect(server);
    expect(result.status).toBe("pending");
    expect(result.effectId).toBeDefined();
  });

  it("stores the effect in the effects map", async () => {
    const server = mockServer();
    const result = await interceptEffect(server, { toolName: "store_test" });
    expect(server.effects.size).toBe(1);
    const entry = storedEntry(server, result.effectId);
    expect(entry).toBeDefined();
    expect(entry?.toolName).toBe("store_test");
  });

  it("throws TypeError for an invalid tier", async () => {
    const server = mockServer();
    await expect(
      interceptEffect(server, { tier: "not_a_valid_tier" }),
    ).rejects.toThrow(TypeError);
  });
});

describe("mockServer commit", () => {
  it("transitions a pending effect to committed", async () => {
    const server = mockServer();
    const interceptResult = await interceptEffect(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/commit",
      body: { effectId: interceptResult.effectId },
    });
    expect(result.status).toBe("committed");
    expect(storedEntry(server, interceptResult.effectId)?.status).toBe("committed");
  });

  it("throws TypeError for an unknown effectId", async () => {
    const server = mockServer();
    await expect(
      server.httpClient.request({
        method: "POST",
        path: "/v1/effects/commit",
        body: { effectId: "unknown_id" },
      }),
    ).rejects.toThrow(TypeError);
  });
});

describe("mockServer fail", () => {
  it("transitions a pending effect to failed with an error message", async () => {
    const server = mockServer();
    const interceptResult = await interceptEffect(server);
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/fail",
      body: { effectId: interceptResult.effectId, error: "something broke" },
    });
    expect(result.status).toBe("failed");
    expect(storedEntry(server, interceptResult.effectId)?.error).toBe("something broke");
  });
});

describe("mockServer approve", () => {
  it("transitions an effect to approved", async () => {
    const server = mockServer();
    const interceptResult = await interceptEffect(server, {
      tier: ToolTier.Irreversible,
    });
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/approve",
      body: { approvalId: interceptResult.effectId },
    });
    expect(result.status).toBe("approved");
  });
});

describe("mockServer reject", () => {
  it("transitions an effect to rejected with a reason", async () => {
    const server = mockServer();
    const interceptResult = await interceptEffect(server, {
      tier: ToolTier.Irreversible,
    });
    const result = await server.httpClient.request<EffectRecord>({
      method: "POST",
      path: "/v1/effects/reject",
      body: { approvalId: interceptResult.effectId, reason: "not needed" },
    });
    expect(result.status).toBe("rejected");
    expect(storedEntry(server, interceptResult.effectId)?.reason).toBe("not needed");
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
