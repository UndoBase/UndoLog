/** Multi-step saga compensation test harness for isolated unit testing.
 *
 * Provides ``TestSaga``, ``createTestSaga``, ``testSaga``, and
 * ``CompensationTestHarness`` for simulating distributed saga compensation
 * workflows, LIFO rollbacks, retry budgets, and human approval gates without
 * a running engine or database.
 *
 * Import from ``@undolog/sdk/testing``.
 *
 * @module
 */

import { randomUUID } from "node:crypto";
import { AwaitingApprovalError, ValidationError } from "../errors.js";
import { ToolTier } from "../tier.js";

/** Default retry budget for compensations matching the engine contract. */
export const DEFAULT_MAX_RETRIES = 3;

/** One recorded or planned step in a saga test workflow.
 *
 * Configures the tool name, classification tier, compensation handler,
 * arguments, and retry budget.
 */
export interface SagaStepDescriptor<TArgs = Record<string, unknown>> {
  /** Logical name of the tool that produced the effect. */
  toolName: string;
  /** Classification tier of the tool. Defaults to ``ToolTier.Compensable``. */
  tier?: ToolTier;
  /** Compensation function invoked during rollback. */
  compensation?: (args: TArgs) => Promise<unknown> | unknown;
  /** Stack position for LIFO rollback ordering: higher positions roll back first. */
  stackPosition?: number;
  /** Maximum retry attempts before escalating. Defaults to 3. */
  maxRetries?: number;
  /** Forward arguments associated with the step. */
  args?: TArgs;
  /** Specific inverse arguments delivered to the compensation function. Defaults to ``args``. */
  compensationArgs?: TArgs;
  /** Optional forward action function invoked during simulation. */
  action?: (args: TArgs) => Promise<unknown> | unknown;
}

/** Backward-compatible alias matching the Python SDK ``EffectDescriptor``. */
export type EffectDescriptor<TArgs = Record<string, unknown>> = SagaStepDescriptor<TArgs>;

/** Outcome of one entry in a simulated rollback. */
export interface SagaStepResult {
  /** Logical name of the compensated tool. */
  toolName: string;
  /** LIFO position the entry rolled back at. */
  stackPosition: number;
  /** Number of times the compensation was invoked. */
  attempts: number;
  /** Outcome state of the step rollback: ``"compensated"`` or ``"failed"``. */
  state: "compensated" | "failed";
  /** Last error message when ``state === "failed"``. */
  error?: string;
  /** Inverse arguments delivered to the compensation function. */
  args?: Record<string, unknown>;
}

/** Backward-compatible alias matching the Python SDK ``StepResult``. */
export type StepResult = SagaStepResult;

/** Executed compensation call record for detailed assertion inspection. */
export interface ExecutedCompensation {
  /** Logical name of the tool. */
  toolName: string;
  /** Stack position in the rollback order. */
  stackPosition: number;
  /** Arguments passed to the compensation handler. */
  args: Record<string, unknown>;
  /** Total attempts consumed during execution. */
  attempts: number;
}

/** Report of a full simulated saga rollback. */
export interface SagaReport {
  /** Session outcome: ``"compensated"`` when all rollback steps succeed, or ``"halted"`` on permanent failure. */
  sessionResult: "compensated" | "halted";
  /** Per-entry outcomes in rollback (LIFO) execution order. */
  steps: SagaStepResult[];
  /** Tool names in the order their compensations were executed. */
  compensationOrder: string[];
  /** Detailed record of executed compensations with tool name, stack position, arguments, and attempts. */
  executedCompensations: ExecutedCompensation[];
  /** Simulated failure error message if provided to simulateFailureAt. */
  failureError?: string;
}

/** Report of a single simulated compensation run. */
export interface CompensationReport {
  /** Name of the compensation function under test. */
  fnName: string;
  /** Number of invocations that returned normally. */
  executionCount: number;
  /** Attempts consumed beyond the initial call. */
  retryCount: number;
  /** Final state: ``"compensated"`` on success, ``"failed"`` when retries are exhausted. */
  finalState: "compensated" | "failed";
  /** Return value of the successful attempt, if any. */
  result?: unknown;
  /** Last error message when ``finalState === "failed"``. */
  error?: string;
}

