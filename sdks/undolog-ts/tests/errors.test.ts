import { describe, it, expect } from "vitest";
import {
  UndoLogError,
  ErrorCodes,
  AuthenticationError,
  AwaitingApprovalError,
  CompensationError,
  ConfigurationError,
  EffectLogError,
  EffectLogConcurrencyError,
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
} from "../src/errors.js";

describe("UndoLogError", () => {
  it("stores code, message, and timestamp", () => {
    const err = new UndoLogError("TEST_CODE", "test message");
    expect(err.code).toBe("TEST_CODE");
    expect(err.message).toBe("test message");
    expect(err.timestamp).toBeGreaterThan(0);
    expect(err.name).toBe("UndoLogError");
    expect(err).toBeInstanceOf(Error);
  });
});

describe("AuthenticationError", () => {
  it("creates with reason and default message", () => {
    const err = new AuthenticationError("expired");
    expect(err.code).toBe(ErrorCodes.AUTHENTICATION_FAILED);
    expect(err.reason).toBe("expired");
    expect(err.message).toContain("expired");
    expect(err.name).toBe("AuthenticationError");
  });

  it("accepts custom message", () => {
    const err = new AuthenticationError("missing", "Custom auth message");
    expect(err.message).toBe("Custom auth message");
  });
});

describe("AwaitingApprovalError", () => {
  it("stores tool details and approvalId", () => {
    const err = new AwaitingApprovalError("delete_db", { db: "prod" }, "apr_123");
    expect(err.code).toBe(ErrorCodes.AWAITING_APPROVAL);
    expect(err.toolName).toBe("delete_db");
    expect(err.args).toEqual({ db: "prod" });
    expect(err.approvalId).toBe("apr_123");
    expect(err.message).toContain("delete_db");
    expect(err.name).toBe("AwaitingApprovalError");
  });
});

describe("CompensationError", () => {
  it("creates with fnName and optional cause", () => {
    const cause = new Error("underlying");
    const err = new CompensationError("undoSendEmail", "Failed to compensate", cause);
    expect(err.code).toBe(ErrorCodes.COMPENSATION_FAILED);
    expect(err.fnName).toBe("undoSendEmail");
    expect(err.cause).toBe(cause);
    expect(err.name).toBe("CompensationError");
  });

  it("creates without cause", () => {
    const err = new CompensationError("undoAction");
    expect(err.fnName).toBe("undoAction");
    expect(err.cause).toBeUndefined();
  });
});

describe("ConfigurationError", () => {
  it("stores message and optional key", () => {
    const err = new ConfigurationError("Missing apiKey", "apiKey");
    expect(err.code).toBe(ErrorCodes.CONFIGURATION_INVALID);
    expect(err.key).toBe("apiKey");
    expect(err.message).toBe("Missing apiKey");
    expect(err.name).toBe("ConfigurationError");
  });

  it("creates without key", () => {
    const err = new ConfigurationError("General config error");
    expect(err.key).toBeUndefined();
  });
});

describe("EffectLogError", () => {
  it("stores operation and defaults message", () => {
    const err = new EffectLogError("write");
    expect(err.code).toBe(ErrorCodes.EFFECT_LOG_FAILED);
    expect(err.operation).toBe("write");
    expect(err.message).toContain("write");
    expect(err.name).toBe("EffectLogError");
  });

  it("accepts custom message", () => {
    const err = new EffectLogError("flush", "Custom flush error");
    expect(err.message).toBe("Custom flush error");
  });
});

describe("EffectLogConcurrencyError", () => {
  it("stores version mismatch details", () => {
    const err = new EffectLogConcurrencyError("write", 3, 5);
    expect(err.code).toBe(ErrorCodes.EFFECT_LOG_CONCURRENCY);
    expect(err.operation).toBe("write");
    expect(err.expectedVersion).toBe(3);
    expect(err.actualVersion).toBe(5);
    expect(err.message).toContain("3");
    expect(err.message).toContain("5");
    expect(err.name).toBe("EffectLogConcurrencyError");
  });

  it("is an instance of EffectLogError and UndoLogError", () => {
    const err = new EffectLogConcurrencyError("read", 1, 2);
    expect(err).toBeInstanceOf(EffectLogError);
    expect(err).toBeInstanceOf(UndoLogError);
  });
});

describe("NotFoundError", () => {
  it("stores resource type and id", () => {
    const err = new NotFoundError("tool", "send_email");
    expect(err.code).toBe(ErrorCodes.NOT_FOUND);
    expect(err.resourceType).toBe("tool");
    expect(err.resourceId).toBe("send_email");
    expect(err.message).toContain("send_email");
    expect(err.name).toBe("NotFoundError");
  });
});

describe("SerializationError", () => {
  it("stores format and optional cause", () => {
    const cause = new Error("parse failure");
    const err = new SerializationError("json", "Could not serialize", cause);
    expect(err.code).toBe(ErrorCodes.SERIALIZATION_FAILED);
    expect(err.format).toBe("json");
    expect(err.cause).toBe(cause);
    expect(err.name).toBe("SerializationError");
  });

  it("creates without cause", () => {
    const err = new SerializationError("msgpack");
    expect(err.cause).toBeUndefined();
  });
});

