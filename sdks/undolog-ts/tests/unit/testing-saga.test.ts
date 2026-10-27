/** Unit tests for the multi-step saga compensation test harness.
 *
 * Covers TestSaga, createTestSaga, testSaga, and CompensationTestHarness:
 * - Sequential registration with inverse compensation handlers
 * - LIFO reverse chronological rollback execution
 * - Delivery of inverse arguments to compensation handlers
 * - Approval gating on irreversible steps (AwaitingApprovalError)
 * - Transient failure simulation and retry budget exhaustion
 * - Permanent compensation failures halting remaining rollbacks
 * - Parity helpers and edge case validation
 *
 * @module
 */

import { describe, expect, it, vi } from "vitest";
import { AwaitingApprovalError, ValidationError } from "../../src/errors.js";
import { ToolTier } from "../../src/tier.js";
import {
  CompensationTestHarness,
  DEFAULT_MAX_RETRIES,
  TestSaga,
  createTestSaga,
  testCompensation,
  testSaga,
} from "../../src/testing/saga.js";

describe("TestSaga registration and construction", () => {
  it("exports DEFAULT_MAX_RETRIES equal to 3", () => {
    expect(DEFAULT_MAX_RETRIES).toBe(3);
  });

  it("can be constructed directly via new TestSaga", () => {
    const saga = new TestSaga();
    expect(saga.steps).toEqual([]);
    expect(saga.report).toBeUndefined();
  });

  it("initializes an empty saga when constructed without arguments", () => {
    const saga = createTestSaga();
    expect(saga.steps).toEqual([]);
    expect(saga.report).toBeUndefined();
  });

  it("initializes with steps passed to constructor array", () => {
    const saga = createTestSaga([
      { toolName: "step_1" },
      { toolName: "step_2" },
    ]);
    expect(saga.steps.length).toBe(2);
    expect(saga.steps[0]?.toolName).toBe("step_1");
    expect(saga.steps[1]?.toolName).toBe("step_2");
  });

  it("initializes with options containing initial steps and preApproved list", () => {
    const saga = createTestSaga({
      defaultMaxRetries: 5,
      preApproved: ["irreversible_tool"],
      steps: [{ toolName: "first_tool" }],
    });
    expect(saga.steps.length).toBe(1);
    expect(
      saga.isApproved(
        { toolName: "irreversible_tool", tier: ToolTier.Irreversible },
        0,
      ),
    ).toBe(true);
  });

  it("rejects steps with empty or invalid tool names", () => {
    const saga = createTestSaga();
    expect(() => saga.addStep({ toolName: "" })).toThrow(ValidationError);
  });

  it("rejects steps with negative stackPosition", () => {
    const saga = createTestSaga();
    expect(() =>
      saga.addStep({ toolName: "invalid_pos", stackPosition: -1 }),
    ).toThrow(ValidationError);
  });
});

