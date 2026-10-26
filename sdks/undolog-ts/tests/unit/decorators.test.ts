import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { wrapTool, type ToolDefinition } from "../../src/decorators.js";
import { ToolTier } from "../../src/tier.js";
import type { CompensationDescriptor } from "../../src/tier.js";
import { AwaitingApprovalError, MissingSessionError } from "../../src/errors.js";
import { UndoLogSession, runWithSession, getCurrentSession } from "../../src/session.js";

const NOW = new Date().toISOString();

function mockEffect(overrides?: Partial<EffectRecord>): EffectRecord {
  return {
    effectId: "eff_001",
    sessionId: "sess_abc",
    stepIndex: 1,
    toolName: "test_tool",
    signature: "sig_xxx",
    status: "pending",
    tier: ToolTier.Compensable,
    createdAt: NOW,
    ...overrides,
  };
}

function stubDef(overrides?: Partial<ToolDefinition>): ToolDefinition {
  return {
    name: "test_tool",
    tier: ToolTier.Compensable,
    fn: vi.fn().mockResolvedValue("ok"),
    ...overrides,
  } as ToolDefinition;
}

let client: UndoLogClient;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SAFE bypass", () => {
  it("executes fn directly without calling intercept or commit", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const fn = vi.fn().mockResolvedValue("safe_result");

    const tool = wrapTool(client, { name: "safe_tool", tier: ToolTier.Safe, fn });

    await expect(tool({ key: "val" })).resolves.toBe("safe_result");
    expect(fn).toHaveBeenCalledWith({ key: "val" }, undefined);
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates fn errors without contacting the server", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const fail = vi.spyOn(client, "fail");
    const fn = vi.fn().mockRejectedValue(new Error("safe_fail"));

    const tool = wrapTool(client, { name: "safe_tool", tier: ToolTier.Safe, fn });

    await expect(tool({})).rejects.toThrow("safe_fail");
    expect(intercept).not.toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });

  it("works with synchronous fn returning non-promise value", async () => {
    vi.spyOn(client, "intercept");
    const fn = vi.fn().mockReturnValue("sync_result");

    const tool = wrapTool(client, { name: "sync_tool", tier: ToolTier.Safe, fn });

    await expect(tool({})).resolves.toBe("sync_result");
  });
});

describe("Execute flow (Compensable)", () => {
  it("calls intercept, fn, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const fn = vi.fn().mockResolvedValue("executed");

    const tool = wrapTool(client, { name: "comp_tool", tier: ToolTier.Compensable, fn });

    await expect(tool({ arg: 1 })).resolves.toBe("executed");
    expect(intercept).toHaveBeenCalledWith({
      toolName: "comp_tool",
      args: { arg: 1 },
      tier: ToolTier.Compensable,
    });
    expect(fn).toHaveBeenCalledWith({ arg: 1 }, undefined);
    expect(commit).toHaveBeenCalledWith("eff_001");
  });

  it("calls fail when fn throws and re-throws the original error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const fn = vi.fn().mockRejectedValue(new Error("exec_fail"));

    const tool = wrapTool(client, { name: "failing_tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes error message as string to fail when fn throws a non-Error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const fn = vi.fn().mockRejectedValue("string_error");

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).rejects.toBe("string_error");
    expect(fail).toHaveBeenCalledWith("eff_001", "string_error");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const compensation: CompensationDescriptor = { fnName: "undo_comp", args: { x: 1 } };

    const tool = wrapTool(client, {
      name: "comp_tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockResolvedValue("ok"),
      compensation,
    });

    await tool({});
    expect(intercept).toHaveBeenCalledWith(
      expect.objectContaining({ compensation: { fnName: "undo_comp", args: { x: 1 } } }),
    );
  });

  it("omits compensation from intercept when not provided", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = wrapTool(client, {
      name: "no_comp_tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockResolvedValue("ok"),
    });

    await tool({});
    expect(intercept).toHaveBeenCalledWith(
      expect.not.objectContaining({ compensation: expect.anything() }),
    );
  });

  it("does not call commit when intercept itself fails with non-AwaitingApproval error", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("intercept_fail"));
    const commit = vi.spyOn(client, "commit");
    const fail = vi.spyOn(client, "fail");
    const fn = vi.fn();

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).rejects.toThrow("intercept_fail");
    expect(fn).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("AwaitingApproval flow (Irreversible)", () => {
  it("propagates AwaitingApprovalError from intercept without calling fn", async () => {
    const approvalError = new AwaitingApprovalError("db_write", { id: 1 }, "app_456");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const fn = vi.fn();

    const tool = wrapTool(client, { name: "irr_tool", tier: ToolTier.Irreversible, fn });

    await expect(tool({})).rejects.toThrow(AwaitingApprovalError);
    expect(fn).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError("delete_db", { name: "prod" }, "app_789");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);

    const tool = wrapTool(client, {
      name: "delete_db",
      tier: ToolTier.Irreversible,
      fn: vi.fn(),
    });

    const err = await tool({ name: "prod" }).catch((e) => e);
    expect(err).toBeInstanceOf(AwaitingApprovalError);
    expect((err as AwaitingApprovalError).toolName).toBe("delete_db");
    expect((err as AwaitingApprovalError).approvalId).toBe("app_789");
    expect((err as AwaitingApprovalError).args).toEqual({ name: "prod" });
  });
});

