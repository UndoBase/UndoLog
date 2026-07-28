export { UndoLogClient } from "./client.js";
export type {
  EffectStatus,
  EffectRecord,
  SessionRecord,
  UndoLogClientOptions,
  InterceptParams,
} from "./client.js";

export { wrapTool } from "./decorators.js";
export type { ToolDefinition, WrappedTool } from "./decorators.js";

export {
  UndoLogError,
  AuthenticationError,
  AwaitingApprovalError,
  CompensationError,
  ConfigurationError,
  EffectLogError,
  EffectLogConcurrencyError,
  MissingSessionError,
  NotFoundError,
  SerializationError,
  TimeoutError,
  ToolRegistrationError,
  ValidationError,
  ErrorCodes,
} from "./errors.js";

export { createHttpClient } from "./http.js";
export type { HttpClientOptions, RequestOptions, HttpClient } from "./http.js";

export { UndoLogSession, getCurrentSession, requireCurrentSession, runWithSession } from "./session.js";
export type { SessionOptions } from "./session.js";

export { canonicalJson, callSignature } from "./signature.js";

export {
  ToolTier,
  requiresApproval,
  isCompensable,
  isSafe,
} from "./tier.js";
export type { CompensationDescriptor } from "./tier.js";
