import { describe, it, expect, vi, beforeEach } from "vitest";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import { undologMastraTool } from "../../src/integrations/mastra.js";
import type { UndologMastraTool, UndologMastraToolOptions } from "../../src/integrations/mastra.js";
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
let defaultOptions: UndologMastraToolOptions;

beforeEach(() => {
  client = new UndoLogClient({ baseUrl: "http://localhost:9999" });
  defaultOptions = { toolName: "test_tool", tier: ToolTier.Compensable };
});

function makeDefinition(execute: (input: Record<string, unknown>) => unknown) {
  return {
    id: "test_tool",
    description: "A test Mastra tool",
    execute,
  };
}

describe("undologMastraTool structure", () => {
  it("returns an object with id, description, and execute", () => {
    const tool = undologMastraTool(
      client,
      {
        id: "my_tool",
        description: "A test tool",
        execute: async () => "result",
      },
      defaultOptions,
    );

    expect(tool.id).toBe("my_tool");
    expect(tool.description).toBe("A test tool");
    expect(typeof tool.execute).toBe("function");
  });

  it("returns an object matching UndologMastraTool interface", () => {
    const tool: UndologMastraTool<Record<string, unknown>, string> = undologMastraTool(
      client,
      {
        id: "typed_tool",
        description: "typed tool",
        execute: async () => "result",
      },
      { toolName: "typed_tool", tier: ToolTier.Safe },
    );

    expect(tool.id).toBe("typed_tool");
    expect(tool.description).toBe("typed tool");
    expect(typeof tool.execute).toBe("function");
  });

  it("preserves inputSchema and outputSchema from the definition", () => {
    const inputSchema = { type: "object", properties: { x: { type: "number" } } };
    const outputSchema = { type: "object", properties: { y: { type: "string" } } };

    const tool = undologMastraTool(
      client,
      {
        id: "schema_tool",
        description: "Schema tool",
        inputSchema,
        outputSchema,
        execute: async () => "ok",
      },
      { toolName: "schema_tool", tier: ToolTier.Safe },
    );

    expect(tool.inputSchema).toBe(inputSchema);
    expect(tool.outputSchema).toBe(outputSchema);
  });
});

describe("undologMastraTool execute passes through context", () => {
  it("forwards the optional context argument to the underlying execute", async () => {
    const execute = vi.fn().mockResolvedValue("done");

    const tool = undologMastraTool(
      client,
      {
        id: "ctx_tool",
        description: "Context tool",
        execute,
      },
      { toolName: "ctx_tool", tier: ToolTier.Safe },
    );

    const context = { mastra: true, runId: "run_123" };
    await expect(tool.execute({ arg: 1 }, context)).resolves.toBe("done");
    expect(execute).toHaveBeenCalledWith({ arg: 1 }, context);
  });

  it("works when context is omitted", async () => {
    const execute = vi.fn().mockResolvedValue("done");

    const tool = undologMastraTool(
      client,
      {
        id: "ctx_tool",
        description: "Context tool",
        execute,
      },
      { toolName: "ctx_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({ arg: 1 })).resolves.toBe("done");
    expect(execute).toHaveBeenCalledWith({ arg: 1 }, undefined);
  });
});

describe("undologMastraTool SAFE bypass", () => {
  it("executes fn directly without calling intercept", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe_result");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({ key: "val" })).resolves.toBe("safe_result");
    expect(execute).toHaveBeenCalledWith({ key: "val" }, undefined);
    expect(intercept).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
  });

  it("propagates fn errors for SAFE tier", async () => {
    const execute = vi.fn().mockRejectedValue(new Error("safe_fail"));

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "safe_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({})).rejects.toThrow("safe_fail");
  });
});

describe("undologMastraTool Compensable flow", () => {
  it("calls intercept, execute, then commit on success", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const commit = vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const execute = vi.fn().mockResolvedValue("executed");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "comp_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({ arg: 1 })).resolves.toBe("executed");

    expect(intercept).toHaveBeenCalledWith({
      toolName: "comp_tool",
      args: { arg: 1 },
      tier: ToolTier.Compensable,
    });
    expect(execute).toHaveBeenCalledWith({ arg: 1 }, undefined);
    expect(commit).toHaveBeenCalledWith("eff_001");
  });

  it("calls fail when execute throws and re-throws the original error", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const fail = vi.spyOn(client, "fail").mockResolvedValue(mockEffect({ status: "failed" }));
    const execute = vi.fn().mockRejectedValue(new Error("exec_fail"));

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "failing_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("exec_fail");
    expect(fail).toHaveBeenCalledWith("eff_001", "exec_fail");
  });

  it("passes compensation descriptor to intercept", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn().mockResolvedValue("ok")),
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

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn().mockResolvedValue("ok")),
      { toolName: "no_comp_tool", tier: ToolTier.Compensable },
    );

    await tool.execute({});
    expect(intercept).toHaveBeenCalledWith(
      expect.not.objectContaining({ compensation: expect.anything() }),
    );
  });
});

