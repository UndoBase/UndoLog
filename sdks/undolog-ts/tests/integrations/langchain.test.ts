import { describe, it, expect, vi, beforeEach } from "vitest";
import { z } from "zod";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { createUndologTool } from "../../src/integrations/langchain.js";
import type { UndologLangchainToolOptions } from "../../src/integrations/langchain.js";
import { ToolTier } from "../../src/tier.js";
import { AwaitingApprovalError } from "../../src/errors.js";

const NOW = new Date().toISOString();

vi.mock("@langchain/core/tools", () => {
  return {
    DynamicStructuredTool: class MockDynamicStructuredTool {
      readonly name: string;
      readonly description: string;
      readonly schema: z.ZodTypeAny;
      readonly func: (...args: unknown[]) => unknown;

      constructor(fields: {
        name: string;
        description: string;
        schema: z.ZodTypeAny;
        func: (...args: unknown[]) => unknown;
      }) {
        this.name = fields.name;
        this.description = fields.description;
        this.schema = fields.schema;
        this.func = fields.func;
      }
    },
  };
});

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
let defaultOptions: UndologLangchainToolOptions;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
  defaultOptions = { tier: ToolTier.Compensable };
});

describe("createUndologTool structure", () => {
  it("returns a DynamicStructuredTool with name, description, schema, func", () => {
    const schema = z.object({ input: z.string() });
    const tool = createUndologTool(
      client,
      {
        name: "my_tool",
        description: "A test LangChain tool",
        schema,
        func: async ({ input }) => `result: ${input}`,
      },
      defaultOptions,
    );

    expect(tool.name).toBe("my_tool");
    expect(tool.description).toBe("A test LangChain tool");
    expect(tool.schema).toBe(schema);
    expect(typeof tool.func).toBe("function");
  });

  it("preserves the original Zod schema", () => {
    const schema = z.object({ location: z.string(), units: z.string().optional() });
    const tool = createUndologTool(
      client,
      {
        name: "weather",
        description: "Weather tool",
        schema,
        func: async ({ location }) => `Weather in ${location}`,
      },
      { tier: ToolTier.Safe },
    );

    expect(tool.schema).toBe(schema);
    const parsed = schema.parse({ location: "NYC" });
    expect(parsed).toEqual({ location: "NYC" });
  });

  it("supports tools with no parameters (empty object schema)", () => {
    const schema = z.object({});
    const tool = createUndologTool(
      client,
      {
        name: "no_params",
        description: "No params tool",
        schema,
        func: async () => "done",
      },
      { tier: ToolTier.Safe },
    );

    expect(tool.name).toBe("no_params");
    expect(typeof tool.func).toBe("function");
  });
});

describe("createUndologTool SAFE bypass", () => {
  it("executes func directly without calling intercept", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const func = vi.fn().mockResolvedValue("safe_result");

    const tool = createUndologTool(
      client,
      {
        name: "safe_tool",
        description: "Safe tool",
        schema: z.object({ key: z.string() }),
        func,
      },
      { tier: ToolTier.Safe },
    );

    const result = await tool.func({ key: "val" });
    expect(result).toBe("safe_result");
    expect(func).toHaveBeenCalledWith({ key: "val" }, undefined);
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates func errors for SAFE tier", async () => {
    const func = vi.fn().mockRejectedValue(new Error("safe_fail"));

    const tool = createUndologTool(
      client,
      {
        name: "safe_tool",
        description: "Safe tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Safe },
    );

    await expect(tool.func({})).rejects.toThrow("safe_fail");
  });
});

