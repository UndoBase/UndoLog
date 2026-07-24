import { describe, it, expect } from "vitest";
import {
  UndoLogSession,
  getCurrentSession,
  runWithSession,
} from "../../src/session.js";

// ── Session lifecycle ───────────────────────────────────────────────────────

describe("UndoLogSession lifecycle", () => {
  it("creates a session with a generated UUID", () => {
    const session = new UndoLogSession();
    expect(session.sessionId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it("uses the provided sessionId", () => {
    const id = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
    const session = new UndoLogSession({ sessionId: id });
    expect(session.sessionId).toBe(id);
  });

  it("starts with stepIndex 0", () => {
    const session = new UndoLogSession();
    expect(session.stepIndex).toBe(0);
  });

  it("nextStep returns the incremented value", () => {
    const session = new UndoLogSession();
    expect(session.nextStep()).toBe(1);
    expect(session.nextStep()).toBe(2);
    expect(session.nextStep()).toBe(3);
  });

  it("stepIndex reflects the current step after nextStep", () => {
    const session = new UndoLogSession();
    expect(session.stepIndex).toBe(0);
    session.nextStep();
    expect(session.stepIndex).toBe(1);
    session.nextStep();
    expect(session.stepIndex).toBe(2);
  });

  it("stores metadata", () => {
    const metadata = { user: "alice", role: "admin" };
    const session = new UndoLogSession({ metadata });
    expect(session.metadata).toEqual(metadata);
  });

  it("metadata defaults to empty object", () => {
    const session = new UndoLogSession();
    expect(session.metadata).toEqual({});
  });

  it("does not mutate the original metadata object", () => {
    const original = { key: "value" };
    const session = new UndoLogSession({ metadata: original });
    original.key = "modified";
    expect(session.metadata).toEqual({ key: "value" });
  });

  it("has a startTime near the construction time", () => {
    const before = Date.now();
    const session = new UndoLogSession();
    const after = Date.now();
    expect(session.startTime).toBeGreaterThanOrEqual(before);
    expect(session.startTime).toBeLessThanOrEqual(after);
  });

  it("each session has a unique UUID", () => {
    const s1 = new UndoLogSession();
    const s2 = new UndoLogSession();
    expect(s1.sessionId).not.toBe(s2.sessionId);
  });
});

// ── Step monotonicity ────────────────────────────────────────────────────────

describe("step monotonicity", () => {
  it("steps are strictly increasing", () => {
    const session = new UndoLogSession();
    const values: number[] = [];
    for (let i = 0; i < 100; i++) {
      values.push(session.nextStep());
    }
    for (let i = 1; i < values.length; i++) {
      expect(values[i]).toBeGreaterThan(values[i - 1]);
    }
  });

  it("parallel nextStep calls produce unique step values", async () => {
    const session = new UndoLogSession();
    const promises: Promise<number>[] = [];
    for (let i = 0; i < 50; i++) {
      promises.push(Promise.resolve().then(() => session.nextStep()));
    }
    const results = await Promise.all(promises);
    const unique = new Set(results);
    expect(unique.size).toBe(50);
    expect(Math.max(...results)).toBe(50);
  });

  it("stepIndex never decreases", () => {
    const session = new UndoLogSession();
    const snapshots: number[] = [];
    for (let i = 0; i < 10; i++) {
      snapshots.push(session.stepIndex);
      session.nextStep();
    }
    for (let i = 1; i < snapshots.length; i++) {
      expect(snapshots[i]).toBeGreaterThanOrEqual(snapshots[i - 1]);
    }
  });

  it("different sessions have independent step counters", () => {
    const s1 = new UndoLogSession();
    const s2 = new UndoLogSession();
    s1.nextStep();
    s1.nextStep();
    s2.nextStep();
    expect(s1.stepIndex).toBe(2);
    expect(s2.stepIndex).toBe(1);
  });
});

// ── Context propagation ──────────────────────────────────────────────────────

describe("context propagation", () => {
  it("makes session available via getCurrentSession", () => {
    const session = new UndoLogSession({
      sessionId: "00000000-0000-0000-0000-000000000000",
    });
    runWithSession(session, () => {
      expect(getCurrentSession()).toBe(session);
    });
  });

  it("returns the callback result", () => {
    const session = new UndoLogSession();
    const result = runWithSession(session, () => 42);
    expect(result).toBe(42);
  });

  it("creates a session from SessionOptions", () => {
    runWithSession({ metadata: { source: "test" } }, () => {
      const current = getCurrentSession();
      expect(current).toBeDefined();
      expect(current!.metadata).toEqual({ source: "test" });
    });
  });

  it("returns undefined outside a session context", () => {
    expect(getCurrentSession()).toBeUndefined();
  });

  it("propagates context through async/await", async () => {
    const session = new UndoLogSession();
    await runWithSession(session, async () => {
      expect(getCurrentSession()).toBe(session);
      await Promise.resolve();
      expect(getCurrentSession()).toBe(session);
    });
  });

  it("propagates context through Promise chains", async () => {
    const session = new UndoLogSession();
    await runWithSession(session, async () => {
      const result = await Promise.resolve(1)
        .then((v) => {
          expect(getCurrentSession()).toBe(session);
          return v + 1;
        })
        .then((v) => {
          expect(getCurrentSession()).toBe(session);
          return v + 1;
        });
      expect(result).toBe(3);
    });
  });

  it("propagates context through Promise.all", async () => {
    const session = new UndoLogSession();
    await runWithSession(session, async () => {
      const results = await Promise.all([
        Promise.resolve(1).then(() => {
          expect(getCurrentSession()).toBe(session);
          return "a";
        }),
        Promise.resolve(2).then(() => {
          expect(getCurrentSession()).toBe(session);
          return "b";
        }),
      ]);
      expect(results).toEqual(["a", "b"]);
    });
  });

  it("propagates context through setTimeout", async () => {
    const session = new UndoLogSession();
    await new Promise<void>((resolve) => {
      runWithSession(session, () => {
        setTimeout(() => {
          expect(getCurrentSession()).toBe(session);
          resolve();
        }, 0);
      });
    });
  });

  it("propagates context through queueMicrotask", async () => {
    const session = new UndoLogSession();
    await new Promise<void>((resolve) => {
      runWithSession(session, () => {
        queueMicrotask(() => {
          expect(getCurrentSession()).toBe(session);
          resolve();
        });
      });
    });
  });

  it("restores context after a thrown error", () => {
    const session = new UndoLogSession();
    expect(() => {
      runWithSession(session, () => {
        throw new Error("test error");
      });
    }).toThrow("test error");
    expect(getCurrentSession()).toBeUndefined();
  });

  it("concurrent runWithSession calls do not leak context across branches", async () => {
    const s1 = new UndoLogSession({
      sessionId: "10000000-0000-0000-0000-000000000001",
    });
    const s2 = new UndoLogSession({
      sessionId: "20000000-0000-0000-0000-000000000002",
    });
    const results = await Promise.all([
      runWithSession(s1, async () => {
        await new Promise((r) => setTimeout(r, 10));
        return getCurrentSession()?.sessionId;
      }),
      runWithSession(s2, async () => {
        await new Promise((r) => setTimeout(r, 5));
        return getCurrentSession()?.sessionId;
      }),
    ]);
    expect(results).toContain(s1.sessionId);
    expect(results).toContain(s2.sessionId);
  });
});

// ── Nesting ──────────────────────────────────────────────────────────────────

describe("nesting", () => {
  it("isolates session contexts across nested calls", () => {
    const outer = new UndoLogSession({
      sessionId: "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
    });
    const inner = new UndoLogSession({
      sessionId: "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
    });

    runWithSession(outer, () => {
      expect(getCurrentSession()).toBe(outer);
      runWithSession(inner, () => {
        expect(getCurrentSession()).toBe(inner);
      });
      expect(getCurrentSession()).toBe(outer);
    });
  });

  it("restores outer session after nested async callback", async () => {
    const outer = new UndoLogSession();
    const inner = new UndoLogSession();
    await runWithSession(outer, async () => {
      expect(getCurrentSession()).toBe(outer);
      await runWithSession(inner, async () => {
        expect(getCurrentSession()).toBe(inner);
        await Promise.resolve();
        expect(getCurrentSession()).toBe(inner);
      });
      expect(getCurrentSession()).toBe(outer);
    });
  });

  it("supports deep nesting at 3 levels", () => {
    const s1 = new UndoLogSession({
      sessionId: "10000000-0000-0000-0000-000000000000",
    });
    const s2 = new UndoLogSession({
      sessionId: "20000000-0000-0000-0000-000000000000",
    });
    const s3 = new UndoLogSession({
      sessionId: "30000000-0000-0000-0000-000000000000",
    });

    runWithSession(s1, () => {
      expect(getCurrentSession()).toBe(s1);
      runWithSession(s2, () => {
        expect(getCurrentSession()).toBe(s2);
        runWithSession(s3, () => {
          expect(getCurrentSession()).toBe(s3);
        });
        expect(getCurrentSession()).toBe(s2);
      });
      expect(getCurrentSession()).toBe(s1);
    });
  });

  it("restores outer context after error in nested scope", () => {
    const outer = new UndoLogSession();
    const inner = new UndoLogSession();
    runWithSession(outer, () => {
      expect(() => {
        runWithSession(inner, () => {
          throw new Error("nested error");
        });
      }).toThrow("nested error");
      expect(getCurrentSession()).toBe(outer);
    });
  });

  it("reuses the same session instance when passed directly", () => {
    const session = new UndoLogSession();
    const returned = runWithSession(session, () => getCurrentSession());
    expect(returned).toBe(session);
  });
});

// ── Step counter overflow guard ──────────────────────────────────────────────

describe("step counter overflow guard", () => {
  it("does not throw when stepIndex is below MAX_SAFE_INTEGER", () => {
    const session = new UndoLogSession({
      stepIndex: Number.MAX_SAFE_INTEGER - 1,
    });
    expect(() => session.nextStep()).not.toThrow();
    expect(session.stepIndex).toBe(Number.MAX_SAFE_INTEGER);
  });

  it("throws RangeError when stepIndex equals MAX_SAFE_INTEGER", () => {
    const session = new UndoLogSession({
      stepIndex: Number.MAX_SAFE_INTEGER,
    });
    expect(() => session.nextStep()).toThrow(RangeError);
    expect(() => session.nextStep()).toThrow(
      "Step counter overflow: cannot exceed Number.MAX_SAFE_INTEGER",
    );
    expect(session.stepIndex).toBe(Number.MAX_SAFE_INTEGER);
  });

  it("throws RangeError when stepIndex exceeds MAX_SAFE_INTEGER", () => {
    const session = new UndoLogSession({
      stepIndex: Number.MAX_SAFE_INTEGER + 1,
    });
    expect(() => session.nextStep()).toThrow(RangeError);
    expect(session.stepIndex).toBe(Number.MAX_SAFE_INTEGER + 1);
  });

  it("rejects initial stepIndex set below zero", () => {
    const session = new UndoLogSession({ stepIndex: -1 });
    expect(session.stepIndex).toBe(-1);
    expect(() => session.nextStep()).not.toThrow();
  });

  it("preserves the step after a single guarded call near the boundary", () => {
    const session = new UndoLogSession({
      stepIndex: Number.MAX_SAFE_INTEGER - 1,
    });
    session.nextStep();
    expect(session.stepIndex).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => session.nextStep()).toThrow(RangeError);
  });
});