describe("undologMastraTool Irreversible flow", () => {
  it("throws AwaitingApprovalError from intercept without calling execute", async () => {
    const approvalError = new AwaitingApprovalError("db_write", { id: 1 }, "app_456");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);
    const execute = vi.fn();

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "irr_tool", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });

  it("preserves approval metadata on the propagated error", async () => {
    const approvalError = new AwaitingApprovalError("delete_db", { name: "prod" }, "app_789");
    vi.spyOn(client, "intercept").mockRejectedValue(approvalError);

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn()),
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

describe("undologMastraTool tier propagation", () => {
  it("SAFE tier bypasses intercept and commit", async () => {
    const intercept = vi.spyOn(client, "intercept");
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("safe");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
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

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
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

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "irr_op", tier: ToolTier.Irreversible },
    );

    await expect(tool.execute({})).rejects.toThrow(AwaitingApprovalError);
    expect(execute).not.toHaveBeenCalled();
  });
});

describe("undologMastraTool schema preservation", () => {
  it("preserves the original inputSchema", () => {
    const inputSchema = { type: "object", properties: { location: { type: "string" } } };

    const tool = undologMastraTool(
      client,
      {
        id: "get_weather",
        description: "Weather tool",
        inputSchema,
        execute: async ({ location }: Record<string, string>) => ({ temperature: 72, location }),
      },
      { toolName: "get_weather", tier: ToolTier.Safe },
    );

    expect(tool.inputSchema).toBe(inputSchema);
    expect(tool.inputSchema).not.toHaveProperty("sessionId");
  });

  it("allows tools without inputSchema or outputSchema to work correctly", async () => {
    const execute = vi.fn().mockResolvedValue("ok");

    const tool = undologMastraTool(
      client,
      {
        id: "no_schema",
        description: "No schema tool",
        execute,
      },
      { toolName: "no_schema", tier: ToolTier.Safe },
    );

    expect(tool.inputSchema).toBeUndefined();
    expect(tool.outputSchema).toBeUndefined();
    await expect(tool.execute({})).resolves.toBe("ok");
  });
});

describe("undologMastraTool replay flow", () => {
  it("re-executes execute but skips commit when status is committed", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect({ status: "committed" }));
    const commit = vi.spyOn(client, "commit");
    const execute = vi.fn().mockResolvedValue("replayed");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
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

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "replay_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("replay_err");
    expect(fail).not.toHaveBeenCalled();
  });
});

describe("undologMastraTool error propagation", () => {
  it("re-throws non-AwaitingApproval intercept errors without calling execute", async () => {
    vi.spyOn(client, "intercept").mockRejectedValue(new Error("network_err"));
    const execute = vi.fn();

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("network_err");
    expect(execute).not.toHaveBeenCalled();
  });

  it("re-throws the original execute error even when fail itself fails", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_also_fails"));

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn().mockRejectedValue(new Error("original_err"))),
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("original_err");
  });

  it("propagates commit errors", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockRejectedValue(new Error("commit_fail"));

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn().mockResolvedValue("done")),
      { toolName: "tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).rejects.toThrow("commit_fail");
  });

  it("re-throws the original execute error even when fail fails, preserving the original message", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "fail").mockRejectedValue(new Error("fail_error"));

    const tool = undologMastraTool(
      client,
      makeDefinition(vi.fn().mockRejectedValue(new Error("original_failure"))),
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

describe("undologMastraTool sync execute function", () => {
  it("handles a non-async execute function", async () => {
    const execute = vi.fn().mockReturnValue("sync_result");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "sync_tool", tier: ToolTier.Safe },
    );

    await expect(tool.execute({})).resolves.toBe("sync_result");
    expect(execute).toHaveBeenCalled();
  });
});

describe("undologMastraTool missing session", () => {
  it("works without an active session context", async () => {
    const intercept = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));
    const execute = vi.fn().mockResolvedValue("result");

    const tool = undologMastraTool(
      client,
      makeDefinition(execute),
      { toolName: "no_session_tool", tier: ToolTier.Compensable },
    );

    await expect(tool.execute({})).resolves.toBe("result");
    expect(intercept).toHaveBeenCalled();
  });
});
