import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { UndoLogClient } from "../../src/client.js";
import type { EffectRecord } from "../../src/client.js";
import { ToolTier } from "../../src/tier.js";
import type { CompensationDescriptor } from "../../src/tier.js";
import {
  AuthenticationError,
  AwaitingApprovalError,
  NotFoundError,
  TimeoutError,
  UndoLogError,
} from "../../src/errors.js";
import { UndoLogSession, runWithSession } from "../../src/session.js";

const NOW = new Date().toISOString();

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const mockEffect: EffectRecord = {
  effectId: "eff_001",
  sessionId: "00000000-0000-0000-0000-000000000000",
  stepIndex: 0,
  toolName: "test_tool",
  signature: "abc123def456",
  status: "pending",
  tier: ToolTier.Safe,
  createdAt: NOW,
};

let client: UndoLogClient;

beforeEach(() => {
  vi.spyOn(globalThis, "fetch");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function buildDefaultParams(overrides?: Record<string, unknown>) {
  return {
    toolName: "send_email",
    args: { to: "user@example.com", subject: "Hello" },
    tier: ToolTier.Safe,
    sessionId: "00000000-0000-0000-0000-000000000000",
    stepIndex: 0,
    ...overrides,
  };
}

function parseRequestBody(): Record<string, unknown> {
  const callBody = (vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit).body;
  return JSON.parse(callBody as string) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

describe("constructor", () => {
  it("creates a client with the default HTTP client when none is provided", () => {
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });
    expect(client).toBeInstanceOf(UndoLogClient);
  });

  it("uses a pre-configured HTTP client when httpClient is provided", () => {
    const mockHttpClient = { request: vi.fn() };
    client = new UndoLogClient({ baseUrl: "http://localhost:8080", httpClient: mockHttpClient });
    expect(client).toBeInstanceOf(UndoLogClient);
  });
});

// ---------------------------------------------------------------------------
// intercept() - normal paths
// ---------------------------------------------------------------------------

describe("intercept()", () => {
  it("returns the effect record from the server for Safe tier", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const result = await client.intercept(buildDefaultParams());

    expect(result).toEqual(mockEffect);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/effects/intercept",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends toolName, args, tier, sessionId, stepIndex, and signature in the body", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    await client.intercept(buildDefaultParams());

    const body = parseRequestBody();
    expect(body.sessionId).toBe("00000000-0000-0000-0000-000000000000");
    expect(body.stepIndex).toBe(0);
    expect(body.toolName).toBe("send_email");
    expect(body.args).toEqual({ to: "user@example.com", subject: "Hello" });
    expect(body.tier).toBe("safe");
    expect(body).toHaveProperty("signature");
    expect(typeof body.signature).toBe("string");
  });

  it("includes compensation descriptor for Compensable tier", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const compensation: CompensationDescriptor = {
      fnName: "undo_send_email",
      fnVersion: "1.0.0",
      args: { to: "user@example.com" },
    };

    await client.intercept(buildDefaultParams({ tier: ToolTier.Compensable, compensation }));

    const body = parseRequestBody();
    expect(body.compensation).toEqual(compensation);
  });

  it("does not include compensation when absent", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    await client.intercept(buildDefaultParams({ tier: ToolTier.Compensable }));

    const body = parseRequestBody();
    expect(body).not.toHaveProperty("compensation");
  });

  it("throws AwaitingApprovalError for Irreversible tier", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, tier: ToolTier.Irreversible }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client
      .intercept(buildDefaultParams({ tier: ToolTier.Irreversible }))
      .catch((e) => e);

    expect(err).toBeInstanceOf(AwaitingApprovalError);
    expect((err as AwaitingApprovalError).toolName).toBe("send_email");
    expect((err as AwaitingApprovalError).approvalId).toBe("eff_001");
    expect((err as AwaitingApprovalError).args).toEqual({
      to: "user@example.com",
      subject: "Hello",
    });
  });

  it("uses explicit sessionId and stepIndex when provided", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    await client.intercept({
      toolName: "test",
      args: {},
      tier: ToolTier.Safe,
      sessionId: "00000000-0000-4000-a000-000000000001",
      stepIndex: 42,
    });

    const body = parseRequestBody();
    expect(body.sessionId).toBe("00000000-0000-4000-a000-000000000001");
    expect(body.stepIndex).toBe(42);
  });

  it("uses active session context when no explicit sessionId or stepIndex", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const session = new UndoLogSession({ sessionId: "00000000-0000-4000-a000-000000000002" });

    await runWithSession(session, () =>
      client.intercept({
        toolName: "test",
        args: {},
        tier: ToolTier.Safe,
      }),
    );

    const body = parseRequestBody();
    expect(body.sessionId).toBe("00000000-0000-4000-a000-000000000002");
    expect(body.stepIndex).toBe(0);
    expect(session.stepIndex).toBe(1);
  });

  it("auto-increments the session step index across multiple intercepts", async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(jsonResponse(mockEffect)),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const session = new UndoLogSession({ sessionId: "00000000-0000-4000-a000-000000000003" });

    await runWithSession(session, async () => {
      await client.intercept({ toolName: "a", args: {}, tier: ToolTier.Safe });
      await client.intercept({ toolName: "b", args: {}, tier: ToolTier.Safe });
    });

    const firstBody = JSON.parse(
      (vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit).body as string,
    ) as Record<string, unknown>;
    const secondBody = JSON.parse(
      (vi.mocked(fetch).mock.calls[1]?.[1] as RequestInit).body as string,
    ) as Record<string, unknown>;

    expect(firstBody.stepIndex).toBe(0);
    expect(firstBody.toolName).toBe("a");
    expect(secondBody.stepIndex).toBe(1);
    expect(secondBody.toolName).toBe("b");
    expect(session.stepIndex).toBe(2);
  });

  it("does not auto-increment when explicit stepIndex is provided", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const session = new UndoLogSession({ sessionId: "00000000-0000-4000-a000-000000000004" });

    await runWithSession(session, () =>
      client.intercept({
        toolName: "a",
        args: {},
        tier: ToolTier.Safe,
        stepIndex: 5,
      }),
    );

    expect(session.stepIndex).toBe(0);
    const body = parseRequestBody();
    expect(body.stepIndex).toBe(5);
  });

  it("generates a random sessionId when no session context and no explicit sessionId", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(mockEffect));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    await client.intercept({
      toolName: "test",
      args: {},
      tier: ToolTier.Safe,
    });

    const body = parseRequestBody();
    expect(body.sessionId).toBeDefined();
    expect(typeof body.sessionId).toBe("string");
    expect(body.sessionId).not.toBe("");
  });
});