describe("Replay flow (already committed)", () => {
  it("re-executes fn but skips commit when effect status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const commit = vi.spyOn(client, "commit");
    const fn = vi.fn().mockResolvedValue("replayed");

    const tool = wrapTool(client, { name: "replay_tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).resolves.toBe("replayed");
    expect(fn).toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("skips fail when replayed tool throws", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const fail = vi.spyOn(client, "fail");
    const fn = vi.fn().mockRejectedValue(new Error("replay_err"));

    const tool = wrapTool(client, { name: "replay_tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });

  it("still returns result when commit is skipped in replay", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    vi.spyOn(client, "commit");
    const fn = vi.fn().mockResolvedValue("replay_result");

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).resolves.toBe("replay_result");
  });
});

describe("Error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling fn", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const fn = vi.fn();

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn });

    await expect(tool({})).rejects.toThrow("network_err");
    expect(fn).not.toHaveBeenCalled();
  });

  it("re-throws the original fn error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_error"));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockRejectedValue(new Error("original_err")),
    });

    await expect(tool({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors to the caller", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockResolvedValue("done"),
    });

    await expect(tool({})).rejects.toThrow("commit_fail");
  });

  it("does not call commit when fn throws (fail is called instead)", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit");
    vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockRejectedValue(new Error("err")),
    });

    await expect(tool({})).rejects.toThrow("err");
    expect(commit).not.toHaveBeenCalled();
  });
});

describe("Missing session detection", () => {
  it("throws MissingSessionError when tool executes outside runWithSession without explicit sessionId", async () => {
    expect(getCurrentSession()).toBeUndefined();
    const fn = vi.fn().mockResolvedValue("executed");
    const tool = wrapTool(client, { name: "send_payment", tier: ToolTier.Compensable, fn });

    await expect(tool({ amount: 100 })).rejects.toThrow(MissingSessionError);
    await expect(tool({ amount: 100 })).rejects.toThrow(/Tool "send_payment" executed without an active session context/);
    await expect(tool({ amount: 100 })).rejects.toThrow(/runWithSession\(\)/);
    expect(fn).not.toHaveBeenCalled();
  });

  it("throws MissingSessionError when requireSession is explicitly true", async () => {
    expect(getCurrentSession()).toBeUndefined();
    const tool = wrapTool(
      client,
      { name: "guarded_tool", tier: ToolTier.Compensable, fn: vi.fn(), requireSession: true },
    );

    let thrown: MissingSessionError | undefined;
    try {
      await tool({});
    } catch (err) {
      thrown = err as MissingSessionError;
    }
    expect(thrown).toBeInstanceOf(MissingSessionError);
    expect(thrown?.toolName).toBe("guarded_tool");
    expect(thrown?.message).toContain("guarded_tool");
  });

  it("allows tool execution when explicit sessionId is passed in context", async () => {
    expect(getCurrentSession()).toBeUndefined();
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const fn = vi.fn().mockResolvedValue("context_session_ok");

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn, requireSession: true });
    const result = await tool({}, { sessionId: "550e8400-e29b-41d4-a716-446655440001" });

    expect(result).toBe("context_session_ok");
    expect(fn).toHaveBeenCalled();
  });

  it("allows tool execution when explicit session instance is passed in context", async () => {
    expect(getCurrentSession()).toBeUndefined();
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const fn = vi.fn().mockResolvedValue("instance_session_ok");
    const explicitSession = new UndoLogSession();

    const tool = wrapTool(client, { name: "tool", tier: ToolTier.Compensable, fn, requireSession: true });
    const result = await tool({}, { session: explicitSession });

    expect(result).toBe("instance_session_ok");
    expect(fn).toHaveBeenCalled();
  });

  it("uses active session context when available", async () => {
    const session = new UndoLogSession({ sessionId: "10000000-0000-4000-a000-000000000001" });

    const capturedSessions: (UndoLogSession | undefined)[] = [];

    vi.spyOn(client, "intercept").mockImplementation(async (_params) => {
      capturedSessions.push(getCurrentSession());
      return mockEffect();
    });
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = wrapTool(client, stubDef());

    await runWithSession(session, () => tool({}));

    expect(capturedSessions).toHaveLength(1);
    expect(capturedSessions[0]).toBe(session);
  });

  it("preserves session context across wrapped call failure", async () => {
    const session = new UndoLogSession({ sessionId: "20000000-0000-4000-a000-000000000002" });

    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockRejectedValue(new Error("fail")),
    });

    const capturedBefore: (UndoLogSession | undefined)[] = [];

    await runWithSession(session, async () => {
      capturedBefore.push(getCurrentSession());
      await tool({}).catch(() => {});
      capturedBefore.push(getCurrentSession());
    });

    expect(capturedBefore[0]).toBe(session);
    expect(capturedBefore[1]).toBe(session);
  });

  it("captures getCurrentSession inside intercept callback", async () => {
    const session = new UndoLogSession({ sessionId: "30000000-0000-4000-a000-000000000003" });

    const sessionAtIntercept: (UndoLogSession | undefined)[] = [];

    vi.spyOn(client, "intercept").mockImplementation(async (_params) => {
      sessionAtIntercept.push(getCurrentSession());
      return mockEffect();
    });
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = wrapTool(client, stubDef());

    await runWithSession(session, () => tool({ a: 1 }));

    expect(sessionAtIntercept[0]).toBe(session);
  });

  it("outside session context getCurrentSession remains undefined during wrapped call", async () => {
    const capturedDuring: (UndoLogSession | undefined)[] = [];

    vi.spyOn(client, "intercept").mockImplementation(async (_params) => {
      capturedDuring.push(getCurrentSession());
      return mockEffect();
    });
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = wrapTool(client, stubDef());

    expect(getCurrentSession()).toBeUndefined();
    await tool({});
    expect(capturedDuring[0]).toBeUndefined();
  });
});
