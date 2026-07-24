/** UndoLog session management with AsyncLocalStorage context.
 *
 * Provides the UndoLogSession class for representing an effect-tracking
 * session and the runWithSession() function for establishing that session as
 * the active context within an async call chain.
 *
 * @module
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";

/** Options for creating a new UndoLogSession. */
export interface SessionOptions {
  /** Explicit session UUID. A version-4 UUID is generated if omitted. */
  sessionId?: string;
  /** Arbitrary key-value metadata attached to the session. */
  metadata?: Record<string, unknown>;
  /** Initial step index. Useful when restoring session state from a previous
   *  snapshot where a specific step count must be preserved. */
  stepIndex?: number;
}

/** Internal store held in AsyncLocalStorage. */
interface SessionStore {
  session: UndoLogSession;
}

const als = new AsyncLocalStorage<SessionStore>();

/** A single UndoLog session with identity, step counter, and metadata.
 *
 * Sessions are the top-level grouping primitive. Every tool call logged by
 * the effect engine is attributed to exactly one session. Create one via
 * ``new UndoLogSession(options)`` and activate it with ``runWithSession()``.
 */
export class UndoLogSession {
  /** UUID identifying this session. */
  readonly sessionId: string;
  /** Epoch timestamp (ms) when the session was constructed. */
  readonly startTime: number;
  /** Arbitrary key-value metadata attached at creation time. */
  readonly metadata: Record<string, unknown>;
  /** Monotonically increasing step counter, 0-based. */
  #stepIndex: number;

  /**
   * @param options - Session configuration (sessionId, metadata).
   *
   * @example
   * ```ts
   * const session = new UndoLogSession({
   *   sessionId: "550e8400-e29b-41d4-a716-446655440000",
   *   metadata: { userId: "abc-123" },
   * });
   * ```
   */
  constructor(options: SessionOptions = {}) {
    this.sessionId = options.sessionId ?? randomUUID();
    this.startTime = Date.now();
    this.metadata = { ...options.metadata };
    this.#stepIndex = options.stepIndex ?? 0;
  }

  /** Current step index (0-based). */
  get stepIndex(): number {
    return this.#stepIndex;
  }

  /** Advance the step counter by one and return the new value.
   *
   * @returns The incremented step index.
   *
   * @throws {RangeError} When the step counter has reached or exceeded
   *   ``Number.MAX_SAFE_INTEGER`` and cannot be safely incremented further.
   *
   * @example
   * ```ts
   * session.nextStep(); // returns 1
   * session.nextStep(); // returns 2
   * ```
   */
  nextStep(): number {
    if (this.#stepIndex >= Number.MAX_SAFE_INTEGER) {
      throw new RangeError(
        "Step counter overflow: cannot exceed Number.MAX_SAFE_INTEGER",
      );
    }
    this.#stepIndex += 1;
    return this.#stepIndex;
  }
}

/** Retrieve the currently active session from the async context.
 *
 * Returns ``undefined`` when called outside of any ``runWithSession()`` scope.
 *
 * @returns The active session, or undefined if no session context is active.
 */
export function getCurrentSession(): UndoLogSession | undefined {
  return als.getStore()?.session;
}

/** Execute a function within an UndoLogSession async context.
 *
 * Inside ``fn``, ``getCurrentSession()`` returns the provided session. Nested
 * calls create a new inner scope; the outer session is restored when the inner
 * scope completes.
 *
 * @typeParam T - Return type of the callback.
 * @param sessionOrOptions - An existing UndoLogSession instance or options to
 *   create a new one.
 * @param fn - The function to execute within the session context.
 * @returns The return value of ``fn``.
 *
 * @example
 * ```ts
 * const session = new UndoLogSession({ metadata: { userId: "42" } });
 * const result = await runWithSession(session, async () => {
 *   // getCurrentSession() returns `session` inside this scope
 *   return doSomething();
 * });
 * ```
 */
export function runWithSession<T>(
  sessionOrOptions: UndoLogSession | SessionOptions,
  fn: () => T,
): T {
  const session =
    sessionOrOptions instanceof UndoLogSession
      ? sessionOrOptions
      : new UndoLogSession(sessionOrOptions);
  const store: SessionStore = { session };
  return als.run(store, fn);
}