/** Configuration options for constructing a ``TestSaga``. */
export interface TestSagaOptions {
  /** Default retry budget for compensations when not specified per step. Defaults to 3. */
  defaultMaxRetries?: number;
  /** Tool names or step positions pre-approved for execution without throwing ``AwaitingApprovalError``. */
  preApproved?: Array<string | number>;
  /** Initial steps to populate the saga. */
  steps?: SagaStepDescriptor[];
}

/** Multi-step saga compensation harness for isolated unit testing.
 *
 * Replays rollback sequences matching the UndoLog engine saga orchestrator:
 * entries execute in LIFO order by ``stackPosition``, each gets a per-entry
 * retry budget, and the pass halts at the first entry that fails permanently.
 *
 * Irreversible steps throw ``AwaitingApprovalError`` unless pre-approved.
 */
export class TestSaga {
  private readonly _steps: SagaStepDescriptor[] = [];
  private readonly _preApproved: Set<string | number> = new Set();
  private readonly _defaultMaxRetries: number;
  private _approveAll = false;
  private _lastReport?: SagaReport;

  constructor(optionsOrSteps?: TestSagaOptions | SagaStepDescriptor[]) {
    if (Array.isArray(optionsOrSteps)) {
      this._defaultMaxRetries = DEFAULT_MAX_RETRIES;
      for (const step of optionsOrSteps) {
        this.addStep(step);
      }
    } else {
      this._defaultMaxRetries = optionsOrSteps?.defaultMaxRetries ?? DEFAULT_MAX_RETRIES;
      if (optionsOrSteps?.preApproved) {
        for (const item of optionsOrSteps.preApproved) {
          this._preApproved.add(item);
        }
      }
      if (optionsOrSteps?.steps) {
        for (const step of optionsOrSteps.steps) {
          this.addStep(step);
        }
      }
    }
  }

  /** Add a step to the test saga.
   *
   * @param descriptor - Step configuration including tool name, tier, args, and compensation.
   * @returns This saga instance for chaining.
   */
  addStep(descriptor: SagaStepDescriptor): this {
    if (!descriptor.toolName || typeof descriptor.toolName !== "string") {
      throw new ValidationError("descriptor.toolName must be a non-empty string");
    }
    if (descriptor.stackPosition !== undefined && descriptor.stackPosition < 0) {
      throw new ValidationError(
        `stackPosition must be non-negative, got ${descriptor.stackPosition}`,
      );
    }
    this._steps.push(descriptor);
    return this;
  }

  /** Pre-approve a tool name or step position to bypass approval gates.
   *
   * @param toolNameOrPosition - Logical tool name or numeric stack position/index.
   * @returns This saga instance for chaining.
   */
  preApprove(toolNameOrPosition: string | number): this {
    this._preApproved.add(toolNameOrPosition);
    return this;
  }

  /** Pre-approve all steps in the saga, bypassing all approval gates.
   *
   * @returns This saga instance for chaining.
   */
  preApproveAll(): this {
    this._approveAll = true;
    return this;
  }

  /** Clear all pre-approvals.
   *
   * @returns This saga instance for chaining.
   */
  clearApprovals(): this {
    this._approveAll = false;
    this._preApproved.clear();
    return this;
  }

  /** Return recorded steps in order of registration. */
  get steps(): readonly SagaStepDescriptor[] {
    return this._steps;
  }

  /** Return the report of the most recent rollback or simulation run. */
  get report(): SagaReport | undefined {
    return this._lastReport;
  }

  /** Return the report of the most recent rollback, throwing if none exists.
   *
   * @returns The ``SagaReport`` from the last run.
   */
  getReport(): SagaReport {
    if (this._lastReport === undefined) {
      throw new ValidationError("No saga run has been performed; call simulateFailureAt() or rollback() first");
    }
    return this._lastReport;
  }

  /** Return executed compensations from the most recent run. */
  get executedCompensations(): ExecutedCompensation[] {
    return this._lastReport ? this._lastReport.executedCompensations : [];
  }

  /** Check whether a given step has received approval. */
  isApproved(step: SagaStepDescriptor, index: number): boolean {
    if (this._approveAll) {
      return true;
    }
    if (this._preApproved.has(step.toolName)) {
      return true;
    }
    if (this._preApproved.has(index)) {
      return true;
    }
    if (step.stackPosition !== undefined && this._preApproved.has(step.stackPosition)) {
      return true;
    }
    return false;
  }

