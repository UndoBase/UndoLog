import { describe, it, expect } from "vitest";
import {
  UndoLogSession,
  getCurrentSession,
  runWithSession,
} from "../src/session.js";

describe("UndoLogSession", () => {
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

  it("stores metadata", () => {
    const metadata = { user: "alice", role: "admin" };
    const session = new UndoLogSession({ metadata });
    expect(session.metadata).toEqual(metadata);
  });

  it("metadata defaults to empty object", () => {
    const session = new UndoLogSession();
    expect(session.metadata).toEqual({});
  });

  it("has a startTime near the construction time", () => {
    const before = Date.now();
    const session = new UndoLogSession();
    const after = Date.now();
    expect(session.startTime).toBeGreaterThanOrEqual(before);
    expect(session.startTime).toBeLessThanOrEqual(after);
  });
});

describe("runWithSession", () => {
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

  it("returns undefined outside a session context", () => {
    expect(getCurrentSession()).toBeUndefined();
  });

  it("supports async callbacks", async () => {
    const session = new UndoLogSession();
    const result = await runWithSession(session, async () => {
      expect(getCurrentSession()).toBe(session);
      return "done";
    });
    expect(result).toBe("done");
  });
});
