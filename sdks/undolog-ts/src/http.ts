/** Low-level fetch wrapper with retry, timeout, auth, and error mapping.
 *
 * Provides a factory function ``createHttpClient`` that returns an
 * ``HttpClient`` implementation wrapping the global ``fetch`` API. Every
 * request is subject to timeout enforcement, configurable retry for
 * retryable HTTP statuses (429, 5xx), and automatic injection of an
 * ``X-Api-Key`` header when an API key is configured. Non-OK responses
 * are mapped to typed ``UndoLogError`` subclasses.
 *
 * @module
 */

import { randomUUID } from "node:crypto";
import { AuthenticationError, NotFoundError, TimeoutError, UndoLogError } from "./errors.js";

/** Options passed to ``createHttpClient``. */
export interface HttpClientOptions {
  /** Base URL prepended to every request path (e.g. "http://localhost:8080"). */
  baseUrl: string;
  /** API key sent as an ``X-Api-Key`` header on every request. */
  apiKey?: string;
  /** Per-request timeout in milliseconds. Defaults to 30000. */
  timeout?: number;
  /** Maximum number of automatic retries for retryable statuses. Defaults to 3. */
  maxRetries?: number;
  /** Additional headers that are merged into every request. */
  headers?: Record<string, string>;
}

/** Per-request options passed to ``HttpClient.request``. */
export interface RequestOptions {
  /** HTTP method. Defaults to ``"GET"``. */
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** URL path relative to the client's ``baseUrl``. */
  path: string;
  /** Query parameters appended to the URL. Values set to ``undefined`` are skipped. */
  query?: Record<string, string | number | boolean | undefined>;
  /** Request body. Serialised as JSON when present. */
  body?: unknown;
  /** Per-request headers merged over the client-level defaults. */
  headers?: Record<string, string>;
  /** Per-request retry override. Falls back to client ``maxRetries`` when omitted. */
  retries?: number;
  /** Idempotency key for mutating requests. Auto-generated when omitted. */
  idempotencyKey?: string;
}

/** Low-level HTTP client interface returned by ``createHttpClient``. */
export interface HttpClient {
  /** Execute an HTTP request and deserialise the JSON response body.
   *
   * @typeParam T - Expected shape of the JSON response body.
   * @param options - Request configuration (path, method, body, headers).
   * @returns Deserialised JSON response (``undefined`` for 204 No Content).
   */
  request<T>(options: RequestOptions): Promise<T>;
}

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);