  /** Simulate failure at a specific step index, rolling back prior completed steps in LIFO order.
   *
   * Steps prior to ``stepIndex`` execute forward. If an irreversible step is
   * encountered without pre-approval, ``AwaitingApprovalError`` is thrown.
   * When ``stepIndex`` fails, all preceding completed steps are rolled back
   * in reverse chronological (LIFO) order with their inverse arguments.
   *
   * @param stepIndex - Zero-based index of the step that triggers failure.
   * @param failureError - Optional error representing the step failure.
   * @returns A ``SagaReport`` documenting the rollback sequence and outcomes.
   */
  async simulateFailureAt(
    stepIndex: number,
    failureError?: Error | string,
  ): Promise<SagaReport> {
    if (this._steps.length === 0) {
      throw new ValidationError("Test saga must have at least one step registered");
    }
    if (stepIndex < 0 || stepIndex >= this._steps.length) {
      throw new ValidationError(
        `Invalid stepIndex ${stepIndex}: saga has ${this._steps.length} step(s)`,
      );
    }

    // Forward execution check from 0 up to stepIndex
    for (let i = 0; i <= stepIndex; i++) {
      const step = this._steps[i];
      if (!step) {
        continue;
      }
      if (step.tier === ToolTier.Irreversible && !this.isApproved(step, i)) {
        const approvalId = `appr_${randomUUID()}`;
        const args = (step.args ?? {}) as Record<string, unknown>;
        throw new AwaitingApprovalError(
          step.toolName,
          args,
          approvalId,
          `Tool "${step.toolName}" requires human approval (id: ${approvalId})`,
        );
      }
      if (i < stepIndex && step.action) {
        await step.action((step.args ?? {}) as Record<string, unknown>);
      }
    }

    // Roll back preceding steps in LIFO order
    const completedPrecedingSteps = this._steps.slice(0, stepIndex);
    const report = await this.rollback(completedPrecedingSteps);
    if (failureError !== undefined) {
      report.failureError =
        failureError instanceof Error ? failureError.message : String(failureError);
    }
    return report;
  }

  /** Execute all steps sequentially, rolling back completed steps if any step fails.
   *
   * @returns A ``SagaReport`` documenting the rollback if a failure occurred.
   */
  async execute(): Promise<SagaReport> {
    if (this._steps.length === 0) {
      throw new ValidationError("Test saga must have at least one step registered");
    }

    for (let i = 0; i < this._steps.length; i++) {
      const step = this._steps[i];
      if (!step) {
        continue;
      }
      if (step.tier === ToolTier.Irreversible && !this.isApproved(step, i)) {
        const approvalId = `appr_${randomUUID()}`;
        const args = (step.args ?? {}) as Record<string, unknown>;
        throw new AwaitingApprovalError(
          step.toolName,
          args,
          approvalId,
          `Tool "${step.toolName}" requires human approval (id: ${approvalId})`,
        );
      }
      if (step.action) {
        try {
          await step.action((step.args ?? {}) as Record<string, unknown>);
        } catch (err) {
          const completed = this._steps.slice(0, i);
          return this.rollback(completed);
        }
      }
    }

    const report: SagaReport = {
      sessionResult: "compensated",
      steps: [],
      compensationOrder: [],
      executedCompensations: [],
    };
    this._lastReport = report;
    return report;
  }