describe("TestSaga LIFO rollback and inverse arguments", () => {
  it("executes registered compensations in reverse chronological order (LIFO)", async () => {
    const executionOrder: string[] = [];
    const saga = createTestSaga();

    saga
      .addStep({
        toolName: "reserve_flight",
        compensation: async () => {
          executionOrder.push("cancel_flight");
        },
      })
      .addStep({
        toolName: "reserve_hotel",
        compensation: async () => {
          executionOrder.push("cancel_hotel");
        },
      })
      .addStep({
        toolName: "reserve_car",
        compensation: async () => {
          executionOrder.push("cancel_car");
        },
      });

    const report = await saga.simulateFailureAt(2);

    expect(report.sessionResult).toBe("compensated");
    expect(report.compensationOrder).toEqual(["reserve_hotel", "reserve_flight"]);
    expect(executionOrder).toEqual(["cancel_hotel", "cancel_flight"]);
  });

  it("records failureError in report when provided to simulateFailureAt", async () => {
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "step_0",
        compensation: async () => {},
      })
      .addStep({
        toolName: "step_1",
      });

    const report = await saga.simulateFailureAt(1, "Out of funds");
    expect(report.failureError).toBe("Out of funds");
  });

  it("verifies delivery of inverse arguments to compensation functions", async () => {
    const flightUndoSpy = vi.fn();
    const hotelUndoSpy = vi.fn();

    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "reserve_flight",
        args: { bookingId: "FL-101", seats: 2 },
        compensationArgs: { bookingId: "FL-101", refundFee: 0 },
        compensation: flightUndoSpy,
      })
      .addStep({
        toolName: "reserve_hotel",
        args: { roomId: "RM-502", nights: 3 },
        compensation: hotelUndoSpy,
      })
      .addStep({
        toolName: "charge_payment",
      });

    const report = await saga.simulateFailureAt(2);

    expect(report.sessionResult).toBe("compensated");
    expect(hotelUndoSpy).toHaveBeenCalledTimes(1);
    expect(hotelUndoSpy).toHaveBeenCalledWith({ roomId: "RM-502", nights: 3 });

    expect(flightUndoSpy).toHaveBeenCalledTimes(1);
    expect(flightUndoSpy).toHaveBeenCalledWith({ bookingId: "FL-101", refundFee: 0 });

    expect(report.executedCompensations).toEqual([
      {
        toolName: "reserve_hotel",
        stackPosition: 1,
        args: { roomId: "RM-502", nights: 3 },
        attempts: 1,
      },
      {
        toolName: "reserve_flight",
        stackPosition: 0,
        args: { bookingId: "FL-101", refundFee: 0 },
        attempts: 1,
      },
    ]);
  });

  it("simulates a 5-step workflow where step 4 fails, rolling back steps 3, 2, and 1", async () => {
    const rolledBack: string[] = [];
    const saga = createTestSaga();

    // Step 0: Safe tool (e.g. read / search), no compensation
    saga.addStep({
      toolName: "search_catalog",
      tier: ToolTier.Safe,
      args: { query: "laptop" },
    });

    // Step 1: Compensable tool
    saga.addStep({
      toolName: "hold_item",
      tier: ToolTier.Compensable,
      args: { itemId: "item-99" },
      compensationArgs: { itemId: "item-99", releaseReason: "saga_failed" },
      compensation: async (args: any) => {
        rolledBack.push(`release:${args.itemId}`);
      },
    });

    // Step 2: Compensable tool
    saga.addStep({
      toolName: "apply_discount",
      tier: ToolTier.Compensable,
      args: { code: "SPRING20" },
      compensation: async (args: any) => {
        rolledBack.push(`revert_discount:${args.code}`);
      },
    });

    // Step 3: Compensable tool
    saga.addStep({
      toolName: "authorize_card",
      tier: ToolTier.Compensable,
      args: { authId: "auth-456" },
      compensationArgs: { voidId: "auth-456" },
      compensation: async (args: any) => {
        rolledBack.push(`void_auth:${args.voidId}`);
      },
    });

    // Step 4: Tool that triggers the failure
    saga.addStep({
      toolName: "ship_package",
      tier: ToolTier.Compensable,
      args: { address: "123 Main St" },
    });

    const report = await saga.simulateFailureAt(4);

    expect(report.sessionResult).toBe("compensated");
    expect(report.compensationOrder).toEqual([
      "authorize_card",
      "apply_discount",
      "hold_item",
    ]);
    expect(rolledBack).toEqual([
      "void_auth:auth-456",
      "revert_discount:SPRING20",
      "release:item-99",
    ]);
  });

  it("respects explicit stackPosition descending order in rollback", async () => {
    const order: string[] = [];
    const saga = createTestSaga([
      {
        toolName: "step_A",
        stackPosition: 10,
        compensation: async () => {
          order.push("undo_A");
        },
      },
      {
        toolName: "step_B",
        stackPosition: 30,
        compensation: async () => {
          order.push("undo_B");
        },
      },
      {
        toolName: "step_C",
        stackPosition: 20,
        compensation: async () => {
          order.push("undo_C");
        },
      },
    ]);

    const report = await saga.rollback();
    expect(report.compensationOrder).toEqual(["step_B", "step_C", "step_A"]);
    expect(order).toEqual(["undo_B", "undo_C", "undo_A"]);
  });
});

