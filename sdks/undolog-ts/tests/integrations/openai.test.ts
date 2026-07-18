import { describe, it, expect, vi, beforeEach } from "vitest";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { undologFunctionTool } from "../../src/integrations/openai.js";
import type { UndologOpenAITool, UndologOpenAIToolOptions } from "../../src/integrations/openai.js";
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
let defaultOptions: UndologOpenAIToolOptions;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
  defaultOptions = { toolName: "test_tool", tier: ToolTier.Compensable };
});

describe("undologFunctionTool structure", () => {
  it("returns an object with name, description, parameters, and execute", () => {
    const parameters = {
      type: "object",
      properties: { location: { type: "string" } },
      required: ["location"],
    };

    const tool = undologFunctionTool(
      client,
      {
        name: "get_weather",
        description: "Get the weather",
        parameters,
        execute: async () => ({ temperature: 72 }),
      },
      defaultOptions,
    );

    expect(tool.name).toBe("get_weather");
    expect(tool.description).toBe("Get the weather");
    expect(tool.parameters).toBe(parameters);
    expect(typeof tool.execute).toBe("function");
  });

  it("returns an object matching UndologOpenAITool interface", () => {
    const tool: UndologOpenAITool<Record<string, unknown>, string> = undologFunctionTool(
      client,
      {
        name: "typed_tool",
        description: "typed tool",
        parameters: { type: "object", properties: {} },
        execute: async () => "result",
      },
      { toolName: "typed_tool", tier: ToolTier.Safe },
    );

    expect(tool.name).toBe("typed_tool");
    expect(tool.description).toBe("typed tool");
    expect(tool.parameters).toEqual({ type: "object", properties: {} });
    expect(typeof tool.execute).toBe("function");
  });

  it("allows omitted description and parameters", () => {
    const tool = undologFunctionTool(
      client,
      {
        name: "minimal_tool",
        execute: async () => "no_desc",
      },
      defaultOptions,
    );

    expect(tool.name).toBe("minimal_tool");
    expect(tool.description).toBeUndefined();
    expect(tool.parameters).toBeUndefined();
  });
});

describe("undologFunctionTool SAFE bypass", () => {
  it("executes fn directly without calling intercept", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe_result");

    const tool = undologFunctionTool(
      client,
      {
        name: "safe_tool",
        execute,
      },
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({ key: "val" })).resolves.toBe("safe_result");
    expect(execute).toHaveBeenCalledWith({ key: "val" });
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates fn errors for SAFE tier", async () => {
    const execute = vi.fn().mockRejectedValue(new Error("safe_fail"));

    const tool = undologFunctionTool(
      client,
      {
        name: "safe_tool",
        execute,
      },
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({})).rejects.toThrow("safe_fail");
  });
});