  /** Roll back the given steps (or all registered steps) in LIFO order.
   *
   * Entries execute in reverse chronological order based on ``stackPosition``
   * (or reverse registration order). Each entry attempts compensation up to
   * its retry budget. If an entry fails permanently, rollback halts.
   *
   * @param stepsToRollback - Subset of steps to roll back, or undefined for all steps.
   * @returns A ``SagaReport`` detailing each step outcome and execution order.
   */
  async rollback(stepsToRollback?: SagaStepDescriptor[]): Promise<SagaReport> {
    const targetSteps = stepsToRollback ?? [...this._steps];
    if (targetSteps.length === 0) {
      const emptyReport: SagaReport = {
        sessionResult: "compensated",
        steps: [],
        compensationOrder: [],
        executedCompensations: [],
      };
      this._lastReport = emptyReport;
      return emptyReport;
    }

    // Assign and validate stack positions
    const resolved: Array<{ step: SagaStepDescriptor; effectivePosition: number }> = [];
    for (let idx = 0; idx < targetSteps.length; idx++) {
      const s = targetSteps[idx];
      if (!s) {
        continue;
      }
      const pos = s.stackPosition !== undefined ? s.stackPosition : idx;
      if (pos < 0) {
        throw new ValidationError(`stackPosition must be non-negative, got ${pos}`);
      }
      resolved.push({ step: s, effectivePosition: pos });
    }

    // Check for duplicate stack positions
    const positions = resolved.map((item) => item.effectivePosition);
    const seen = new Set<number>();
    const duplicates = new Set<number>();
    for (const p of positions) {
      if (seen.has(p)) {
        duplicates.add(p);
      }
      seen.add(p);
    }
    if (duplicates.size > 0) {
      const sortedDupes = Array.from(duplicates).sort((a, b) => a - b);
      throw new ValidationError(`duplicate stackPosition values: ${sortedDupes.join(", ")}`);
    }

    // Sort LIFO: higher stackPosition rolls back first
    resolved.sort((a, b) => b.effectivePosition - a.effectivePosition);

    const stepResults: SagaStepResult[] = [];
    const compensationOrder: string[] = [];
    const executedCompensations: ExecutedCompensation[] = [];
    let halted = false;

    for (const item of resolved) {
      const { step, effectivePosition } = item;
      // Skip steps without a compensation function or Safe steps
      if (!step.compensation || step.tier === ToolTier.Safe) {
        continue;
      }

      const budget =
        step.maxRetries && step.maxRetries > 0 ? step.maxRetries : this._defaultMaxRetries;
      const inverseArgs = (
        step.compensationArgs !== undefined
          ? step.compensationArgs
          : step.args !== undefined
            ? step.args
            : {}
      ) as Record<string, unknown>;

      let attempts = 0;
      let lastError: string | undefined;
      let compensated = false;

      while (attempts < budget) {
        attempts++;
        try {
          await step.compensation(inverseArgs);
          compensationOrder.push(step.toolName);
          executedCompensations.push({
            toolName: step.toolName,
            stackPosition: effectivePosition,
            args: inverseArgs,
            attempts,
          });
          compensated = true;
          break;
        } catch (err) {
          lastError = err instanceof Error ? err.message : String(err);
        }
      }

      if (compensated) {
        stepResults.push({
          toolName: step.toolName,
          stackPosition: effectivePosition,
          attempts,
          state: "compensated",
          args: inverseArgs,
        });
      } else {
        stepResults.push({
          toolName: step.toolName,
          stackPosition: effectivePosition,
          attempts,
          state: "failed",
          error: lastError,
          args: inverseArgs,
        });
        halted = true;
        break;
      }
    }

    const report: SagaReport = {
      sessionResult: halted ? "halted" : "compensated",
      steps: stepResults,
      compensationOrder,
      executedCompensations,
    };
    this._lastReport = report;
    return report;
  }

  /** Reset all recorded reports and approvals. */
  reset(): void {
    this._lastReport = undefined;
    this._approveAll = false;
    this._preApproved.clear();
  }
}

/** Backward-compatible alias matching the Python SDK ``SagaTestHarness``. */
export { TestSaga as SagaTestHarness };

/** Test harness for individual compensation functions without a running engine.
 *
 * Wraps an async compensation function and records invocations, return
 * values, errors, retry behavior, and idempotency guarantees.
 */
export class CompensationTestHarness<
  TArgs = Record<string, unknown>,
  TResult = unknown,
