/** Live-stack integration tests for the UndoLog TypeScript SDK.
 *
 * Exercises the full UndoLog stack (postgres + engine + proxy + mock tool
 * server) end-to-end through the SDK client and proxy HTTP API. Verifies
 * effect lifecycle transitions (intercept, commit, fail, replay, approval)
 * against the live stack.
 *
 * Prerequisites:
 * - UndoLog stack running via ``docker compose up -d postgres engine tool-server proxy``.
 * - ``UNDOLOG_PROXY_URL`` or default ``http://localhost:8080``.
 * - ``UNDOLOG_API_KEY`` or default ``dev-key``.
 *
 * @module
 */

import { describe, it, expect, beforeAll } from "vitest";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { UndoLogSession, runWithSession } from "../../src/session.js";
import { wrapTool } from "../../src/decorators.js";
import { ToolTier } from "../../src/tier.js";
import { AwaitingApprovalError } from "../../src/errors.js";

// ── Configuration helpers ──────────────────────────────────────────────────

function proxyUrl(): string {
  return process.env.UNDOLOG_PROXY_URL ?? "http://localhost:8080";
}

function apiKey(): string | undefined {
  return process.env.UNDOLOG_API_KEY ?? "dev-key";
}

function orgId(): string {
  return process.env.UNDOLOG_ORG_ID ?? "org_demo";
}

function proxyHeaders(): Record<string, string> {
  const hdrs: Record<string, string> = {
    "X-UndoLog-Org-Id": orgId(),
    "Content-Type": "application/json",
  };
  const key = apiKey();
  if (key !== undefined) {
    hdrs["X-Api-Key"] = key;
  }
  return hdrs;
}

function makeClient(): UndoLogClient {
  return new UndoLogClient({
    baseUrl: proxyUrl(),
    apiKey: apiKey(),
    headers: { "X-UndoLog-Org-Id": orgId() },
  });
}

async function proxyPost(
  path: string,
  body: Record<string, unknown>,
  options?: { expectedStatus?: number },
): Promise<{ status: number; data: Record<string, unknown> }> {
  const resp = await fetch(`${proxyUrl()}${path}`, {
    method: "POST",
    headers: proxyHeaders(),
    body: JSON.stringify(body),
  });
  const data = (await resp.json()) as Record<string, unknown>;
  if (options?.expectedStatus !== undefined) {
    expect(resp.status).toBe(options.expectedStatus);
  }
  return { status: resp.status, data };
}

async function proxyPostRaw(
  path: string,
  body: Record<string, unknown>,
): Promise<Response> {
  return fetch(`${proxyUrl()}${path}`, {
    method: "POST",
    headers: proxyHeaders(),
    body: JSON.stringify(body),
  });
}

// ── Prerequisite check ─────────────────────────────────────────────────────

let stackAvailable = false;

beforeAll(async () => {
  try {
    const resp = await fetch(`${proxyUrl()}/health`, {
      signal: AbortSignal.timeout(5000),
    });
    stackAvailable = resp.status === 200;
  } catch {
    stackAvailable = false;
  }
}, 10000);

function ensureStack(): void {
  if (!stackAvailable) {
    throw new Error(
      "UndoLog stack not running (docker compose up -d postgres engine tool-server proxy)",
    );
  }
}

// ── Tool registration ──────────────────────────────────────────────────────

describe("tool registration", { timeout: 30000 }, () => {
  it("wraps a tool function and runs the full effect lifecycle through the live proxy", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    const tool = wrapTool(client, {
      name: "integration_test_tool",
      tier: ToolTier.Compensable,
      fn: async (args: Record<string, unknown>) => ({ received: args, ok: true }),
      compensation: { fnName: "compensate_integration_test" },
    });

    await runWithSession(session, async () => {
      const result = await tool({ input: "hello" });
      expect(result).toEqual({ received: { input: "hello" }, ok: true });
    });
  });

  it("wraps a SAFE tier tool and bypasses effect registration", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    const tool = wrapTool(client, {
      name: "safe_test_tool",
      tier: ToolTier.Safe,
      fn: async (args: Record<string, unknown>) => ({ bypassed: true, ...args }),
    });

    await runWithSession(session, async () => {
      const result = await tool({ value: 42 });
      expect(result).toEqual({ bypassed: true, value: 42 });
    });
  });
});

// ── Intercept + Commit ─────────────────────────────────────────────────────

