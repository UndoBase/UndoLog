import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createHttpClient } from "../src/http.js";
import type { HttpClient } from "../src/http.js";
import {
  AuthenticationError,
  NotFoundError,
  TimeoutError,
  UndoLogError,
} from "../src/errors.js";

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createAbortableFetch(hangForever = false) {
  return vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    return new Promise<Response>((resolve, reject) => {
      const signal = init?.signal as AbortSignal | undefined;
      if (signal?.aborted) {
        reject(new DOMException("The operation was aborted", "AbortError"));
        return;
      }
      const onAbort = () => {
        reject(new DOMException("The operation was aborted", "AbortError"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      if (!hangForever) {
        // Resolve on next microtask so tests can set up mocks first
        queueMicrotask(() => {
          signal?.removeEventListener("abort", onAbort);
          resolve(jsonResponse({ ok: true }));
        });
      }
    });
  });
}

let client: HttpClient;

beforeEach(() => {
  vi.spyOn(globalThis, "fetch");
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Basic request / response
// ---------------------------------------------------------------------------

describe("request", () => {
  it("sends GET and returns parsed JSON", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ name: "test" }));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const result = await client.request<{ name: string }>({ path: "/ping" });
    expect(result).toEqual({ name: "test" });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/ping",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("sends POST with JSON body", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: "abc" }));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const result = await client.request<{ id: string }>({
      method: "POST",
      path: "/items",
      body: { name: "foo" },
    });
    expect(result).toEqual({ id: "abc" });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/items",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "foo" }),
      }),
    );
  });

  it("returns undefined for 204 No Content", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const result = await client.request<unknown>({ method: "PUT", path: "/items/1" });
    expect(result).toBeUndefined();
  });

  it("appends query parameters to the URL", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({ path: "/search", query: { q: "hello", page: 1, active: true } });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/search?q=hello&page=1&active=true",
      expect.any(Object),
    );
  });

  it("skips undefined query parameters", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({ path: "/search", query: { q: "a", filter: undefined } });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/search?q=a",
      expect.any(Object),
    );
  });

  it("normalises trailing slash on baseUrl", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080/" });

    await client.request({ path: "/tools" });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8080/tools",
      expect.any(Object),
    );
  });
});

// ---------------------------------------------------------------------------
// Headers
// ---------------------------------------------------------------------------