describe("TestSaga approval requirement on irreversible tools", () => {
  it("throws AwaitingApprovalError on irreversible step encountered without pre-approval", async () => {
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "prepare_migration",
        tier: ToolTier.Compensable,
        compensation: async () => {},
      })
      .addStep({
        toolName: "drop_production_table",
        tier: ToolTier.Irreversible,
        args: { table: "customers" },
      })
      .addStep({
        toolName: "verify_migration",
      });

    await expect(saga.simulateFailureAt(2)).rejects.toThrow(
      AwaitingApprovalError,
    );

    try {
      await saga.simulateFailureAt(2);
    } catch (err) {
      expect(err).toBeInstanceOf(AwaitingApprovalError);
      const appErr = err as AwaitingApprovalError;
      expect(appErr.toolName).toBe("drop_production_table");
      expect(appErr.args).toEqual({ table: "customers" });
      expect(appErr.approvalId).toMatch(/^appr_/);
    }
  });

  it("proceeds past irreversible step when pre-approved by toolName", async () => {
    const undoSpy = vi.fn();
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "prepare_migration",
        tier: ToolTier.Compensable,
        compensation: undoSpy,
      })
      .addStep({
        toolName: "drop_production_table",
        tier: ToolTier.Irreversible,
      })
      .addStep({
        toolName: "verify_cleanup",
      });

    saga.preApprove("drop_production_table");
    const report = await saga.simulateFailureAt(2);

    expect(report.sessionResult).toBe("compensated");
    expect(undoSpy).toHaveBeenCalledTimes(1);
    expect(report.compensationOrder).toEqual(["prepare_migration"]);
  });

  it("proceeds past irreversible step when pre-approved by position or index", async () => {
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "step_0",
        compensation: async () => {},
      })
      .addStep({
        toolName: "irreversible_1",
        tier: ToolTier.Irreversible,
        stackPosition: 100,
      })
      .addStep({
        toolName: "step_2",
      });

    saga.preApprove(100);
    const report = await saga.simulateFailureAt(2);
    expect(report.sessionResult).toBe("compensated");
  });

  it("preApproveAll() approves all irreversible tools in the saga", async () => {
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "safe_0",
        compensation: async () => {},
      })
      .addStep({
        toolName: "irrev_1",
        tier: ToolTier.Irreversible,
      })
      .addStep({
        toolName: "irrev_2",
        tier: ToolTier.Irreversible,
      })
      .addStep({
        toolName: "fail_at_3",
      });

    saga.preApproveAll();
    const report = await saga.simulateFailureAt(3);
    expect(report.sessionResult).toBe("compensated");
  });

  it("clearApprovals() re-enables approval checks", async () => {
    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "irrev_tool",
        tier: ToolTier.Irreversible,
      })
      .addStep({
        toolName: "step_1",
      });

    saga.preApprove("irrev_tool");
    saga.clearApprovals();

    await expect(saga.simulateFailureAt(1)).rejects.toThrow(
      AwaitingApprovalError,
    );
  });
});

describe("TestSaga transient retry simulation and failure handling", () => {
  it("retries transient failure and succeeds within retry budget", async () => {
    let callCount = 0;
    const transientCompensation = async () => {
      callCount++;
      if (callCount < 3) {
        throw new Error("Temporary network timeout");
      }
    };

    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "transient_service",
        maxRetries: 3,
        compensation: transientCompensation,
      })
      .addStep({
        toolName: "trigger_fail",
      });

    const report = await saga.simulateFailureAt(1);

    expect(report.sessionResult).toBe("compensated");
    expect(report.steps[0]?.attempts).toBe(3);
    expect(report.steps[0]?.state).toBe("compensated");
    expect(callCount).toBe(3);
  });

  it("halts rollback sequence when an entry permanently exhausts its retry budget", async () => {
    const step0Undo = vi.fn();
    let failingAttempts = 0;

    const permanentFailCompensation = async () => {
      failingAttempts++;
      throw new Error("Persistent database failure");
    };

    const saga = createTestSaga();
    saga
      .addStep({
        toolName: "step_0",
        compensation: step0Undo,
      })
      .addStep({
        toolName: "step_1_fails",
        maxRetries: 2,
        compensation: permanentFailCompensation,
      })
      .addStep({
        toolName: "step_2",
      });

    const report = await saga.simulateFailureAt(2);

    expect(report.sessionResult).toBe("halted");
    expect(failingAttempts).toBe(2);
    expect(report.steps[0]?.toolName).toBe("step_1_fails");
    expect(report.steps[0]?.state).toBe("failed");
    expect(report.steps[0]?.error).toBe("Persistent database failure");
    expect(report.steps[0]?.attempts).toBe(2);

    // Remaining step_0 in rollback is untouched because rollback halted
    expect(step0Undo).not.toHaveBeenCalled();
    expect(report.compensationOrder).toEqual([]);
  });

  it("reports getReport() and throws if getReport() called before run", () => {
    const saga = createTestSaga();
    saga.addStep({ toolName: "some_tool" });

    expect(() => saga.getReport()).toThrow(ValidationError);
  });
});