describe("intercept and commit", { timeout: 30000 }, () => {
  it("intercepts a COMPENSABLE tool and returns a pending effect record", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();
    const sessionId = session.sessionId;

    await runWithSession(session, async () => {
      const effect = await client.intercept({
        toolName: "send_email",
        args: { to: "alice@example.com", subject: "Hello" },
        tier: ToolTier.Compensable,
      });

      expect(effect).toBeDefined();
      expect(effect.effectId).toBeDefined();
      expect(effect.status).toBe("pending");
      expect(effect.toolName).toBe("send_email");
      expect(effect.sessionId).toBe(sessionId);
      expect(effect.tier).toBe(ToolTier.Compensable);
    });
  });

  it("commits a pending effect and transitions to committed", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    await runWithSession(session, async () => {
      const effect = await client.intercept({
        toolName: "send_email",
        args: { to: "bob@example.com", subject: "Commit test" },
        tier: ToolTier.Compensable,
      });

      const committed = await client.commit(effect.effectId);
      expect(committed.status).toBe("committed");
      expect(committed.effectId).toBe(effect.effectId);
    });
  });

  it("intercepts with compensation descriptor included", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    await runWithSession(session, async () => {
      const effect = await client.intercept({
        toolName: "charge_payment",
        args: { amount: 100, currency: "USD" },
        tier: ToolTier.Compensable,
        compensation: { fnName: "refund_payment" },
      });

      expect(effect.status).toBe("pending");

      const committed = await client.commit(effect.effectId);
      expect(committed.status).toBe("committed");
    });
  });
});

// ── Fail ───────────────────────────────────────────────────────────────────

describe("fail", { timeout: 30000 }, () => {
  it("fails a pending effect and transitions to failed status", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    await runWithSession(session, async () => {
      const effect = await client.intercept({
        toolName: "send_email",
        args: { to: "carol@example.com", subject: "Fail test" },
        tier: ToolTier.Compensable,
      });

      const failed = await client.fail(effect.effectId, "Intentional failure for testing");
      expect(failed.status).toBe("failed");
      expect(failed.effectId).toBe(effect.effectId);
    });
  });

  it("fails an already committed effect gracefully", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    await runWithSession(session, async () => {
      const effect = await client.intercept({
        toolName: "send_email",
        args: { to: "dave@example.com", subject: "Double transition" },
        tier: ToolTier.Compensable,
      });

      await client.commit(effect.effectId);
      const failed = await client.fail(effect.effectId, "Attempted fail after commit");
      expect(failed.status).toBe("failed");
    });
  });
});

// ── Approval lifecycle ─────────────────────────────────────────────────────

describe("approval lifecycle", { timeout: 30000 }, () => {
  it("IRREVERSIBLE tool raises AwaitingApprovalError", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    await runWithSession(session, async () => {
      let thrownError: AwaitingApprovalError | undefined;

      try {
        await client.intercept({
          toolName: "escalate_case",
          args: { ticket_id: "TKT-42", reason: "Integration test escalation" },
          tier: ToolTier.Irreversible,
        });
      } catch (err: unknown) {
        if (err instanceof AwaitingApprovalError) {
          thrownError = err;
        } else {
          throw err;
        }
      }

      expect(thrownError).toBeDefined();
      expect(thrownError?.toolName).toBe("escalate_case");
      expect(thrownError?.approvalId).toBeDefined();
      expect(thrownError?.args).toEqual({ ticket_id: "TKT-42", reason: "Integration test escalation" });
    });
  });

  it("approves a pending IRREVERSIBLE effect via the proxy API", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    let approvalId: string | undefined;

    await runWithSession(session, async () => {
      try {
        await client.intercept({
          toolName: "escalate_case",
          args: { ticket_id: "TKT-100", reason: "Critical issue" },
          tier: ToolTier.Irreversible,
        });
      } catch (err: unknown) {
        if (err instanceof AwaitingApprovalError) {
          approvalId = err.approvalId;
        } else {
          throw err;
        }
      }

      expect(approvalId).toBeDefined();

      const { status, data } = await proxyPost(`/approvals/${approvalId}/approve`, {
        actor: "integration_test",
        note: "Auto-approved",
      });
      expect(status).toBe(200);
      expect(data.execution).toBe("committed");
    });
  });

  it("rejects a pending IRREVERSIBLE effect via the proxy API", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    let approvalId: string | undefined;

    await runWithSession(session, async () => {
      try {
        await client.intercept({
          toolName: "escalate_case",
          args: { ticket_id: "TKT-200", reason: "To be rejected" },
          tier: ToolTier.Irreversible,
        });
      } catch (err: unknown) {
        if (err instanceof AwaitingApprovalError) {
          approvalId = err.approvalId;
        } else {
          throw err;
        }
      }

      expect(approvalId).toBeDefined();

      const { status, data } = await proxyPost(`/approvals/${approvalId}/reject`, {
        actor: "integration_test",
        note: "Auto-rejected",
      });
      expect(status).toBe(200);
      expect(data.status).toBe("rejected");
    });
  });

  it("double approve returns 409 Conflict", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    let approvalId: string | undefined;

    await runWithSession(session, async () => {
      try {
        await client.intercept({
          toolName: "escalate_case",
          args: { ticket_id: "TKT-300", reason: "Double approve test" },
          tier: ToolTier.Irreversible,
        });
      } catch (err: unknown) {
        if (err instanceof AwaitingApprovalError) {
          approvalId = err.approvalId;
        } else {
          throw err;
        }
      }

      expect(approvalId).toBeDefined();

      const resp1 = await proxyPostRaw(`/approvals/${approvalId}/approve`, {
        actor: "integration_test",
        note: "First approve",
      });
      expect(resp1.status).toBe(200);
      const data1 = (await resp1.json()) as Record<string, unknown>;
      expect(data1.execution).toBe("committed");

      const resp2 = await proxyPostRaw(`/approvals/${approvalId}/approve`, {
        actor: "integration_test",
        note: "Double approve",
      });
      expect(resp2.status).toBe(409);
    });
  });

  it("double reject returns 409 Conflict", async () => {
    ensureStack();

    const client = makeClient();
    const session = new UndoLogSession();

    let approvalId: string | undefined;

    await runWithSession(session, async () => {
      try {
        await client.intercept({
          toolName: "escalate_case",
          args: { ticket_id: "TKT-400", reason: "Double reject test" },
          tier: ToolTier.Irreversible,
        });
      } catch (err: unknown) {
        if (err instanceof AwaitingApprovalError) {
          approvalId = err.approvalId;
        } else {
          throw err;
        }
      }

      expect(approvalId).toBeDefined();

      const resp1 = await proxyPostRaw(`/approvals/${approvalId}/reject`, {
        actor: "integration_test",
        note: "First reject",
      });
      expect(resp1.status).toBe(200);

      const resp2 = await proxyPostRaw(`/approvals/${approvalId}/reject`, {
        actor: "integration_test",
        note: "Double reject",
      });
      expect(resp2.status).toBe(409);
    });
  });
});

