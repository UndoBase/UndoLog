/** Administrative client for UndoLog dead-letter queue operations.
 *
 * Provides the UndoLogAdminClient class with listDeadLetters,
 * retryDeadLetter, and skipDeadLetter methods.
 *
 * @module
 */

import { createHttpClient } from "./http.js";
import type { HttpClient } from "./http.js";
import { ValidationError } from "./errors.js";

/** Lifecycle state of a dead-letter compensation record. */
export type DeadLetterState = "failed" | "retrying" | "skipped";

/** Record returned by the UndoLog proxy for an administrative dead-letter query. */
export interface DeadLetterRecord {
  /** Unique UUID of the dead-letter record. */
  deadLetterId: string;
  /** Organization UUID owning the dead-letter record. */
  orgId: string;
  /** Session UUID that generated the failed tool call. */
  sessionId: string;
  /** Effect log identifier associated with the failure. */
  effectId: string;
  /** Unique undo log identifier for the compensation. */
  undoId: string;
  /** Logical name of the registered compensation function. */
  compensationFn: string;
  /** Version identifier of the compensation function. */
  compensationVersion: string;
  /** Arguments payload registered for compensation execution. */
  compensationArgs: Record<string, unknown>;
  /** Error message describing why compensation failed. */
  errorMessage: string;
  /** Number of times this dead letter has been retried. */
  retryCount: number;
  /** Current lifecycle state of the record. */
  state: DeadLetterState;
  /** RFC 3339 creation timestamp. */
  createdAt: string;
  /** RFC 3339 last update timestamp. */
  updatedAt: string;
}

/** Options for filtering dead-letter records. */
export interface ListDeadLettersFilter {
  /** Optional lifecycle state filter. Defaults to "failed" on the proxy. */
  state?: DeadLetterState;
}

/** Result returned by ``UndoLogAdminClient.listDeadLetters()``. */
export interface ListDeadLettersResponse {
  /** Dead-letter records matching the query. */
  records: DeadLetterRecord[];
  /** Total count of records returned. */
  count: number;
}

/** Options for constructing an UndoLogAdminClient. */
export interface UndoLogAdminClientOptions {
  /** Base URL of the UndoLog proxy server. */
  baseUrl: string;
  /** Administrative API key sent as an ``X-Api-Key`` header. */
  apiKey?: string;
  /** Per-request timeout in milliseconds. Defaults to 30000. */
  timeout?: number;
  /** Maximum retry attempts for retryable statuses. Defaults to 3. */
  maxRetries?: number;
  /** Additional headers included on every request. */
  headers?: Record<string, string>;
  /** Pre-configured HttpClient instance. Overrides other HTTP options. */
  httpClient?: HttpClient;
}

interface WireDeadLetterRecord {
  dead_letter_id?: string;
  deadLetterId?: string;
  org_id?: string;
  orgId?: string;
  session_id?: string;
  sessionId?: string;
  effect_id?: string;
  effectId?: string;
  undo_id?: string;
  undoId?: string;
  compensation_fn?: string;
  compensationFn?: string;
  compensation_version?: string;
  compensationVersion?: string;
  compensation_args?: Record<string, unknown>;
  compensationArgs?: Record<string, unknown>;
  error_message?: string;
  errorMessage?: string;
  retry_count?: number;
  retryCount?: number;
  state?: DeadLetterState;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
}

function fromWireRecord(raw: WireDeadLetterRecord): DeadLetterRecord {
  return {
    deadLetterId: raw.dead_letter_id ?? raw.deadLetterId ?? "",
    orgId: raw.org_id ?? raw.orgId ?? "",
    sessionId: raw.session_id ?? raw.sessionId ?? "",
    effectId: raw.effect_id ?? raw.effectId ?? "",
    undoId: raw.undo_id ?? raw.undoId ?? "",
    compensationFn: raw.compensation_fn ?? raw.compensationFn ?? "",
    compensationVersion: raw.compensation_version ?? raw.compensationVersion ?? "",
    compensationArgs: raw.compensation_args ?? raw.compensationArgs ?? {},
    errorMessage: raw.error_message ?? raw.errorMessage ?? "",
    retryCount: raw.retry_count ?? raw.retryCount ?? 0,
    state: raw.state ?? "failed",
    createdAt: raw.created_at ?? raw.createdAt ?? "",
    updatedAt: raw.updated_at ?? raw.updatedAt ?? "",
  };
}

/** Administrative client for managing UndoLog dead-letter queues.
 *
 * Create an instance with ``new UndoLogAdminClient(options)`` using an
 * administrative API key to inspect, retry, and skip failed compensations.
 */
export class UndoLogAdminClient {
  readonly #http: HttpClient;

  constructor(options: UndoLogAdminClientOptions) {
    if (!options || typeof options.baseUrl !== "string" || !options.baseUrl) {
      throw new ValidationError(
        "baseUrl is required and must be a non-empty string",
        "baseUrl",
        options?.baseUrl,
      );
    }
    this.#http =
      options.httpClient ??
      createHttpClient({
        baseUrl: options.baseUrl,
        apiKey: options.apiKey,
        timeout: options.timeout,
        maxRetries: options.maxRetries,
        headers: options.headers,
      });
  }

  /** List dead-letter compensation records for the authenticated organization.
   *
   * @param filter - Optional filter by lifecycle state (failed, retrying, skipped).
   * @returns Deserialized records and total count.
   */
  async listDeadLetters(filter?: ListDeadLettersFilter): Promise<ListDeadLettersResponse> {
    const query: Record<string, string | undefined> = {};
    if (filter?.state) {
      query.state = filter.state;
    }
    const resp = await this.#http.request<{
      records?: WireDeadLetterRecord[];
      count?: number;
    }>({
      method: "GET",
      path: "/api/v1/admin/dead-letters",
      query,
    });

    const rawRecords = resp?.records ?? [];
    return {
      records: rawRecords.map(fromWireRecord),
      count: resp?.count ?? rawRecords.length,
    };
  }

  /** Request the engine to retry a failed dead-letter compensation.
   *
   * @param deadLetterId - UUID of the dead-letter record to retry.
   * @returns Updated dead-letter record in retrying state.
   */
  async retryDeadLetter(deadLetterId: string): Promise<DeadLetterRecord> {
    if (!deadLetterId || typeof deadLetterId !== "string") {
      throw new ValidationError(
        "deadLetterId must be a non-empty string",
        "deadLetterId",
        deadLetterId,
      );
    }
    const resp = await this.#http.request<WireDeadLetterRecord>({
      method: "POST",
      path: `/api/v1/admin/dead-letters/${encodeURIComponent(deadLetterId)}/retry`,
      body: {},
    });
    return fromWireRecord(resp);
  }

  /** Request the engine to mark a failed dead-letter compensation as skipped.
   *
   * @param deadLetterId - UUID of the dead-letter record to skip.
   * @returns Updated dead-letter record in skipped state.
   */
  async skipDeadLetter(deadLetterId: string): Promise<DeadLetterRecord> {
    if (!deadLetterId || typeof deadLetterId !== "string") {
      throw new ValidationError(
        "deadLetterId must be a non-empty string",
        "deadLetterId",
        deadLetterId,
      );
    }
    const resp = await this.#http.request<WireDeadLetterRecord>({
      method: "POST",
      path: `/api/v1/admin/dead-letters/${encodeURIComponent(deadLetterId)}/skip`,
      body: {},
    });
    return fromWireRecord(resp);
  }
}
