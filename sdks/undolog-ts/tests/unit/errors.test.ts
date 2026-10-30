import { describe, it, expect } from "vitest";
import {
  UndoLogError,
  AuthenticationError,
  AwaitingApprovalError,
  CompensationError,
  ConfigurationError,
  EffectLogError,
  EffectLogConcurrencyError,
  MissingSessionError,
  MissingSessionContextError,
  NotFoundError,
  PermissionError,
  RateLimitError,
  SerializationError,
  ServerError,
  TimeoutError,
  ToolNotRegisteredError,
  ToolRegistrationError,
  InvalidStateTransitionError,
  ApprovalNotFoundError,
  ValidationError,
  ErrorCodes,
  ERROR_DOCS_BASE_URL,
  formatErrorDocsUrl,
  type ErrorCode,
} from "../../src/errors.js";

describe("formatErrorDocsUrl", () => {
  it("formats upper snake case error codes into lowercase kebab runbook URLs", () => {
    expect(formatErrorDocsUrl(ErrorCodes.AUTHENTICATION_FAILED)).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#authentication-failed"
    );
    expect(formatErrorDocsUrl(ErrorCodes.MISSING_SESSION)).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#missing-session"
    );
    expect(formatErrorDocsUrl(ErrorCodes.TIMEOUT)).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#timeout"
    );
  });

  it("returns base URL when code is empty string", () => {
    expect(formatErrorDocsUrl("")).toBe(ERROR_DOCS_BASE_URL);
  });

  it("handles whitespace and special characters cleanly", () => {
    expect(formatErrorDocsUrl(" CUSTOM_CODE ")).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#custom-code"
    );
    expect(formatErrorDocsUrl("error with spaces")).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#error-with-spaces"
    );
  });
});

describe("UndoLogError base class", () => {
  it("initializes code, message, timestamp, and canonical docsUrl", () => {
    const err = new UndoLogError("CUSTOM_CODE", "Something broke");
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(UndoLogError);
    expect(err.name).toBe("UndoLogError");
    expect(err.code).toBe("CUSTOM_CODE");
    expect(err.message).toBe("Something broke");
    expect(err.timestamp).toBeGreaterThan(0);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#custom-code"
    );
  });

  it("allows overriding docsUrl in constructor", () => {
    const customUrl = "https://custom.undolog.dev/runbooks/custom-fix";
    const err = new UndoLogError("CUSTOM_CODE", "Custom msg", customUrl);
    expect(err.docsUrl).toBe(customUrl);
  });

  it("serializes to plain JSON object via toJSON preserving code, message, and docsUrl", () => {
    const err = new UndoLogError("TEST_ERROR", "Test message");
    const json = err.toJSON();
    expect(json.name).toBe("UndoLogError");
    expect(json.code).toBe("TEST_ERROR");
    expect(json.message).toBe("Test message");
    expect(json.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#test-error"
    );
    expect(json.timestamp).toBe(err.timestamp);
  });

  it("preserves code, message, and docsUrl through JSON.stringify and JSON.parse", () => {
    const err = new UndoLogError("SERIALIZE_TEST", "Serialization message");
    const raw = JSON.stringify(err);
    const parsed = JSON.parse(raw);
    expect(parsed.code).toBe("SERIALIZE_TEST");
    expect(parsed.message).toBe("Serialization message");
    expect(parsed.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#serialize-test"
    );
    expect(parsed.name).toBe("UndoLogError");
    expect(parsed.timestamp).toBe(err.timestamp);
  });
});