describe("TimeoutError", () => {
  it("stores operation and timeoutMs", () => {
    const err = new TimeoutError("compensate", 5000);
    expect(err.code).toBe(ErrorCodes.TIMEOUT);
    expect(err.operation).toBe("compensate");
    expect(err.timeoutMs).toBe(5000);
    expect(err.message).toContain("5000");
    expect(err.name).toBe("TimeoutError");
  });
});

describe("ToolRegistrationError", () => {
  it("stores tool name and reason", () => {
    const err = new ToolRegistrationError("my_tool", "duplicate name");
    expect(err.code).toBe(ErrorCodes.TOOL_REGISTRATION_FAILED);
    expect(err.toolName).toBe("my_tool");
    expect(err.reason).toBe("duplicate name");
    expect(err.message).toContain("my_tool");
    expect(err.name).toBe("ToolRegistrationError");
  });
});

describe("ValidationError", () => {
  it("stores field and constraint", () => {
    const err = new ValidationError("amount must be positive", "amount", "must be > 0");
    expect(err.code).toBe(ErrorCodes.VALIDATION_FAILED);
    expect(err.field).toBe("amount");
    expect(err.constraint).toBe("must be > 0");
    expect(err.message).toBe("amount must be positive");
    expect(err.name).toBe("ValidationError");
  });

  it("creates without field or constraint", () => {
    const err = new ValidationError("generic validation failure");
    expect(err.field).toBeUndefined();
    expect(err.constraint).toBeUndefined();
  });
});

describe("PermissionError", () => {
  it("stores permission and defaults message", () => {
    const err = new PermissionError("admin:write");
    expect(err.code).toBe(ErrorCodes.PERMISSION_DENIED);
    expect(err.permission).toBe("admin:write");
    expect(err.message).toContain("admin:write");
    expect(err.name).toBe("PermissionError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("accepts custom message", () => {
    const err = new PermissionError("read", "Custom permission message");
    expect(err.message).toBe("Custom permission message");
  });
});

describe("ToolNotRegisteredError", () => {
  it("stores tool name and defaults message", () => {
    const err = new ToolNotRegisteredError("send_email");
    expect(err.code).toBe(ErrorCodes.TOOL_NOT_REGISTERED);
    expect(err.toolName).toBe("send_email");
    expect(err.message).toContain("send_email");
    expect(err.name).toBe("ToolNotRegisteredError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("accepts custom message", () => {
    const err = new ToolNotRegisteredError("delete_db", "Custom tool message");
    expect(err.message).toBe("Custom tool message");
  });
});

describe("InvalidStateTransitionError", () => {
  it("stores current and target state", () => {
    const err = new InvalidStateTransitionError("committed", "pending");
    expect(err.code).toBe(ErrorCodes.INVALID_STATE_TRANSITION);
    expect(err.currentState).toBe("committed");
    expect(err.targetState).toBe("pending");
    expect(err.message).toContain("committed");
    expect(err.message).toContain("pending");
    expect(err.name).toBe("InvalidStateTransitionError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("accepts custom message", () => {
    const err = new InvalidStateTransitionError("failed", "committed", "Custom state message");
    expect(err.message).toBe("Custom state message");
  });
});

describe("ApprovalNotFoundError", () => {
  it("stores approvalId and defaults message", () => {
    const err = new ApprovalNotFoundError("apr_456");
    expect(err.code).toBe(ErrorCodes.APPROVAL_NOT_FOUND);
    expect(err.approvalId).toBe("apr_456");
    expect(err.message).toContain("apr_456");
    expect(err.name).toBe("ApprovalNotFoundError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("accepts custom message", () => {
    const err = new ApprovalNotFoundError("apr_789", "Custom approval message");
    expect(err.message).toBe("Custom approval message");
  });
});

describe("RateLimitError", () => {
  it("defaults message and stores retryAfterSeconds", () => {
    const err = new RateLimitError(undefined, 30);
    expect(err.code).toBe(ErrorCodes.RATE_LIMITED);
    expect(err.retryAfterSeconds).toBe(30);
    expect(err.message).toBe("Rate limit exceeded");
    expect(err.name).toBe("RateLimitError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("creates without retryAfterSeconds", () => {
    const err = new RateLimitError();
    expect(err.retryAfterSeconds).toBeUndefined();
  });

  it("accepts custom message", () => {
    const err = new RateLimitError("Too many requests", 60);
    expect(err.message).toBe("Too many requests");
    expect(err.retryAfterSeconds).toBe(60);
  });
});

describe("ServerError", () => {
  it("stores statusCode and defaults message", () => {
    const err = new ServerError(503);
    expect(err.code).toBe(ErrorCodes.SERVER_ERROR);
    expect(err.statusCode).toBe(503);
    expect(err.message).toContain("503");
    expect(err.name).toBe("ServerError");
    expect(err).toBeInstanceOf(UndoLogError);
  });

  it("accepts custom message", () => {
    const err = new ServerError(500, "Internal failure");
    expect(err.message).toBe("Internal failure");
  });
});