// ── Replay idempotency ─────────────────────────────────────────────────────

describe("replay idempotency", { timeout: 30000 }, () => {
  it("same session/step/tool/args returns replayed status on second call", async () => {
    ensureStack();

    const testSessionId = crypto.randomUUID();
    const args = { amount: 100, currency: "USD" };

    const payload = {
      session_id: testSessionId,
      tool_name: "charge_payment",
      tool_version: "1.0.0",
      step_index: 1,
      args,
    };

    const resp1 = await proxyPostRaw("/mcp/tool_call", payload);
    expect(resp1.status).toBe(200);
    const data1 = (await resp1.json()) as Record<string, unknown>;
    expect(data1.status).toBe("executed");
    const effectId1 = data1.effect_id as string;
    expect(effectId1).toBeDefined();

    const resp2 = await proxyPostRaw("/mcp/tool_call", payload);
    expect(resp2.status).toBe(200);
    const data2 = (await resp2.json()) as Record<string, unknown>;
    expect(data2.status).toBe("replayed");
    expect(data2.effect_id).toBe(effectId1);
  });

  it("different args produce a fresh execute, not a replay", async () => {
    ensureStack();

    const testSessionId = crypto.randomUUID();

    const payload1 = {
      session_id: testSessionId,
      tool_name: "charge_payment",
      tool_version: "1.0.0",
      step_index: 1,
      args: { amount: 100, currency: "USD" },
    };

    const payload2 = {
      session_id: testSessionId,
      tool_name: "charge_payment",
      tool_version: "1.0.0",
      step_index: 2,
      args: { amount: 200, currency: "EUR" },
    };

    const resp1 = await proxyPostRaw("/mcp/tool_call", payload1);
    expect(resp1.status).toBe(200);
    const data1 = (await resp1.json()) as Record<string, unknown>;
    expect(data1.status).toBe("executed");

    const resp2 = await proxyPostRaw("/mcp/tool_call", payload2);
    expect(resp2.status).toBe(200);
    const data2 = (await resp2.json()) as Record<string, unknown>;
    expect(data2.status).toBe("executed");
    expect(data2.effect_id).not.toBe(data1.effect_id);
  });
});

// ── Session isolation ──────────────────────────────────────────────────────

describe("session isolation", { timeout: 30000 }, () => {
  it("two separate sessions produce independent effect records", async () => {
    ensureStack();

    const client = makeClient();
    const sessionA = new UndoLogSession();
    const sessionB = new UndoLogSession();

    let effectA: EffectRecord | undefined;
    let effectB: EffectRecord | undefined;

    await runWithSession(sessionA, async () => {
      effectA = await client.intercept({
        toolName: "send_email",
        args: { to: "sessionA@example.com", subject: "Isolation" },
        tier: ToolTier.Compensable,
      });
      await client.commit(effectA.effectId);
    });

    await runWithSession(sessionB, async () => {
      effectB = await client.intercept({
        toolName: "send_email",
        args: { to: "sessionB@example.com", subject: "Isolation" },
        tier: ToolTier.Compensable,
      });
      await client.commit(effectB.effectId);
    });

    expect(effectA?.sessionId).toBe(sessionA.sessionId);
    expect(effectB?.sessionId).toBe(sessionB.sessionId);
    expect(effectA?.sessionId).not.toBe(effectB?.sessionId);
    expect(effectA?.effectId).not.toBe(effectB?.effectId);
  });
});
