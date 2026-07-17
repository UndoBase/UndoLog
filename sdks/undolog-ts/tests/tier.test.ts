import { describe, it, expect } from "vitest";
import { ToolTier, CompensationDescriptor, requiresApproval, isCompensable, isSafe } from "../src/tier.js";

describe("ToolTier", () => {
  it("has the expected string values", () => {
    expect(ToolTier.Safe).toBe("safe");
    expect(ToolTier.Compensable).toBe("compensable");
    expect(ToolTier.Irreversible).toBe("irreversible");
  });
});

describe("CompensationDescriptor", () => {
  it("accepts a minimal descriptor with only fnName", () => {
    const d: CompensationDescriptor = { fnName: "undoSendEmail" };
    expect(d.fnName).toBe("undoSendEmail");
    expect(d.fnVersion).toBeUndefined();
  });

  it("accepts a fully populated descriptor", () => {
    const d: CompensationDescriptor = {
      fnName: "undoTransferFunds",
      fnVersion: "2.1.0",
      args: { from: "a", to: "b", amount: 100 },
      maxRetries: 5,
      retryBackoffMs: 500,
    };
    expect(d.fnVersion).toBe("2.1.0");
    expect(d.args).toEqual({ from: "a", to: "b", amount: 100 });
    expect(d.maxRetries).toBe(5);
    expect(d.retryBackoffMs).toBe(500);
  });

  it("defaults optional fields when absent", () => {
    const d: CompensationDescriptor = { fnName: "test" };
    expect(d.fnVersion).toBeUndefined();
    expect(d.args).toBeUndefined();
    expect(d.maxRetries).toBeUndefined();
    expect(d.retryBackoffMs).toBeUndefined();
  });
});

describe("requiresApproval", () => {
  it("returns true for Irreversible", () => {
    expect(requiresApproval(ToolTier.Irreversible)).toBe(true);
  });

  it("returns false for Safe", () => {
    expect(requiresApproval(ToolTier.Safe)).toBe(false);
  });

  it("returns false for Compensable", () => {
    expect(requiresApproval(ToolTier.Compensable)).toBe(false);
  });
});

describe("isCompensable", () => {
  it("returns true for Compensable", () => {
    expect(isCompensable(ToolTier.Compensable)).toBe(true);
  });

  it("returns false for Safe", () => {
    expect(isCompensable(ToolTier.Safe)).toBe(false);
  });

  it("returns false for Irreversible", () => {
    expect(isCompensable(ToolTier.Irreversible)).toBe(false);
  });
});

describe("isSafe", () => {
  it("returns true for Safe", () => {
    expect(isSafe(ToolTier.Safe)).toBe(true);
  });

  it("returns false for Compensable", () => {
    expect(isSafe(ToolTier.Compensable)).toBe(false);
  });

  it("returns false for Irreversible", () => {
    expect(isSafe(ToolTier.Irreversible)).toBe(false);
  });
});