// ---------------------------------------------------------------------------
// intercept() - error mapping
// ---------------------------------------------------------------------------

describe("intercept() error mapping", () => {
  it("maps HTTP 401 to AuthenticationError with reason invalid", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "bad key" }, 401));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
    expect((err as AuthenticationError).reason).toBe("invalid");
  });

  it("maps HTTP 403 to AuthenticationError with reason expired", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ error: "token expired" }, 403));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
    expect((err as AuthenticationError).reason).toBe("expired");
  });

  it("maps HTTP 404 to NotFoundError", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "not found" }, 404));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(NotFoundError);
    expect((err as NotFoundError).resourceType).toBe("endpoint");
    expect((err as NotFoundError).resourceId).toContain("/v1/effects/intercept");
  });

  it("maps HTTP 409 to UndoLogError with code CONFLICT", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "conflict" }, 409));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("CONFLICT");
  });

  it("maps HTTP 400 to UndoLogError with code CLIENT_ERROR", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "bad request" }, 400));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("CLIENT_ERROR");
  });

  it("maps HTTP 500 to UndoLogError with code SERVER_ERROR", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "internal error" }, 500));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 0 });

    const err = await client.intercept(buildDefaultParams()).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("SERVER_ERROR");
  });
});

// ---------------------------------------------------------------------------
// intercept() - retry behavior
// ---------------------------------------------------------------------------