describe("undologFunctionTool Compensable flow", () => {
  it("calls intercept, execute, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const execute = vi.fn().mockResolvedValue("executed");

    const tool = undologFunctionTool(
      client,
      {
        name: "comp_tool",
        execute,
      },
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

    const tool = undologFunctionTool(
      client,
      {
        name: "failing_tool",
        execute,
      },
      { toolName: "failing_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = undologFunctionTool(
      client,
      {
        name: "comp_tool",
        execute: vi.fn().mockResolvedValue("ok"),
      },
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

    const tool = undologFunctionTool(
      client,
      {
        name: "no_comp_tool",
        execute: vi.fn().mockResolvedValue("ok"),
      },
      { toolName: "no_comp_tool", tier: ToolTier.Compensable },
    );

    await tool.execute({});
    expect(intercept).toHaveBeenCalledWith(
      expect.not.objectContaining({ compensation: expect.anything() }),
    );
  });
});

describe("undologFunctionTool Irreversible flow", () => {
  it("throws AwaitingApprovalError from intercept without calling execute", async () => {
    const approvalError = new AwaitingApprovalError("db_write", { id: 1 }, "app_456");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const execute = vi.fn();

    const tool = undologFunctionTool(
      client,
      {
        name: "irr_tool",
        execute,
      },
      { toolName: "irr_tool", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError("delete_db", { name: "prod" }, "app_789");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);

    const tool = undologFunctionTool(
      client,
      {
        name: "delete_db",
        execute: vi.fn(),
      },
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

describe("undologFunctionTool tier propagation", () => {
  it("SAFE tier bypasses intercept and commit", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe");

    const tool = undologFunctionTool(
      client,
      {
        name: "safe_op",
        execute,
      },
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

    const tool = undologFunctionTool(
      client,
      {
        name: "comp_op",
        execute,
      },
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

    const tool = undologFunctionTool(
      client,
      {
        name: "irr_op",
        execute,
      },
      { toolName: "irr_op", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });
});

describe("undologFunctionTool parameters schema preservation", () => {
  it("preserves the original parameters schema", () => {
    const parameters = {
      type: "object",
      properties: { location: { type: "string" } },
      required: ["location"],
    };

    const tool = undologFunctionTool(
      client,
      {
        name: "get_weather",
        description: "Weather tool",
        parameters,
        execute: async ({ location }: Record<string, string>) => ({ temperature: 72, location }),
      },
      { toolName: "get_weather", tier: ToolTier.Safe },
    );

    expect(tool.parameters).toBe(parameters);
    expect(tool.parameters).toEqual({
      type: "object",
      properties: { location: { type: "string" } },
      required: ["location"],
    });
    expect(tool.parameters).not.toHaveProperty("sessionId");
  });

  it("allows tools without parameters to work correctly", async () => {
    const execute = vi.fn().mockResolvedValue("ok");

    const tool = undologFunctionTool(
      client,
      {
        name: "no_params",
        execute,
      },
      { toolName: "no_params", tier: ToolTier.Safe },
    );

    expect(tool.parameters).toBeUndefined();
    await expect(tool.execute({})).resolves.toBe("ok");
  });

  it("preserves parameters from definition with empty schema", () => {
    const parameters = { type: "object", properties: {} };

    const tool = undologFunctionTool(
      client,
      {
        name: "empty_params",
        parameters,
        execute: async () => "done",
      },
      { toolName: "empty_params", tier: ToolTier.Safe },
    );

    expect(tool.parameters).toBe(parameters);
  });
});

describe("undologFunctionTool replay flow", () => {
  it("re-executes execute but skips commit when status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("replayed");

    const tool = undologFunctionTool(
      client,
      {
        name: "replay_tool",
        execute,
      },
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

    const tool = undologFunctionTool(
      client,
      {
        name: "replay_tool",
        execute,
      },
      { toolName: "replay_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("undologFunctionTool error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling execute", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const execute = vi.fn();

    const tool = undologFunctionTool(
      client,
      {
        name: "tool",
        execute,
      },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("network_err");
    expect(execute).not.toHaveBeenCalled();
  });

  it("re-throws the original execute error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_also_fails"));

    const tool = undologFunctionTool(
      client,
      {
        name: "tool",
        execute: vi.fn().mockRejectedValue(new Error("original_err")),
      },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = undologFunctionTool(
      client,
      {
        name: "tool",
        execute: vi.fn().mockResolvedValue("done"),
      },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("commit_fail");
  });

  it("re-throws the original execute error even when fail fails, preserving the original message", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_error"));

    const tool = undologFunctionTool(
      client,
      {
        name: "tool",
        execute: vi.fn().mockRejectedValue(new Error("original_failure")),
      },
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    let error: unknown;
    try {
      await tool.execute({});
    } catch (e) {
      error = e;
    }
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("original_failure");
  });
});

describe("undologFunctionTool sync execute function", () => {
  it("handles a non-async execute function", async () => {
    const execute = vi.fn().mockReturnValue("sync_result");

    const tool = undologFunctionTool(
      client,
      {
        name: "sync_tool",
        execute,
      },
      { toolName: "sync_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({})).resolves.toBe("sync_result");
    expect(execute).toHaveBeenCalled();
  });
});
