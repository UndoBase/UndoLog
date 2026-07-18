import { describe, it, expect, vi, beforeEach } from "vitest";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { undologTool } from "../../src/integrations/vercel-ai-sdk.js";
import type { UndologVercelTool, UndologVercelToolOptions } from "../../src/integrations/vercel-ai-sdk.js";
import { ToolTier } from "../../src/tier.js";
import { AwaitingApprovalError } from "../../src/errors.js";

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

let client: UndoLogClient;
let defaultOptions: UndologVercelToolOptions;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
  defaultOptions = { toolName: "test_tool", tier: ToolTier.Compensable };
});

describe("undologTool structure", () => {
  it("returns an object with description, parameters, and execute", () => {
    const tool = undologTool(
      client,
      {
        description: "A test tool",
        parameters: { parse: (input: unknown) => input as Record<string, unknown> },
        execute: async () => "result",
      },
      defaultOptions,
    );

    expect(tool).toHaveProperty("description", "A test tool");
    expect(tool).toHaveProperty("parameters");
    expect(tool).toHaveProperty("execute");
    expect(typeof tool.execute).toBe("function");
  });

  it("returns an object matching UndologVercelTool interface", () => {
    const tool: UndologVercelTool = undologTool(
      client,
      {
        description: "typed tool",
        parameters: { parse: (input: unknown) => ({}) },
        execute: async () => 42,
      },
      { toolName: "typed_tool", tier: ToolTier.Safe },
    );

    expect(tool.description).toBe("typed tool");
    expect(typeof tool.parameters?.parse).toBe("function");
    expect(typeof tool.execute).toBe("function");
  });

  it("allows omitted description and parameters", () => {
    const tool = undologTool(
      client,
      {
        execute: async () => "no_desc",
      },
      defaultOptions,
    );

    expect(tool.description).toBeUndefined();
    expect(tool.parameters).toBeUndefined();
  });
});

describe("undologTool SAFE bypass", () => {
  it("executes fn directly without calling intercept", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe_result");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({ key: "val" })).resolves.toBe("safe_result");
    expect(execute).toHaveBeenCalledWith({ key: "val" });
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates fn errors for SAFE tier", async () => {
    const execute = vi.fn().mockRejectedValue(new Error("safe_fail"));

    const tool = undologTool(
      client,
      { execute },
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({})).rejects.toThrow("safe_fail");
  });
});

describe("undologTool Compensable flow", () => {
  it("calls intercept, execute, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const execute = vi.fn().mockResolvedValue("executed");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "comp_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({ arg: 1 })).resolves.toBe("executed");

    expect(intercept).toHaveBeenCalledWith({
      toolName: "comp_tool",
      args: { arg: 1 },
      tier: ToolTier.Compensable,
    });
    expect(execute).toHaveBeenCalledWith({ arg: 1 });
    expect(commit).toHaveBeenCalledWith("eff_001");
  });

  it("calls fail when execute throws and re-throws the original error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const execute = vi.fn().mockRejectedValue(new Error("exec_fail"));

    const tool = undologTool(
      client,
      { execute },
      { toolName: "failing_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = undologTool(
      client,
      { execute: vi.fn().mockResolvedValue("ok") },
      {
        toolName: "comp_tool",
        tier: ToolTier.Compensable,
        compensation: { fnName: "undo_comp", args: { x: 1 } },
      },
    );

    await tool.execute({});
    expect(intercept).toHaveBeenCalledWith(
      expect.objectContaining({
        compensation: { fnName: "undo_comp", args: { x: 1 } },
      }),
    );
  });

  it("omits compensation from intercept when not provided", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = undologTool(
      client,
      { execute: vi.fn().mockResolvedValue("ok") },
      { toolName: "no_comp_tool", tier: ToolTier.Compensable },
    );

    await tool.execute({});
    expect(intercept).toHaveBeenCalledWith(
      expect.not.objectContaining({ compensation: expect.anything() }),
    );
  });
});

describe("undologTool Irreversible flow", () => {
  it("throws AwaitingApprovalError from intercept without calling execute", async () => {
    const approvalError = new AwaitingApprovalError("db_write", { id: 1 }, "app_456");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const execute = vi.fn();

    const tool = undologTool(
      client,
      { execute },
      { toolName: "irr_tool", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError("delete_db", { name: "prod" }, "app_789");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);

    const tool = undologTool(
      client,
      { execute: vi.fn() },
      { toolName: "delete_db", tier: ToolTier.Irreversible },
    );

    const err = await tool.execute({ name: "prod" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AwaitingApprovalError);
    const approvalErr = err as AwaitingApprovalError;
    expect(approvalErr.toolName).toBe("delete_db");
    expect(approvalErr.approvalId).toBe("app_789");
    expect(approvalErr.args).toEqual({ name: "prod" });
  });
});

describe("undologTool tier propagation", () => {
  it("SAFE tier bypasses intercept and commit", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "safe_op", tier: ToolTier.Safe },
    );

    const result = await tool.execute({});
    expect(result).toBe("safe");
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("Compensable tier performs full intercept-execute-commit cycle", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const execute = vi.fn().mockResolvedValue("compensable_result");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "comp_op", tier: ToolTier.Compensable },
    );

    const result = await tool.execute({});
    expect(result).toBe("compensable_result");
    expect(intercept).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("Irreversible tier throws AwaitingApprovalError before execute", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(
      new AwaitingApprovalError("irr_op", {}, "app_999"),
    );
    const execute = vi.fn();

    const tool = undologTool(
      client,
      { execute },
      { toolName: "irr_op", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });
});

describe("undologTool session param omission", () => {
  it("exposes the original parameters schema without injected session fields", () => {
    const parameters = {
      parse: (input: unknown) => {
        const raw = input as Record<string, string>;
        return { location: raw.location ?? "" };
      },
    };

    const tool = undologTool(
      client,
      {
        description: "weather tool",
        parameters,
        execute: async ({ location }: Record<string, string>) => ({ temperature: 72, location }),
      },
      { toolName: "get_weather", tier: ToolTier.Safe },
    );

    expect(tool.parameters).toBe(parameters);
    const parsed = parameters.parse({ location: "NYC" });
    expect(parsed).toEqual({ location: "NYC" });
    expect(parsed).not.toHaveProperty("sessionId");
    expect(parsed).not.toHaveProperty("session");
  });

  it("allows tools without parameters to work correctly", async () => {
    const execute = vi.fn().mockResolvedValue("ok");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "no_params", tier: ToolTier.Safe },
    );

    expect(tool.parameters).toBeUndefined();
    await expect(tool.execute({})).resolves.toBe("ok");
  });
});

describe("undologTool replay flow", () => {
  it("re-executes execute but skips commit when status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("replayed");

    const tool = undologTool(
      client,
      { execute },
      { toolName: "replay_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).resolves.toBe("replayed");
    expect(execute).toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("skips fail when replayed tool throws", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const fail = vi.spyOn(client, "fail");
    const execute = vi.fn().mockRejectedValue(new Error("replay_err"));

    const tool = undologTool(
      client,
      { execute },
      { toolName: "replay_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("undologTool error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling execute", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const execute = vi.fn();

    const tool = undologTool(
      client,
      { execute },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("network_err");
    expect(execute).not.toHaveBeenCalled();
  });

  it("re-throws the original execute error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_also_fails"));

    const tool = undologTool(
      client,
      { execute: vi.fn().mockRejectedValue(new Error("original_err")) },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = undologTool(
      client,
      { execute: vi.fn().mockResolvedValue("done") },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("commit_fail");
  });
});