describe("Edge cases and validation", () => {
  it("rejects simulateFailureAt when saga has no steps", async () => {
    const saga = createTestSaga();
    await expect(saga.simulateFailureAt(0)).rejects.toThrow(ValidationError);
  });

  it("rejects simulateFailureAt with out-of-range index", async () => {
    const saga = createTestSaga([{ toolName: "step_0" }]);
    await expect(saga.simulateFailureAt(-1)).rejects.toThrow(ValidationError);
    await expect(saga.simulateFailureAt(1)).rejects.toThrow(ValidationError);
  });

  it("returns clean report when simulateFailureAt(0) has no preceding steps", async () => {
    const saga = createTestSaga([{ toolName: "step_0" }]);
    const report = await saga.simulateFailureAt(0);
    expect(report.sessionResult).toBe("compensated");
    expect(report.steps).toEqual([]);
    expect(report.compensationOrder).toEqual([]);
    expect(saga.executedCompensations).toEqual([]);
  });

  it("rejects rollback when duplicate stackPosition values are supplied", async () => {
    const saga = createTestSaga([
      { toolName: "tool_1", stackPosition: 5, compensation: async () => {} },
      { toolName: "tool_2", stackPosition: 5, compensation: async () => {} },
    ]);
    await expect(saga.rollback()).rejects.toThrow(ValidationError);
  });

  it("executes all steps forward successfully with execute()", async () => {
    const actions: string[] = [];
    const saga = createTestSaga([
      {
        toolName: "act_1",
        action: async () => {
          actions.push("one");
        },
      },
      {
        toolName: "act_2",
        action: async () => {
          actions.push("two");
        },
      },
    ]);

    const report = await saga.execute();
    expect(report.sessionResult).toBe("compensated");
    expect(actions).toEqual(["one", "two"]);
  });

  it("triggers rollback when an action in execute() throws", async () => {
    const rolledBack: string[] = [];
    const saga = createTestSaga([
      {
        toolName: "act_1",
        compensation: async () => {
          rolledBack.push("undo_1");
        },
      },
      {
        toolName: "act_2",
        action: async () => {
          throw new Error("Action failure");
        },
      },
    ]);

    const report = await saga.execute();
    expect(report.sessionResult).toBe("compensated");
    expect(rolledBack).toEqual(["undo_1"]);
  });
});

describe("CompensationTestHarness and testCompensation", () => {
  it("tracks call count, arguments, and return values", async () => {
    const fn = async (args: { count: number }) => ({ doubled: args.count * 2 });
    const harness = new CompensationTestHarness(fn);

    const res = await harness.execute({ count: 5 });
    expect(res).toEqual({ doubled: 10 });
    expect(harness.callCount).toBe(1);
    expect(harness.calls).toEqual([{ count: 5 }]);
    expect(harness.results).toEqual([{ doubled: 10 }]);

    harness.assertCalledWith({ count: 5 });
    await harness.assertIdempotent({ count: 5 });
  });

  it("executeWithRetry retries upon error up to maxRetries", async () => {
    let attempts = 0;
    const flaky = async () => {
      attempts++;
      if (attempts < 2) {
        throw new Error("fail once");
      }
      return "recovered";
    };

    const harness = new CompensationTestHarness(flaky, 3);
    const result = await harness.executeWithRetry({});
    expect(result).toBe("recovered");
    expect(attempts).toBe(2);
  });

  it("executeWithRetry re-throws when retries are exhausted", async () => {
    const alwaysFail = async () => {
      throw new Error("fatal");
    };
    const harness = new CompensationTestHarness(alwaysFail, 2);
    await expect(harness.executeWithRetry({})).rejects.toThrow("fatal");
  });

  it("testCompensation reports executionCount, retryCount, and idempotency", async () => {
    let runs = 0;
    const compFn = async (args: { id: string }) => {
      runs++;
      return { status: "undone", id: args.id };
    };

    const report = await testCompensation(compFn, { id: "item-1" }, { retries: 2 });
    expect(report.finalState).toBe("compensated");
    expect(report.executionCount).toBe(2); // Initial run + idempotency check
    expect(report.retryCount).toBe(0);
    expect(report.result).toEqual({ status: "undone", id: "item-1" });
  });

  it("testSaga convenience function rolls back session effects", async () => {
    const compensated: string[] = [];
    const report = await testSaga([
      {
        toolName: "step_A",
        stackPosition: 1,
        compensation: async () => {
          compensated.push("undo_A");
        },
      },
      {
        toolName: "step_B",
        stackPosition: 2,
        compensation: async () => {
          compensated.push("undo_B");
        },
      },
    ]);

    expect(report.sessionResult).toBe("compensated");
    expect(report.compensationOrder).toEqual(["step_B", "step_A"]);
    expect(compensated).toEqual(["undo_B", "undo_A"]);
  });
});
