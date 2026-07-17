import { describe, it, expect, vi, beforeEach } from "vitest";
import { UndoLogClient, type EffectRecord } from "../src/client.js";
import { wrapTool, type ToolDefinition } from "../src/decorators.js";
import { ToolTier } from "../src/tier.js";
import { AwaitingApprovalError } from "../src/errors.js";

function mockEffect(overrides?: Partial<EffectRecord>): EffectRecord {
  return {
    effectId: "eff_001",
    sessionId: "sess_abc",
    stepIndex: 1,
    toolName: "test_tool",
    signature: "sig_xxx",
    status: "pending",
    tier: ToolTier.Compensable,
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

let client: UndoLogClient;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
});

describe("SAFE bypass", () => {
  it("executes fn directly without calling intercept", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const fn = vi.fn().mockResolvedValue("safe_result");

    const tool = wrapTool(client, {
      name: "safe_tool",
      tier: ToolTier.Safe,
      fn,
    });

    await expect(tool({ key: "val" })).resolves.toBe("safe_result");
    expect(fn).toHaveBeenCalledWith({ key: "val" });
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates fn errors", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("safe_fail"));
    const tool = wrapTool(client, {
      name: "safe_tool",
      tier: ToolTier.Safe,
      fn,
    });

    await expect(tool({})).rejects.toThrow("safe_fail");
  });
});

describe("Execute flow (Compensable)", () => {
  it("calls intercept, fn, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const fn = vi.fn().mockResolvedValue("executed");

    const tool = wrapTool(client, {
      name: "comp_tool",
      tier: ToolTier.Compensable,
      fn,
    });

    await expect(tool({ arg: 1 })).resolves.toBe("executed");
    expect(intercept).toHaveBeenCalledWith({
      toolName: "comp_tool",
      args: { arg: 1 },
      tier: ToolTier.Compensable,
    });
    expect(fn).toHaveBeenCalledWith({ arg: 1 });
    expect(commit).toHaveBeenCalledWith("eff_001");
  });

  it("calls fail when fn throws and re-throws the original error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const fn = vi.fn().mockRejectedValue(new Error("exec_fail"));

    const tool = wrapTool(client, {
      name: "failing_tool",
      tier: ToolTier.Compensable,
      fn,
    });

    await expect(tool({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = wrapTool(client, {
      name: "comp_tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockResolvedValue("ok"),
      compensation: { fnName: "undo_comp", args: { x: 1 } },
    });

    await tool({});
    expect(intercept).toHaveBeenCalledWith(
      expect.objectContaining({
        compensation: { fnName: "undo_comp", args: { x: 1 } },
      }),
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
});

describe("AwaitingApproval flow (Irreversible)", () => {
  it("propagates AwaitingApprovalError from intercept without calling fn", async () => {
    const approvalError = new AwaitingApprovalError(
      "db_write",
      { id: 1 },
      "app_456",
    );
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const fn = vi.fn();

    const tool = wrapTool(client, {
      name: "irr_tool",
      tier: ToolTier.Irreversible,
      fn,
    });

    await expect(tool({})).rejects.toThrow(AwaitingApprovalError);
    expect(fn).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError(
      "delete_db",
      { name: "prod" },
      "app_789",
    );
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
  it("re-executes fn but skips commit when status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(
      mockEffect({ status: "committed" }),
    );
    const commit = vi.spyOn(client, "commit");
    const fn = vi.fn().mockResolvedValue("replayed");

    const tool = wrapTool(client, {
      name: "replay_tool",
      tier: ToolTier.Compensable,
      fn,
    });

    await expect(tool({})).resolves.toBe("replayed");
    expect(fn).toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("skips fail when replayed tool throws", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(
      mockEffect({ status: "committed" }),
    );
    const fail = vi.spyOn(client, "fail");
    const fn = vi.fn().mockRejectedValue(new Error("replay_err"));

    const tool = wrapTool(client, {
      name: "replay_tool",
      tier: ToolTier.Compensable,
      fn,
    });

    await expect(tool({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("Error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling fn", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const fn = vi.fn();

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn,
    });

    await expect(tool({})).rejects.toThrow("network_err");
    expect(fn).not.toHaveBeenCalled();
  });

  it("re-throws the original fn error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_also_fails"));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockRejectedValue(new Error("original_err")),
    });

    await expect(tool({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = wrapTool(client, {
      name: "tool",
      tier: ToolTier.Compensable,
      fn: vi.fn().mockResolvedValue("done"),
    });

    await expect(tool({})).rejects.toThrow("commit_fail");
  });
});
