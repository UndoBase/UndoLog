export { UndoLogClient } from "./client.js";
export type {
  EffectStatus,
  EffectRecord,
  SessionRecord,
  UndoLogClientOptions,
  InterceptParams,
} from "./client.js";

export { wrapTool } from "./decorators.js";
export type { ToolDefinition, WrappedTool, WrapToolOptions } from "./decorators.js";

export {
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

export {
  DEFAULT_MAX_RETRIES,
  TestSaga,
  SagaTestHarness,
  CompensationTestHarness,
  createTestSaga,
  testSaga,
  testCompensation,
} from "./testing/saga.js";

export type {
  SagaStepDescriptor,
  EffectDescriptor,
  SagaStepResult,
  StepResult,
  ExecutedCompensation,
  SagaReport,
  CompensationReport,
  TestSagaOptions,
} from "./testing/saga.js";

export { undologTool, wrapVercelAITools } from "./integrations/vercel-ai-sdk.js";
export type {
  UndologVercelTool,
  UndologVercelToolOptions,
  WrapVercelAIToolsOptions,
  WrappedVercelAITools,
} from "./integrations/vercel-ai-sdk.js";

export {
  createUndologTool,
  wrapLangChainAgent,
  wrapLangChainTools,
} from "./integrations/langchain.js";
export type {
  UndologLangchainToolOptions,
  WrapLangChainAgentOptions,
  LangChainToolLike,
  LangChainAgentExecutorLike,
  WrappedLangChainAgent,
} from "./integrations/langchain.js";

export {
  undologMastraTool,
  wrapMastraAgent,
  wrapMastraTools,
  isSuspensionState,
} from "./integrations/mastra.js";
export type {
  UndologMastraTool,
  UndologMastraToolOptions,
  WrapMastraAgentOptions,
  MastraToolLike,
  MastraAgentLike,
  MastraSuspensionState,
  WrappedMastraAgent,
  WrappedMastraTool,
} from "./integrations/mastra.js";

export type * as mcp from "./mcp/index.js";