> {
  readonly fn: (args: TArgs) => Promise<TResult> | TResult;
  readonly maxRetries: number;
  callCount = 0;
  readonly calls: TArgs[] = [];
  readonly results: TResult[] = [];
  readonly errors: Array<{ callIndex: number; error: unknown }> = [];

  constructor(fn: (args: TArgs) => Promise<TResult> | TResult, maxRetries = 3) {
    this.fn = fn;
    this.maxRetries = maxRetries;
  }

  /** Execute the compensation function and record call metrics.
   *
   * @param args - Arguments forwarded to the function.
   * @returns The return value of the compensation function.
   */
  async execute(args: TArgs): Promise<TResult> {
    const callIndex = this.calls.length;
    this.calls.push(args);
    try {
      const result = await this.fn(args);
      this.callCount++;
      this.results.push(result);
      return result;
    } catch (err) {
      this.errors.push({ callIndex, error: err });
      throw err;
    }
  }

  /** Execute with retry logic, re-throwing after exhausting ``maxRetries``.
   *
   * @param args - Arguments forwarded to the function.
   * @returns The return value of the compensation function.
   */
  async executeWithRetry(args: TArgs): Promise<TResult> {
    let attempts = 0;
    let lastError: unknown;
    while (attempts < this.maxRetries) {
      attempts++;
      try {
        return await this.execute(args);
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError;
  }

  /** Assert that the most recent invocation received the expected arguments.
   *
   * @param expectedArgs - Expected argument payload.
   */
  assertCalledWith(expectedArgs: TArgs): void {
    const lastCall = this.calls[this.calls.length - 1];
    if (JSON.stringify(lastCall) !== JSON.stringify(expectedArgs)) {
      throw new ValidationError(
        `Arguments mismatch: expected ${JSON.stringify(expectedArgs)}, got ${JSON.stringify(lastCall)}`,
      );
    }
  }

  /** Execute twice with identical arguments and verify return value equality.
   *
   * @param args - Arguments passed to both invocations.
   */
  async assertIdempotent(args: TArgs): Promise<void> {
    const r1 = await this.execute(args);
    const r2 = await this.execute(args);
    if (JSON.stringify(r1) !== JSON.stringify(r2)) {
      throw new ValidationError(
        `Idempotency violation: first=${JSON.stringify(r1)}, second=${JSON.stringify(r2)}`,
      );
    }
  }

  /** Reset all recorded state. */
  reset(): void {
    this.callCount = 0;
    this.calls.length = 0;
    this.results.length = 0;
    this.errors.length = 0;
  }
}

/** Create a new ``TestSaga`` instance.
 *
 * @param optionsOrSteps - Configuration options or initial step descriptors.
 * @returns A fresh ``TestSaga`` harness.
 *
 * @example
 * ```ts
 * const saga = createTestSaga();
 * saga.addStep({
 *   toolName: "reserve_inventory",
 *   compensation: async (args) => { ... },
 * });
 * const report = await saga.simulateFailureAt(1);
 * ```
 */
export function createTestSaga(
  optionsOrSteps?: TestSagaOptions | SagaStepDescriptor[],
): TestSaga {
  return new TestSaga(optionsOrSteps);
}

/** Simulate a full LIFO compensation chain without a running engine.
 *
 * @param sessionEffects - Recorded effects to roll back.
 * @returns A ``SagaReport`` with per-step outcomes and execution order.
 */
export async function testSaga(
  sessionEffects: SagaStepDescriptor[],
): Promise<SagaReport> {
  const harness = new TestSaga({ steps: sessionEffects });
  return harness.rollback();
}

/** Test one compensation function without a running engine.
 *
 * Executes the function, then verifies idempotency by invoking it a second
 * time with identical arguments and asserting matching return values.
 *
 * @param fn - The async compensation function under test.
 * @param args - Arguments to pass to the compensation call.
 * @param options - Optional retries budget and function name.
 * @returns A ``CompensationReport`` documenting execution metrics.
 */
export async function testCompensation<
  TArgs = Record<string, unknown>,
  TResult = unknown,
>(
  fn: (args: TArgs) => Promise<TResult> | TResult,
  args: TArgs,
  options?: { retries?: number; fnName?: string },
): Promise<CompensationReport> {
  const retries = options?.retries ?? 1;
  const fnName = options?.fnName ?? fn.name ?? "anonymous";
  const harness = new CompensationTestHarness(fn, retries + 1);
  let result: unknown;
  let attempts = 0;
  let finalState: "compensated" | "failed" = "failed";
  let lastError: string | undefined;

  for (let i = 0; i <= retries; i++) {
    attempts++;
    try {
      result = await harness.execute(args);
      finalState = "compensated";
      break;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
  }

  const retryCount = Math.max(attempts - 1, 0);
  if (finalState === "compensated" && retries > 0) {
    try {
      const second = await harness.execute(args);
      if (JSON.stringify(second) !== JSON.stringify(result)) {
        finalState = "failed";
        lastError = `Idempotency violation: first=${JSON.stringify(result)}, second=${JSON.stringify(second)}`;
      }
    } catch (err) {
      finalState = "failed";
      lastError = err instanceof Error ? err.message : String(err);
    }
  }

  return {
    fnName,
    executionCount: harness.callCount,
    retryCount,
    finalState,
    result,
    error: lastError,
  };
}
