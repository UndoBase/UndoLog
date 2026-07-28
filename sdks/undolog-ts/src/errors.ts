/** Typed error hierarchy for the UndoLog SDK.
 *
 * Every error carries a machine-readable `code` for programmatic handling
 * and a human-readable `message`. Use `instanceof` checks to distinguish
 * error types in catch blocks.
 *
 * @module
 */

/**
 * Base class for all UndoLog SDK errors.
 *
 * @remarks
 * Every error in the SDK extends this class. Catch `UndoLogError` when you
 * want to handle all SDK-originated errors uniformly.
 */
export class UndoLogError extends Error {
  /** Machine-readable error code for programmatic routing. */
  readonly code: string;
  /** Timestamp (epoch ms) when the error was constructed. */
  readonly timestamp: number;

  constructor(code: string, message: string) {
    super(message);
    this.name = "UndoLogError";
    this.code = code;
    this.timestamp = Date.now();
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Error codes shared across the hierarchy. */
export const ErrorCodes = {
  AUTHENTICATION_FAILED: "AUTHENTICATION_FAILED",
  AWAITING_APPROVAL: "AWAITING_APPROVAL",
  COMPENSATION_FAILED: "COMPENSATION_FAILED",
  CONFIGURATION_INVALID: "CONFIGURATION_INVALID",
  EFFECT_LOG_CONCURRENCY: "EFFECT_LOG_CONCURRENCY",
  EFFECT_LOG_FAILED: "EFFECT_LOG_FAILED",
  MISSING_SESSION: "MISSING_SESSION",
  NOT_FOUND: "NOT_FOUND",
  SERIALIZATION_FAILED: "SERIALIZATION_FAILED",
  TIMEOUT: "TIMEOUT",
  TOOL_REGISTRATION_FAILED: "TOOL_REGISTRATION_FAILED",
  VALIDATION_FAILED: "VALIDATION_FAILED",
} as const;

/**
 * Authentication or authorization failed.
 *
 * @remarks
 * Thrown when the API key or auth token is missing, invalid, or expired.
 */
export class AuthenticationError extends UndoLogError {
  /** The reason category: "missing", "invalid", "expired". */
  readonly reason: string;

  constructor(reason: string, message?: string) {
    const msg = message ?? `Authentication failed: ${reason}`;
    super(ErrorCodes.AUTHENTICATION_FAILED, msg);
    this.name = "AuthenticationError";
    this.reason = reason;
  }
}

/**
 * An irreversible tool call requires human approval and is waiting.
 *
 * @remarks
 * Thrown when the effect engine intercepts an Irreversible-tier call and
 * human approval has not yet been granted.
 */
export class AwaitingApprovalError extends UndoLogError {
  /** The tool name that requires approval. */
  readonly toolName: string;
  /** Serialised arguments passed to the tool. */
  readonly args: Record<string, unknown>;
  /** Unique approval request identifier. */
  readonly approvalId: string;

  constructor(toolName: string, args: Record<string, unknown>, approvalId: string, message?: string) {
    const msg = message ?? `Tool "${toolName}" requires human approval (id: ${approvalId})`;
    super(ErrorCodes.AWAITING_APPROVAL, msg);
    this.name = "AwaitingApprovalError";
    this.toolName = toolName;
    this.args = args;
    this.approvalId = approvalId;
  }
}

/**
 * Compensation (undo) function failed after exhausting retries.
 *
 * @remarks
 * Thrown when a compensation function has been retried `maxRetries` times
 * without success and escalation is required.
 */
export class CompensationError extends UndoLogError {
  /** Name of the compensation function that failed. */
  readonly fnName: string;
  /** The underlying error that caused the failure, if available. */
  readonly cause?: Error;

  constructor(fnName: string, message?: string, cause?: Error) {
    const msg = message ?? `Compensation "${fnName}" failed after retries`;
    super(ErrorCodes.COMPENSATION_FAILED, msg);
    this.name = "CompensationError";
    this.fnName = fnName;
    this.cause = cause;
  }
}

/**
 * SDK configuration is invalid or incomplete.
 *
 * @remarks
 * Thrown at initialisation when required options are missing or contradict.
 */
export class ConfigurationError extends UndoLogError {
  /** The configuration key that triggered the error. */
  readonly key?: string;

  constructor(message: string, key?: string) {
    super(ErrorCodes.CONFIGURATION_INVALID, message);
    this.name = "ConfigurationError";
    this.key = key;
  }
}

/**
 * Effect log persistence operation failed.
 *
 * @remarks
 * Thrown when the effect log cannot be written, read, or flushed.
 */
export class EffectLogError extends UndoLogError {
  /** The storage backend operation that failed. */
  readonly operation: string;

  /**
   * @param operation - The storage backend operation that failed.
   * @param message - Human-readable error message.
   * @param code - Override the error code (used by subclasses).
   */
  constructor(operation: string, message?: string, code?: string) {
    super(code ?? ErrorCodes.EFFECT_LOG_FAILED, message ?? `Effect log ${operation} failed`);
    this.name = "EffectLogError";
    this.operation = operation;
  }
}

/**
 * Concurrent modification detected on the effect log.
 *
 * @remarks
 * Thrown when an optimistic concurrency check fails (e.g. expected
 * version does not match the stored version).
 */
export class EffectLogConcurrencyError extends EffectLogError {
  /** The expected version that did not match. */
  readonly expectedVersion: number;
  /** The actual version found in storage. */
  readonly actualVersion: number;

  constructor(operation: string, expectedVersion: number, actualVersion: number, message?: string) {
    const msg =
      message ?? `Effect log ${operation} version mismatch: expected ${expectedVersion}, got ${actualVersion}`;
    super(operation, msg, ErrorCodes.EFFECT_LOG_CONCURRENCY);
    this.name = "EffectLogConcurrencyError";
    this.expectedVersion = expectedVersion;
    this.actualVersion = actualVersion;
  }
}

/**
 * No active session context available.
 *
 * @remarks
 * Thrown by ``requireCurrentSession()`` when called outside of any
 * ``runWithSession()`` scope.
 */
export class MissingSessionError extends UndoLogError {
  constructor(message?: string) {
    super(ErrorCodes.MISSING_SESSION, message ?? "No active session context");
    this.name = "MissingSessionError";
  }
}

/**
 * The requested resource was not found.
 *
 * @remarks
 * Thrown when a tool, effect entry, or compensation function is not
 * registered or does not exist.
 */
export class NotFoundError extends UndoLogError {
  /** The type of resource that was not found. */
  readonly resourceType: string;
  /** The identifier of the resource that was not found. */
  readonly resourceId: string;

  constructor(resourceType: string, resourceId: string, message?: string) {
    const msg = message ?? `${resourceType} "${resourceId}" not found`;
    super(ErrorCodes.NOT_FOUND, msg);
    this.name = "NotFoundError";
    this.resourceType = resourceType;
    this.resourceId = resourceId;
  }
}

/**
 * Serialisation or deserialisation of an effect log entry failed.
 *
 * @remarks
 * Thrown when an entry cannot be encoded for storage or decoded from
 * storage.
 */
export class SerializationError extends UndoLogError {
  /** The format that failed (e.g. "json", "msgpack"). */
  readonly format: string;
  /** The underlying error, if available. */
  readonly cause?: Error;

  constructor(format: string, message?: string, cause?: Error) {
    const msg = message ?? `Serialization failed for format "${format}"`;
    super(ErrorCodes.SERIALIZATION_FAILED, msg);
    this.name = "SerializationError";
    this.format = format;
    this.cause = cause;
  }
}

/**
 * An operation exceeded its configured deadline.
 *
 * @remarks
 * Thrown when a timeout fires before the operation completes.
 */
export class TimeoutError extends UndoLogError {
  /** The operation that timed out. */
  readonly operation: string;
  /** The timeout duration in milliseconds. */
  readonly timeoutMs: number;

  constructor(operation: string, timeoutMs: number, message?: string) {
    const msg = message ?? `Operation "${operation}" timed out after ${timeoutMs}ms`;
    super(ErrorCodes.TIMEOUT, msg);
    this.name = "TimeoutError";
    this.operation = operation;
    this.timeoutMs = timeoutMs;
  }
}

/**
 * Tool registration failed.
 *
 * @remarks
 * Thrown when a tool cannot be registered because its name conflicts with
 * an existing tool, its tier is invalid, or its schema is malformed.
 */
export class ToolRegistrationError extends UndoLogError {
  /** The tool name that failed registration. */
  readonly toolName: string;
  /** The specific reason for the failure. */
  readonly reason: string;

  constructor(toolName: string, reason: string, message?: string) {
    const msg = message ?? `Cannot register tool "${toolName}": ${reason}`;
    super(ErrorCodes.TOOL_REGISTRATION_FAILED, msg);
    this.name = "ToolRegistrationError";
    this.toolName = toolName;
    this.reason = reason;
  }
}

/**
 * Input validation failed.
 *
 * @remarks
 * Thrown when user-supplied arguments do not match the expected schema
 * or constraints.
 */
export class ValidationError extends UndoLogError {
  /** The field or parameter that failed validation. */
  readonly field?: string;
  /** Human-readable constraint description. */
  readonly constraint?: string;

  constructor(message: string, field?: string, constraint?: string) {
    super(ErrorCodes.VALIDATION_FAILED, message);
    this.name = "ValidationError";
    this.field = field;
    this.constraint = constraint;
  }
}