describe("headers", () => {
  it("includes Content-Type application/json by default", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({ path: "/test" });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }),
    );
  });

  it("injects X-Api-Key when apiKey is configured", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080", apiKey: "sk_test" });

    await client.request({ path: "/test" });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Api-Key": "sk_test" }),
      }),
    );
  });

  it("merges client-level custom headers", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({
      baseUrl: "http://localhost:8080",
      headers: { "X-Org-Id": "org_demo" },
    });

    await client.request({ path: "/test" });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Org-Id": "org_demo" }),
      }),
    );
  });

  it("merges per-request headers over client-level headers", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({
      baseUrl: "http://localhost:8080",
      headers: { "X-Org-Id": "org_demo" },
    });

    await client.request({ path: "/test", headers: { "X-Request-Id": "req_001" } });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Org-Id": "org_demo",
          "X-Request-Id": "req_001",
        }),
      }),
    );
  });

  it("auto-generates Idempotency-Key for POST requests", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({ method: "POST", path: "/items", body: { x: 1 } });
    const callArgs = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(callArgs.headers).toHaveProperty("Idempotency-Key");
    expect(typeof (callArgs.headers as Record<string, string>)["Idempotency-Key"]).toBe("string");
  });

  it("uses provided idempotencyKey instead of auto-generating", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({
      method: "PUT",
      path: "/items/1",
      body: { x: 1 },
      idempotencyKey: "my-key",
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": "my-key" }),
      }),
    );
  });

  it("does not override explicit Idempotency-Key header", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    await client.request({
      method: "POST",
      path: "/items",
      body: { x: 1 },
      headers: { "Idempotency-Key": "explicit" },
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": "explicit" }),
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

describe("error mapping", () => {
  it("maps 401 to AuthenticationError with reason invalid", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "bad key" }, 401));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client
      .request({ path: "/test" })
      .catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
    expect((err as AuthenticationError).reason).toBe("invalid");
    expect((err as AuthenticationError).message).toContain("bad key");
  });

  it("maps 403 to AuthenticationError with reason expired", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ error: "token expired" }, 403));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client.request({ path: "/test" }).catch((e) => e);
    expect(err).toBeInstanceOf(AuthenticationError);
    expect((err as AuthenticationError).reason).toBe("forbidden");
  });

  it("maps 404 to NotFoundError", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "not found" }, 404));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client.request({ path: "/tools/foo" }).catch((e) => e);
    expect(err).toBeInstanceOf(NotFoundError);
    expect((err as NotFoundError).resourceType).toBe("endpoint");
    expect((err as NotFoundError).resourceId).toContain("/tools/foo");
  });

  it("maps 409 to UndoLogError with code CONFLICT", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "version conflict" }, 409));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client.request({ path: "/effects/1/commit" }).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("CONFLICT");
  });

  it("maps other 4xx to CLIENT_ERROR", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "bad request" }, 400));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client.request({ path: "/test" }).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("CLIENT_ERROR");
  });

  it("maps 5xx to SERVER_ERROR", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ message: "internal error" }, 500));
    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 0 });

    const err = await client.request({ path: "/test" }).catch((e) => e);
    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("SERVER_ERROR");
  });

  it("falls back to status text when no message or error in body", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}, 400));
    client = createHttpClient({ baseUrl: "http://localhost:8080" });

    const err = await client.request({ path: "/test" }).catch((e) => e);
    expect((err as UndoLogError).message).toBe("HTTP 400");
  });

  it("does not retry non-retryable 4xx", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValue(jsonResponse({}, 400));
    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 3 });

    await expect(client.request({ path: "/test" })).rejects.toThrow(UndoLogError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Retry behaviour
// ---------------------------------------------------------------------------

describe("retry", () => {
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
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.request<{ ok: boolean }>({ path: "/test" });
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("retries on 503 and succeeds on second attempt", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ error: "unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.request<{ ok: boolean }>({ path: "/test" });
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("retries on network error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.request<{ ok: boolean }>({ path: "/test" });
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("exhausts retries and throws last mapped error on 429", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockImplementation(() =>
      Promise.resolve(jsonResponse({ error: "rate limited" }, 429)),
    );

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 2 });

    const promise = client.request({ path: "/test" }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(15000);
    const err = await promise;

    expect(err).toBeInstanceOf(UndoLogError);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("exhausts retries and throws on persistent network error", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockImplementation(() => Promise.reject(new TypeError("fetch failed")));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 2 });

    const promise = client.request({ path: "/test" }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(15000);
    const err = await promise;

    expect(err).toBeInstanceOf(UndoLogError);
    expect((err as UndoLogError).code).toBe("NETWORK_ERROR");
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("honours Retry-After header for 429", async () => {
    const mockFetch = vi.mocked(fetch);
    const response429 = new Response(JSON.stringify({ error: "rate limited" }), {
      status: 429,
      headers: { "Retry-After": "1", "Content-Type": "application/json" },
    });
    mockFetch
      .mockResolvedValueOnce(response429)
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 1 });

    const promise = client.request<{ ok: boolean }>({ path: "/test" });
    // Retry-After says 1 second; advance just past it
    await vi.advanceTimersByTimeAsync(1500);
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("supports per-request retries override", async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ error: "rate limited" }, 429))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    client = createHttpClient({ baseUrl: "http://localhost:8080", maxRetries: 0 });

    const promise = client.request<{ ok: boolean }>({ path: "/test", retries: 1 });
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Timeout
// ---------------------------------------------------------------------------

describe("timeout", () => {
  it("throws TimeoutError when request exceeds deadline", async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockImplementation(createAbortableFetch(true));

    client = createHttpClient({ baseUrl: "http://localhost:8080", timeout: 50 });

    const promise = client.request({ path: "/slow" }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(100);
    const err = await promise;

    expect(err).toBeInstanceOf(TimeoutError);
    expect((err as TimeoutError).operation).toContain("/slow");
    expect((err as TimeoutError).timeoutMs).toBe(50);
    vi.useRealTimers();
  });

  it("does not throw TimeoutError when response arrives in time", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }));
    client = createHttpClient({ baseUrl: "http://localhost:8080", timeout: 5000 });

    const result = await client.request<{ ok: boolean }>({ path: "/fast" });
    expect(result).toEqual({ ok: true });
  });
});
