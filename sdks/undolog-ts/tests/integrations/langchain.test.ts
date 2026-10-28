import { describe, it, expect, vi, beforeEach } from "vitest";
import { z } from "zod";
import { UndoLogClient, type EffectRecord } from "../../src/client.js";
import {
  createUndologTool,
  wrapLangChainAgent,
  wrapLangChainTools,
} from "../../src/integrations/langchain.js";
import type { UndologLangchainToolOptions } from "../../src/integrations/langchain.js";
import { ToolTier } from "../../src/tier.js";
import { AwaitingApprovalError, MissingSessionError } from "../../src/errors.js";
import { getCurrentSession, runWithSession, UndoLogSession } from "../../src/session.js";

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

describe("wrapLangChainAgent and wrapLangChainTools", () => {
  it("binds UndoLog interception to all tools registered on an agent executor", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const searchTool = {
      name: "search_docs",
      description: "Search documentation",
      func: vi.fn().mockResolvedValue("search_results"),
    };
    const calcTool = {
      name: "calculate",
      description: "Perform calculation",
      invoke: vi.fn().mockResolvedValue("42"),
    };

    const executor = {
      tools: [searchTool, calcTool],
      invoke: async (input: { query: string }) => {
        const r1 = await searchTool.func({ query: input.query });
        const r2 = await calcTool.invoke({ expr: "20+22" });
        return `${r1} and ${r2}`;
      },
    };

    const wrapped = wrapLangChainAgent(client, executor, {
      tiers: {
        search_docs: ToolTier.Safe,
        calculate: ToolTier.Compensable,
      },
    });

    expect(wrapped.tools.length).toBe(2);
    expect(wrapped._undologWrapped).toBe(true);

    const result = await wrapped.invoke({ query: "pricing" });
    expect(result).toBe("search_results and 42");
    expect(client.commit).toHaveBeenCalledWith("eff_001");
  });

  it("intercepts tool calls inside LangChain agent execution loops", async () => {
    let capturedSessionId: string | undefined;
    vi.spyOn(client, "intercept").mockImplementation(async (params) => {
      capturedSessionId = params.sessionId ?? getCurrentSession()?.sessionId;
      return mockEffect({ sessionId: capturedSessionId, toolName: params.toolName });
    });
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const step1Tool = {
      name: "step_one",
      call: vi.fn().mockResolvedValue("res_1"),
    };
    const step2Tool = {
      name: "step_two",
      call: vi.fn().mockResolvedValue("res_2"),
    };

    const executor = {
      tools: [step1Tool, step2Tool],
      invoke: async (_input?: unknown) => {
        const a = await step1Tool.call({ x: 1 });
        const b = await step2Tool.call({ y: 2 });
        return [a, b];
      },
    };

    const wrapped = wrapLangChainAgent(client, executor);
    const result = await wrapped.invoke({});

    expect(result).toEqual(["res_1", "res_2"]);
    expect(client.intercept).toHaveBeenCalledTimes(2);
    expect(client.commit).toHaveBeenCalledTimes(2);
    expect(capturedSessionId).toBeDefined();
  });

  it("structured tool arguments are validated and serialized via canonical JSON", async () => {
    const interceptSpy = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const schema = z.object({
      target: z.string(),
      payload: z.object({
        z: z.number(),
        a: z.number(),
      }),
    });

    const structuredTool = {
      name: "structured_tool",
      schema,
      func: vi.fn().mockImplementation(async (args) => {
        schema.parse(args);
        return "valid";
      }),
    };

    const executor = {
      tools: [structuredTool],
      invoke: async (input?: unknown) => structuredTool.func(input),
    };

    const wrapped = wrapLangChainAgent(client, executor);
    const complexArgs = { target: "srv-1", payload: { z: 99, a: 1 } };
    const res = await wrapped.invoke(complexArgs);

    expect(res).toBe("valid");
    expect(interceptSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "structured_tool",
        args: complexArgs,
      }),
    );
  });

  it("normalizes primitive tool arguments for intercept while forwarding original primitive", async () => {
    const interceptSpy = vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    let receivedArg: unknown;
    const primitiveTool = {
      name: "primitive_tool",
      invoke: vi.fn().mockImplementation(async (arg: unknown) => {
        receivedArg = arg;
        return `echo:${arg}`;
      }),
    };

    const executor = {
      tools: [primitiveTool],
      invoke: async (input: unknown) => primitiveTool.invoke(input),
    };

    const wrapped = wrapLangChainAgent(client, executor);
    const res = await wrapped.invoke("raw_string_param");

    expect(res).toBe("echo:raw_string_param");
    expect(receivedArg).toBe("raw_string_param");
    expect(interceptSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        toolName: "primitive_tool",
        args: expect.objectContaining({ input: "raw_string_param" }),
      }),
    );
  });

  it("replay status committed skips re-commit while returning re-executed values", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(
      mockEffect({ status: "committed" }),
    );
    const commitSpy = vi.spyOn(client, "commit");

    const invokeSpy = vi.fn().mockResolvedValue("replayed_value");
    const tool = {
      name: "replay_action",
      invoke: invokeSpy,
    };

    const executor = {
      tools: [tool],
      invoke: async (_input?: unknown) => tool.invoke({ action: "read" }),
    };

    const wrapped = wrapLangChainAgent(client, executor);
    const result = await wrapped.invoke({});

    expect(result).toBe("replayed_value");
    expect(invokeSpy).toHaveBeenCalled();
    expect(commitSpy).not.toHaveBeenCalled();
  });

  it("skips fail callback when replayed tool throws", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(
      mockEffect({ status: "committed" }),
    );
    const failSpy = vi.spyOn(client, "fail");

    const tool = {
      name: "flaky_replay",
      call: vi.fn().mockRejectedValue(new Error("replay_crash")),
    };

    const executor = {
      tools: [tool],
      invoke: async (_input?: unknown) => tool.call({}),
    };

    const wrapped = wrapLangChainAgent(client, executor);
    await expect(wrapped.invoke({})).rejects.toThrow("replay_crash");
    expect(failSpy).not.toHaveBeenCalled();
  });

  it("reversible tool failure registers failure record and propagates error to loop", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    const failSpy = vi.spyOn(client, "fail").mockResolvedValue(
      mockEffect({ status: "failed" }),
    );

    const failingTool = {
      name: "failing_operation",
      func: vi.fn().mockRejectedValue(new Error("operation_aborted")),
    };

    const executor = {
      tools: [failingTool],
      invoke: async (_input?: unknown) => {
        try {
          await failingTool.func({ id: 10 });
        } catch (err) {
          return `caught: ${(err as Error).message}`;
        }
      },
    };

    const wrapped = wrapLangChainAgent(client, executor);
    const result = await wrapped.invoke({});

    expect(result).toBe("caught: operation_aborted");
    expect(failSpy).toHaveBeenCalledWith("eff_001", "operation_aborted");
  });

  it("pauses with AwaitingApprovalError on irreversible tool before tool execution", async () => {
    const approvalErr = new AwaitingApprovalError(
      "delete_data",
      { id: 42 },
      "appr_xyz",
    );
    vi.spyOn(client, "intercept").mockRejectedValue(approvalErr);
    const execSpy = vi.fn();

    const irreversibleTool = {
      name: "delete_data",
      invoke: execSpy,
    };

    const executor = {
      tools: [irreversibleTool],
      invoke: async (_input?: unknown) => irreversibleTool.invoke({ id: 42 }),
    };

    const wrapped = wrapLangChainAgent(client, executor, {
      tiers: { delete_data: ToolTier.Irreversible },
    });

    await expect(wrapped.invoke({})).rejects.toThrow(AwaitingApprovalError);
    expect(execSpy).not.toHaveBeenCalled();
  });

  it("creates automatic session lifecycle if invoked outside an existing session", async () => {
    let activeInLoop: boolean | undefined;
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = {
      name: "session_aware_tool",
      func: vi.fn().mockImplementation(async () => {
        activeInLoop = getCurrentSession() !== undefined;
        return "ok";
      }),
    };

    const executor = {
      tools: [tool],
      invoke: async (_input?: unknown) => tool.func({}),
    };

    expect(getCurrentSession()).toBeUndefined();
    const wrapped = wrapLangChainAgent(client, executor);
    await wrapped.invoke({});

    expect(activeInLoop).toBe(true);
    expect(getCurrentSession()).toBeUndefined();
  });

  it("reuses existing ambient session when invoked inside runWithSession", async () => {
    const ambientSession = new UndoLogSession();
    let capturedSessionId: string | undefined;

    vi.spyOn(client, "intercept").mockImplementation(async (params) => {
      capturedSessionId = params.sessionId ?? getCurrentSession()?.sessionId;
      return mockEffect({ sessionId: capturedSessionId });
    });
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = {
      name: "ambient_tool",
      func: vi.fn().mockResolvedValue("ambient_ok"),
    };

    const executor = {
      tools: [tool],
      invoke: async (_input?: unknown) => tool.func({}),
    };

    const wrapped = wrapLangChainAgent(client, executor);
    await runWithSession(ambientSession, async () => {
      await wrapped.invoke({});
    });

    expect(capturedSessionId).toBe(ambientSession.sessionId);
  });

  it("throws MissingSessionError when autoSession is false and no ambient session exists", async () => {
    const tool = {
      name: "guard_tool",
      func: vi.fn().mockResolvedValue("ok"),
    };

    const executor = {
      tools: [tool],
      invoke: async (_input?: unknown) => tool.func({}),
    };

    const wrapped = wrapLangChainAgent(client, executor, { autoSession: false });
    expect(getCurrentSession()).toBeUndefined();
    await expect(wrapped.invoke({})).rejects.toThrow(MissingSessionError);
  });

  it("supports wrapLangChainTools utility to instrument standalone tool arrays", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const rawTools = [
      { name: "t1", func: vi.fn().mockResolvedValue("r1") },
      { name: "t2", invoke: vi.fn().mockResolvedValue("r2") },
    ];

    const wrappedTools = wrapLangChainTools(client, rawTools);
    expect(wrappedTools.length).toBe(2);
    expect(wrappedTools[0]?._undologWrapped).toBe(true);
    expect(wrappedTools[1]?._undologWrapped).toBe(true);

    // Does not double-wrap already instrumented tools
    const rewrapped = wrapLangChainTools(client, wrappedTools);
    expect(rewrapped[0]).toBe(wrappedTools[0]);
  });

  it("supports wrapLangChainAgent(executor, options) overload with client in options", async () => {
    vi.spyOn(client, "intercept").mockResolvedValue(mockEffect());
    vi.spyOn(client, "commit").mockResolvedValue(mockEffect({ status: "committed" }));

    const tool = {
      name: "overload_tool",
      func: vi.fn().mockResolvedValue("overload_ok"),
    };

    const executor = {
      tools: [tool],
      call: async (_input?: unknown) => tool.func({}),
    };

    const wrapped = wrapLangChainAgent(executor, { client });
    const res = await wrapped.call({});
    expect(res).toBe("overload_ok");
  });
});