describe("Structured Error Taxonomy: Subclass codes and runbook URLs", () => {
  it("AuthenticationError has correct code and docsUrl", () => {
    const err = new AuthenticationError("token expired");
    expect(err.code).toBe(ErrorCodes.AUTHENTICATION_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#authentication-failed"
    );
    expect(err.reason).toBe("token expired");
    expect(err.toJSON().reason).toBe("token expired");
  });

  it("AwaitingApprovalError has correct code and docsUrl", () => {
    const err = new AwaitingApprovalError(
      "deleteDatabase",
      { env: "prod" },
      "apr_123"
    );
    expect(err.code).toBe(ErrorCodes.AWAITING_APPROVAL);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#awaiting-approval"
    );
    expect(err.toolName).toBe("deleteDatabase");
    expect(err.args).toEqual({ env: "prod" });
    expect(err.approvalId).toBe("apr_123");
    const json = err.toJSON();
    expect(json.toolName).toBe("deleteDatabase");
    expect(json.approvalId).toBe("apr_123");
  });

  it("CompensationError has correct code and docsUrl", () => {
    const cause = new Error("network down");
    const err = new CompensationError("undoPayment", undefined, cause);
    expect(err.code).toBe(ErrorCodes.COMPENSATION_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#compensation-failed"
    );
    expect(err.fnName).toBe("undoPayment");
    expect(err.cause).toBe(cause);
    expect(err.toJSON().fnName).toBe("undoPayment");
  });

  it("ConfigurationError has correct code and docsUrl", () => {
    const err = new ConfigurationError("Missing apiKey", "apiKey");
    expect(err.code).toBe(ErrorCodes.CONFIGURATION_INVALID);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#configuration-invalid"
    );
    expect(err.key).toBe("apiKey");
    expect(err.toJSON().key).toBe("apiKey");
  });

  it("EffectLogError has correct code and docsUrl", () => {
    const err = new EffectLogError("write");
    expect(err.code).toBe(ErrorCodes.EFFECT_LOG_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#effect-log-failed"
    );
    expect(err.operation).toBe("write");
    expect(err.toJSON().operation).toBe("write");
  });

  it("EffectLogConcurrencyError has correct code and docsUrl", () => {
    const err = new EffectLogConcurrencyError("commit", 2, 3);
    expect(err.code).toBe(ErrorCodes.EFFECT_LOG_CONCURRENCY);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#effect-log-concurrency"
    );
    expect(err.expectedVersion).toBe(2);
    expect(err.actualVersion).toBe(3);
    const json = err.toJSON();
    expect(json.expectedVersion).toBe(2);
    expect(json.actualVersion).toBe(3);
  });

  it("MissingSessionError has correct code and docsUrl", () => {
    const err = new MissingSessionError("sendEmail");
    expect(err.code).toBe(ErrorCodes.MISSING_SESSION);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#missing-session"
    );
    expect(err.toolName).toBe("sendEmail");
    expect(err.toJSON().toolName).toBe("sendEmail");

    const aliasErr = new MissingSessionContextError("sendEmail");
    expect(aliasErr).toBeInstanceOf(MissingSessionError);
    expect(aliasErr.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#missing-session"
    );
  });

  it("NotFoundError has correct code and docsUrl", () => {
    const err = new NotFoundError("effect", "eff_999");
    expect(err.code).toBe(ErrorCodes.NOT_FOUND);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#not-found"
    );
    expect(err.resourceType).toBe("effect");
    expect(err.resourceId).toBe("eff_999");
    const json = err.toJSON();
    expect(json.resourceType).toBe("effect");
    expect(json.resourceId).toBe("eff_999");
  });

  it("PermissionError has correct code and docsUrl", () => {
    const err = new PermissionError("tools:execute:dangerous");
    expect(err.code).toBe(ErrorCodes.PERMISSION_DENIED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#permission-denied"
    );
    expect(err.permission).toBe("tools:execute:dangerous");
    expect(err.toJSON().permission).toBe("tools:execute:dangerous");
  });

  it("RateLimitError has correct code and docsUrl", () => {
    const err = new RateLimitError("Too many calls", 45);
    expect(err.code).toBe(ErrorCodes.RATE_LIMITED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#rate-limited"
    );
    expect(err.retryAfterSeconds).toBe(45);
    expect(err.toJSON().retryAfterSeconds).toBe(45);
  });

  it("SerializationError has correct code and docsUrl", () => {
    const cause = new TypeError("Converting circular structure to JSON");
    const err = new SerializationError("json", undefined, cause);
    expect(err.code).toBe(ErrorCodes.SERIALIZATION_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#serialization-failed"
    );
    expect(err.format).toBe("json");
    expect(err.cause).toBe(cause);
    expect(err.toJSON().format).toBe("json");
  });

  it("ServerError has correct code and docsUrl", () => {
    const err = new ServerError(503, "Service unavailable");
    expect(err.code).toBe(ErrorCodes.SERVER_ERROR);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#server-error"
    );
    expect(err.statusCode).toBe(503);
    expect(err.toJSON().statusCode).toBe(503);
  });

  it("TimeoutError has correct code and docsUrl", () => {
    const err = new TimeoutError("engine_rpc", 5000);
    expect(err.code).toBe(ErrorCodes.TIMEOUT);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#timeout"
    );
    expect(err.operation).toBe("engine_rpc");
    expect(err.timeoutMs).toBe(5000);
    const json = err.toJSON();
    expect(json.operation).toBe("engine_rpc");
    expect(json.timeoutMs).toBe(5000);
  });

  it("ToolNotRegisteredError has correct code and docsUrl", () => {
    const err = new ToolNotRegisteredError("unknownTool");
    expect(err.code).toBe(ErrorCodes.TOOL_NOT_REGISTERED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#tool-not-registered"
    );
    expect(err.toolName).toBe("unknownTool");
    expect(err.toJSON().toolName).toBe("unknownTool");
  });

  it("ToolRegistrationError has correct code and docsUrl", () => {
    const err = new ToolRegistrationError("duplicateTool", "Name conflict");
    expect(err.code).toBe(ErrorCodes.TOOL_REGISTRATION_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#tool-registration-failed"
    );
    expect(err.toolName).toBe("duplicateTool");
    expect(err.reason).toBe("Name conflict");
    const json = err.toJSON();
    expect(json.toolName).toBe("duplicateTool");
    expect(json.reason).toBe("Name conflict");
  });

  it("InvalidStateTransitionError has correct code and docsUrl", () => {
    const err = new InvalidStateTransitionError("committed", "pending");
    expect(err.code).toBe(ErrorCodes.INVALID_STATE_TRANSITION);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#invalid-state-transition"
    );
    expect(err.currentState).toBe("committed");
    expect(err.targetState).toBe("pending");
    const json = err.toJSON();
    expect(json.currentState).toBe("committed");
    expect(json.targetState).toBe("pending");
  });

  it("ApprovalNotFoundError has correct code and docsUrl", () => {
    const err = new ApprovalNotFoundError("apr_missing");
    expect(err.code).toBe(ErrorCodes.APPROVAL_NOT_FOUND);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#approval-not-found"
    );
    expect(err.approvalId).toBe("apr_missing");
    expect(err.toJSON().approvalId).toBe("apr_missing");
  });

  it("ValidationError has correct code and docsUrl", () => {
    const err = new ValidationError(
      "Count must be positive",
      "count",
      "count > 0"
    );
    expect(err.code).toBe(ErrorCodes.VALIDATION_FAILED);
    expect(err.docsUrl).toBe(
      "https://undolog.dev/docs/troubleshooting/errors#validation-failed"
    );
    expect(err.field).toBe("count");
    expect(err.constraint).toBe("count > 0");
    const json = err.toJSON();
    expect(json.field).toBe("count");
    expect(json.constraint).toBe("count > 0");
  });
});