describe("createUndologTool Compensable flow", () => {
  it("calls intercept, execute, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const func = vi.fn().mockResolvedValue("executed");

    const tool = createUndologTool(
      client,
      {
        name: "comp_tool",
        description: "Compensable tool",
        schema: z.object({ arg: z.number() }),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    const result = await tool.func({ arg: 1 });
    expect(result).toBe("executed");

    expect(intercept).toHaveBeenCalledWith({
      toolName: "comp_tool",
      args: { arg: 1 },
      tier: ToolTier.Compensable,
    });
    expect(func).toHaveBeenCalledWith({ arg: 1 }, undefined);
    expect(commit).toHaveBeenCalledWith("eff_001");
  });

  it("calls fail when execute throws and re-throws the original error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const func = vi.fn().mockRejectedValue(new Error("exec_fail"));

    const tool = createUndologTool(
      client,
      {
        name: "failing_tool",
        description: "Failing tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = createUndologTool(
      client,
      {
        name: "comp_tool",
        description: "Comp tool",
        schema: z.object({}),
        func: vi.fn().mockResolvedValue("ok"),
      },
      {
        tier: ToolTier.Compensable,
        compensation: { fnName: "undo_comp", args: { x: 1 } },
      },
    );

    await tool.func({});
    expect(intercept).toHaveBeenCalledWith(
      expect.objectContaining({
        compensation: { fnName: "undo_comp", args: { x: 1 } },
      }),
    );
  });

  it("omits compensation from intercept when not provided", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = createUndologTool(
      client,
      {
        name: "no_comp_tool",
        description: "No comp tool",
        schema: z.object({}),
        func: vi.fn().mockResolvedValue("ok"),
      },
      { tier: ToolTier.Compensable },
    );

    await tool.func({});
    expect(intercept).toHaveBeenCalledWith(
      expect.not.objectContaining({ compensation: expect.anything() }),
    );
  });
});

describe("createUndologTool Irreversible flow", () => {
  it("throws AwaitingApprovalError from intercept without calling func", async () => {
    const approvalError = new AwaitingApprovalError("db_write", { id: 1 }, "app_456");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const func = vi.fn();

    const tool = createUndologTool(
      client,
      {
        name: "irr_tool",
        description: "Irreversible tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Irreversible },
    );

    await expect(tool.func({})).rejects.toThrow(AwaitingApprovalError);
    expect(func).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError("delete_db", { name: "prod" }, "app_789");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);

    const tool = createUndologTool(
      client,
      {
        name: "delete_db",
        description: "Delete db tool",
        schema: z.object({ name: z.string() }),
        func: vi.fn(),
      },
      { tier: ToolTier.Irreversible },
    );

    const err = await tool.func({ name: "prod" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AwaitingApprovalError);
    const approvalErr = err as AwaitingApprovalError;
    expect(approvalErr.toolName).toBe("delete_db");
    expect(approvalErr.approvalId).toBe("app_789");
    expect(approvalErr.args).toEqual({ name: "prod" });
  });
});

describe("createUndologTool replay flow", () => {
  it("re-executes func but skips commit when status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const commit = vi.spyOn(client, "commit");
    const func = vi.fn().mockResolvedValue("replayed");

    const tool = createUndologTool(
      client,
      {
        name: "replay_tool",
        description: "Replay tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    const result = await tool.func({});
    expect(result).toBe("replayed");
    expect(func).toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("skips fail when replayed tool throws", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const fail = vi.spyOn(client, "fail");
    const func = vi.fn().mockRejectedValue(new Error("replay_err"));

    const tool = createUndologTool(
      client,
      {
        name: "replay_tool",
        description: "Replay tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("createUndologTool missing session", () => {
  it("works without an active session context", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const func = vi.fn().mockResolvedValue("result");

    const tool = createUndologTool(
      client,
      {
        name: "no_session_tool",
        description: "No active session",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).resolves.toBe("result");
    expect(intercept).toHaveBeenCalled();
  });
});

describe("createUndologTool error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling func", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const func = vi.fn();

    const tool = createUndologTool(
      client,
      {
        name: "tool",
        description: "Generic tool",
        schema: z.object({}),
        func,
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).rejects.toThrow("network_err");
    expect(func).not.toHaveBeenCalled();
  });

  it("re-throws the original func error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_also_fails"));

    const tool = createUndologTool(
      client,
      {
        name: "tool",
        description: "Generic tool",
        schema: z.object({}),
        func: vi.fn().mockRejectedValue(new Error("original_err")),
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = createUndologTool(
      client,
      {
        name: "tool",
        description: "Generic tool",
        schema: z.object({}),
        func: vi.fn().mockResolvedValue("done"),
      },
      { tier: ToolTier.Compensable },
    );

    await expect(tool.func({})).rejects.toThrow("commit_fail");
  });
});