describe("intercept() retry behavior", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries on 429 and succeeds on second attempt", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429))
      .mockResolvedValueOnce(jsonResponse(mockEffect));

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.intercept(buildDefaultParams());
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual(mockEffect);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("retries on 503 and succeeds on second attempt", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ error: "unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse(mockEffect));

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.intercept(buildDefaultParams());
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual(mockEffect);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("retries on network error and succeeds on second attempt", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(jsonResponse(mockEffect));

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.intercept(buildDefaultParams());
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual(mockEffect);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("exhausts retries on persistent 429 and throws last error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockImplementation(() =>
      Promise.resolve(jsonResponse({ error: "rate limited" }, 429)),
    );

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 2 });

    const promise = client.intercept(buildDefaultParams()).catch((e) => e);
    await vi.advanceTimersByTimeAsync(15000);
    const err = await promise;

    expect(err).toBeInstanceOf(UndoLogError);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("exhausts retries on persistent network error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockImplementation(() => Promise.reject(new TypeError("fetch failed")));

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 2 });

    const promise = client.intercept(buildDefaultParams()).catch((e) => e);
    await vi.advanceTimersByTimeAsync(15000);
    const err = await promise;

    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("NETWORK_ERROR");
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("does not retry on non-retryable 4xx", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValue(jsonResponse({ message: "bad request" }, 400));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080", maxRetries: 3 });

    await expect(client.intercept(buildDefaultParams())).rejects.toThrow(UndoLogError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("throws TimeoutError when the request exceeds the deadline", async () => {
    vi.mocked(fetch).mockImplementation(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal as AbortSignal | undefined;
          signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted", "AbortError"));
          });
        }),
    );

    client = new UndoLogClient({ baseUrl: "http://localhost:8080", timeout: 50 });

    const promise = client.intercept(buildDefaultParams()).catch((e) => e);
    await vi.advanceTimersByTimeAsync(100);
    const err = await promise;

    expect(err).toBeInstanceOf(TimeoutError);
    expect((err as TimeoutError).timeoutMs).toBe(50);
  });
});

// ---------------------------------------------------------------------------
// commit()
// ---------------------------------------------------------------------------

describe("commit()", () => {
  it("POSTs to /v1/effects/commit and returns the updated effect record", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, status: "committed" }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const result = await client.commit("eff_001");

    expect(result.status).toBe("committed");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/effects/commit",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ effectId: "eff_001" }),
      }),
    );
  });

  it("propagates HTTP errors from the server", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "effect not found" }, 404));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.commit("eff_missing").catch((e) => e);
    expect(err).toBeInstanceOf(NotFoundError);
  });
});

// ---------------------------------------------------------------------------
// fail()
// ---------------------------------------------------------------------------

describe("fail()", () => {
  it("POSTs to /v1/effects/fail with effectId and error message", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, status: "failed" }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const result = await client.fail("eff_001", "Something went wrong");

    expect(result.status).toBe("failed");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/effects/fail",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          effectId: "eff_001",
          error: "Something went wrong",
        }),
      }),
    );
  });

  it("propagates HTTP errors from the server", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "unauthorized" }, 401));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.fail("eff_001", "error").catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
  });
});

// ---------------------------------------------------------------------------
// approve()
// ---------------------------------------------------------------------------

describe("approve()", () => {
  it("POSTs to /v1/effects/approve with approvalId", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, status: "approved" }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const result = await client.approve("eff_001");

    expect(result.status).toBe("approved");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/effects/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approvalId: "eff_001" }),
      }),
    );
  });

  it("propagates HTTP errors from the server", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "conflict" }, 409));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.approve("eff_001").catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("CONFLICT");
  });
});

// ---------------------------------------------------------------------------
// reject()
// ---------------------------------------------------------------------------

describe("reject()", () => {
  it("POSTs to /v1/effects/reject with approvalId and no reason", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, status: "rejected" }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const result = await client.reject("eff_001");

    expect(result.status).toBe("rejected");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/v1/effects/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approvalId: "eff_001" }),
      }),
    );
  });

  it("includes reason in the body when provided", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...mockEffect, status: "rejected" }),
    );
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    await client.reject("eff_001", "User declined");

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        body: JSON.stringify({ approvalId: "eff_001", reason: "User declined" }),
      }),
    );
  });

  it("propagates HTTP errors from the server", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "forbidden" }, 403));
    client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

    const err = await client.reject("eff_001").catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
    expect((err as AuthenticationError).reason).toBe("expired");
  });
});
