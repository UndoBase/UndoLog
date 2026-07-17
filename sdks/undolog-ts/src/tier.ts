/** Tier classification for UndoLog tool calls.
 *
 * Every tool is classified at registration time into exactly one tier.
 * Classification is declarative (SDK annotation), never inferred by the LLM.
 */

/** How the Effect Engine treats an intercepted tool call. */
export enum ToolTier {
  /** Read-only or idempotent. Execute freely; no effect log entry required.
   *
   * Examples: search_web, read_file, get_user
   */
  Safe = "safe",

  /** Write operation with a well-defined compensation (undo).
   *
   * Examples: send_email, transfer_funds, create_record
   */
  Compensable = "compensable",

  /** Cannot be undone. Requires explicit human approval before execution.
   *
   * Examples: delete_database, publish_to_production, wire_large_amount
   */
  Irreversible = "irreversible",
}

/** Describes the compensation function to invoke when rolling back a
 * Compensable tool call.
 *
 * Stored in the undo stack entry before the action executes, so that
 * a process crash cannot lose the compensation information.
 */
export interface CompensationDescriptor {
  /** Logical name matching the compensation registry. */
  fnName: string;

  /** Semver version of the compensation function. Defaults to "1.0.0". */
  fnVersion?: string;

  /** Arguments captured from the original call before execution. */
  args?: Record<string, unknown>;

  /** Max retry attempts before escalating to compensation_failed. Defaults to 3. */
  maxRetries?: number;

  /** Backoff delay between retries in milliseconds. Defaults to 1000. */
  retryBackoffMs?: number;
}

/** Returns true when the tool requires human approval before execution.
 *
 * @param tier - The tier to evaluate.
 * @returns True for Irreversible, false otherwise.
 */
export function requiresApproval(tier: ToolTier): boolean {
  return tier === ToolTier.Irreversible;
}

/** Returns true when the tool registers a compensation function before execution.
 *
 * @param tier - The tier to evaluate.
 * @returns True for Compensable, false otherwise.
 */
export function isCompensable(tier: ToolTier): boolean {
  return tier === ToolTier.Compensable;
}

/** Returns true when the tool is read-only or idempotent (no effect log entry).
 *
 * @param tier - The tier to evaluate.
 * @returns True for Safe, false otherwise.
 */
export function isSafe(tier: ToolTier): boolean {
  return tier === ToolTier.Safe;
}