function isRetryable(status: number): boolean {
  return RETRYABLE_STATUSES.has(status) || (status >= 500 && status < 600);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function calculateBackoff(attempt: number, response?: Response): number {
  const maxDelay = 30_000;
  if (response) {
    const retryAfter = response.headers.get("Retry-After");
    if (retryAfter) {
      const seconds = Number.parseInt(retryAfter, 10);
      if (!Number.isNaN(seconds)) {
        return Math.min(seconds * 1000, maxDelay);
      }
    }
  }
  const baseDelay = 1000;
  const delay = Math.min(baseDelay * 2 ** attempt, maxDelay);
  const jitter = delay * 0.25 * (Math.random() * 2 - 1);
  return Math.floor(delay + jitter);
}

function mapHttpError(
  status: number,
  body: unknown,
  url: string,
): UndoLogError {
  const bodyRecord = body as Record<string, unknown> | undefined;
  const message =
    (bodyRecord?.message as string) ?? (bodyRecord?.error as string) ?? `HTTP ${status}`;
  switch (status) {
    case 401:
      return new AuthenticationError("invalid", message);
    case 403:
      return new AuthenticationError("forbidden", message);
    case 404:
      return new NotFoundError("endpoint", url, message);
    case 409:
      return new UndoLogError("CONFLICT", message);
    default:
      if (status >= 400 && status < 500) {
        return new UndoLogError("CLIENT_ERROR", message);
      }
      return new UndoLogError("SERVER_ERROR", message);
  }
}

function buildUrl(
  baseUrl: string,
  path: string,
  query: Record<string, string | number | boolean | undefined> | undefined,
): string {
  let normalizedBase = baseUrl;
  while (normalizedBase.endsWith("/")) {
    normalizedBase = normalizedBase.slice(0, -1);
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  let url = `${normalizedBase}${normalizedPath}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
        params.append(key, String(value));
      }
    }
    const qs = params.toString();
    if (qs) {
      url += `?${qs}`;
    }
  }
  return url;
}

/** Strip credentials from a URL string for safe inclusion in error messages. */
function sanitizeUrl(raw: string): string {
  try {
    const parsed = new URL(raw);
    if (parsed.username || parsed.password) {
      parsed.username = "";
      parsed.password = "";
      return parsed.toString();
    }
    return raw;
  } catch {
    return raw;
  }
}

/** Create a configured ``HttpClient``.
 *
 * @param options - Client configuration (base URL, auth, timeouts, retries).
 * @returns An ``HttpClient`` instance ready to issue requests.
 *
 * @example
 * ```ts
 * const httpClient = createHttpClient({
 *   baseUrl: "http://localhost:8080",
 *   apiKey: "sk-...",
 * });
 * const data = await httpClient.request({ path: "/v1/effects/list" });
 * ```
 */
export function createHttpClient(options: HttpClientOptions): HttpClient {
  const timeout = options.timeout ?? 30_000;
  const maxRetries = options.maxRetries ?? 3;

  const authHeaders: Record<string, string> = {};
  if (options.apiKey) {
    authHeaders["X-Api-Key"] = options.apiKey;
  }

  const request: HttpClient["request"] = async <T>(
    requestOptions: RequestOptions,
  ): Promise<T> => {
    const retries = requestOptions.retries ?? maxRetries;
    const method = (requestOptions.method ?? "GET").toUpperCase();
    const url = buildUrl(options.baseUrl, requestOptions.path, requestOptions.query);

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...options.headers,
      ...requestOptions.headers,
      ...authHeaders,
    };

    if (
      (method === "POST" || method === "PUT") &&
      !headers["Idempotency-Key"] &&
      !headers["idempotency-key"]
    ) {
      headers["Idempotency-Key"] = requestOptions.idempotencyKey ?? randomUUID();
    }

    const hasBody = (method === "POST" || method === "PUT") && requestOptions.body !== undefined;
    let lastError: UndoLogError | undefined;

    for (let attempt = 0; attempt <= retries; attempt++) {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeout);

      try {
        const response = await fetch(url, {
          method,
          headers,
          body: hasBody ? JSON.stringify(requestOptions.body) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (response.status === 204) {
          return undefined as T;
        }

        const text = await response.text();
        const parsedBody = text ? safeJsonParse(text) : undefined;

        if (response.ok) {
          return parsedBody as T;
        }

        const error = mapHttpError(response.status, parsedBody, sanitizeUrl(url));

        if (isRetryable(response.status) && attempt < retries) {
          const delay = calculateBackoff(attempt, response);
          await sleep(delay);
          lastError = error;
          continue;
        }

        throw error;
      } catch (err) {
        clearTimeout(timeoutId);

        if (err instanceof UndoLogError) {
          throw err;
        }

        if (err instanceof DOMException && err.name === "AbortError") {
          throw new TimeoutError(`${method} ${sanitizeUrl(url)}`, timeout);
        }

        // Fallback for runtimes where AbortError is not a DOMException
        // (e.g. Deno, Cloudflare Workers).
        if (err instanceof Error && err.name === "AbortError") {
          throw new TimeoutError(`${method} ${sanitizeUrl(url)}`, timeout);
        }

        const wrappedError =
          err instanceof Error
            ? new UndoLogError("NETWORK_ERROR", err.message)
            : new UndoLogError("NETWORK_ERROR", String(err));

        // Never retry mutating requests on network errors. The server may have
        // processed the request already and lost the response. A retry would
        // re-execute the side effect, defeating exactly-once guarantees.
        if (method === "POST" || method === "PUT") {
          throw wrappedError;
        }

        if (attempt < retries) {
          const delay = calculateBackoff(attempt);
          await sleep(delay);
          lastError = wrappedError;
          continue;
        }

        throw wrappedError;
      }
    }

    throw lastError ?? new UndoLogError("UNKNOWN_ERROR", "Request failed after retries");
  };

  return { request };
}