describe("Subclass custom docsUrl overrides", () => {
  it("allows custom docsUrl on subclasses", () => {
    const custom = "https://internal.wiki/errors/auth";
    const authErr = new AuthenticationError("bad token", undefined, custom);
    expect(authErr.docsUrl).toBe(custom);
    expect(authErr.toJSON().docsUrl).toBe(custom);

    const valErr = new ValidationError("bad val", "f", "c", custom);
    expect(valErr.docsUrl).toBe(custom);
  });
});

describe("Taxonomy Completeness", () => {
  it("includes at least 13 standard error codes in ErrorCodes", () => {
    const codes = Object.values(ErrorCodes);
    expect(codes.length).toBeGreaterThanOrEqual(13);
  });

  it("maps every registered error code to a valid troubleshooting runbook URL", () => {
    for (const code of Object.values(ErrorCodes)) {
      const url = formatErrorDocsUrl(code as ErrorCode);
      expect(url.startsWith("https://undolog.dev/docs/troubleshooting/errors#")).toBe(
        true
      );
      const slug = url.split("#")[1];
      expect(slug).toBeDefined();
      expect(slug).toMatch(/^[a-z0-9-]+$/);
    }
  });

  it("JSON.stringify preserves code, message, and docsUrl on all error instances", () => {
    const errors: UndoLogError[] = [
      new AuthenticationError("expired"),
      new AwaitingApprovalError("tool", {}, "apr_1"),
      new CompensationError("comp"),
      new ConfigurationError("bad config"),
      new EffectLogError("read"),
      new EffectLogConcurrencyError("write", 1, 2),
      new MissingSessionError("tool"),
      new NotFoundError("effect", "e1"),
      new PermissionError("perm"),
      new RateLimitError("rate"),
      new SerializationError("json"),
      new ServerError(500),
      new TimeoutError("op", 100),
      new ToolNotRegisteredError("t"),
      new ToolRegistrationError("t", "r"),
      new InvalidStateTransitionError("s1", "s2"),
      new ApprovalNotFoundError("a1"),
      new ValidationError("invalid"),
    ];

    for (const err of errors) {
      const raw = JSON.stringify(err);
      const parsed = JSON.parse(raw);
      expect(parsed.code).toBe(err.code);
      expect(parsed.message).toBe(err.message);
      expect(parsed.docsUrl).toBe(err.docsUrl);
      expect(parsed.name).toBe(err.name);
      expect(parsed.timestamp).toBe(err.timestamp);
    }
  });
});
