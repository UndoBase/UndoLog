# @undolog/sdk

TypeScript SDK for the UndoLog effect-tracking and exactly-once execution system.

UndoLog tracks tool and function effects across LLM agents, providing exactly-once
semantics, human-in-the-loop approval for irreversible actions, and compensation-based
rollback for recoverable operations.

## Installation

```bash
npm install @undolog/sdk
```

The package ships as both ESM and CommonJS and includes full TypeScript declaration
files. It requires Node.js 18 or later.

**Optional peer dependencies** (install only the integrations you need):

| Package                   | Import path                          | Required for              |
| ------------------------- | ------------------------------------ | ------------------------- |
| `ai` (Vercel AI SDK)      | `@undolog/sdk/vercel-ai-sdk` | Vercel AI SDK integration  |
| `@langchain/core`         | `@undolog/sdk/langchain`    | LangChain integration      |
| `openai`                  | `@undolog/sdk/openai`       | OpenAI Agents SDK          |
| `mastra`                  | `@undolog/sdk/mastra`       | Mastra integration         |
| `@modelcontextprotocol/sdk` | `@undolog/sdk/mcp`        | MCP server                 |

## Quickstart

### 1. Create a client

```typescript
import { UndoLogClient } from "@undolog/sdk";

const client = new UndoLogClient({
  baseUrl: "http://localhost:8080",
  apiKey: "your-api-key",
});
```

### 2. Wrap a tool

```typescript
import { wrapTool, ToolTier } from "@undolog/sdk";

const sendEmail = wrapTool(client, {
  name: "send_email",
  description: "Send an email to a recipient",
  tier: ToolTier.Compensable,
  fn: async ({ to, subject }: { to: string; subject: string }) => {
    // your actual tool implementation
    return { sent: true, to, subject };
  },
  compensation: {
    fnName: "recall_email",
    args: { to: "{{to}}", subject: "{{subject}}" },
  },
});

// The wrapped function can be called directly:
const result = await sendEmail({ to: "user@example.com", subject: "Hello" });
```

### 3. Run with session

```typescript
import { UndoLogSession, runWithSession } from "@undolog/sdk";

const session = new UndoLogSession({ metadata: { userId: "abc-123" } });

const output = await runWithSession(session, async () => {
  // All intercept calls inside this scope automatically
  // attach to `session` via AsyncLocalStorage.
  const r1 = await sendEmail({ to: "alice@example.com", subject: "Hi" });
  const r2 = await sendEmail({ to: "bob@example.com", subject: "Hey" });
  return { r1, r2 };
});
```

### 4. Handle approval (irreversible tools)

```typescript
import { AwaitingApprovalError } from "@undolog/sdk";

const deleteUser = wrapTool(client, {
  name: "delete_user",
  description: "Permanently delete a user account",
  tier: ToolTier.Irreversible,
  fn: async ({ userId }: { userId: string }) => {
    // destructive action
  },
});

try {
  await deleteUser({ userId: "42" });
} catch (err) {
  if (err instanceof AwaitingApprovalError) {
    // Persist `err.approvalId` and wait for human review.
    // Later: client.approve(err.approvalId) or client.reject(err.approvalId)
    console.log(`Approval required: ${err.approvalId}`);
  }
}
```

## Framework integrations

### Vercel AI SDK

```typescript
import { tool } from "ai";
import { z } from "zod";
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { undologTool } from "@undolog/sdk/vercel-ai-sdk";

const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

const weatherTool = undologTool(
  client,
  {
    description: "Get the weather for a location",
    parameters: z.object({ location: z.string() }),
    execute: async ({ location }) => {
      return { temperature: 72, location };
    },
  },
  { toolName: "get_weather", tier: ToolTier.Safe },
);

// Pass to generateText / streamText:
// const result = await generateText({
//   model,
//   tools: { get_weather: weatherTool },
//   prompt: "What is the weather in London?",
// });
```

### LangChain

```typescript
import { z } from "zod";
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { createUndologTool } from "@undolog/sdk/langchain";

const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

const weatherTool = createUndologTool(
  client,
  {
    name: "get_weather",
    description: "Get the weather for a location",
    schema: z.object({ location: z.string() }),
    func: async ({ location }) => {
      return `It is 72\u00b0F in ${location}`;
    },
  },
  { toolName: "get_weather", tier: ToolTier.Safe },
);
```

### OpenAI Agents SDK

```typescript
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { undologFunctionTool } from "@undolog/sdk/openai";

const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

const weatherTool = undologFunctionTool(
  client,
  {
    name: "get_weather",
    description: "Get the weather for a location",
    parameters: {
      type: "object",
      properties: { location: { type: "string" } },
      required: ["location"],
    },
    execute: async ({ location }) => {
      return { temperature: 72, location };
    },
  },
  { toolName: "get_weather", tier: ToolTier.Safe },
);
```

### Mastra

```typescript
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { undologMastraTool } from "@undolog/sdk/mastra";

const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

const reverseTool = undologMastraTool(
  client,
  {
    id: "reverse-string",
    description: "Reverse the input string",
    execute: async ({ input }) => ({
      output: input.split("").reverse().join(""),
    }),
  },
  { toolName: "reverse-string", tier: ToolTier.Compensable },
);
```

## MCP server

Expose UndoLog-wrapped tools as MCP tools over stdio transport:

```typescript
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { createUndoLogMcpServer } from "@undolog/sdk/mcp";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const client = new UndoLogClient({ baseUrl: "http://localhost:8080" });

const server = createUndoLogMcpServer(
  client,
  [
    {
      name: "get_weather",
      description: "Get the current weather for a location",
      inputSchema: {
        type: "object",
        properties: { location: { type: "string" } },
        required: ["location"],
      },
      tier: ToolTier.Safe,
      execute: async ({ location }: { location: string }) => {
        return { temperature: 72, condition: "sunny", location };
      },
    },
  ],
);

const transport = new StdioServerTransport();
await server.connect(transport);
```

## Testing helpers

The `testing` sub-path provides a fully in-memory mock server so you can
test your UndoLog integration without a real server:

```typescript
import { UndoLogClient, ToolTier } from "@undolog/sdk";
import { mockServer, createMockEffect, createMockSession } from "@undolog/sdk/testing";

// Create a mock server and inject its HttpClient
const server = mockServer();
const client = new UndoLogClient({
  baseUrl: "http://localhost",
  httpClient: server.httpClient,
});

// Interact with the client normally
await client.intercept({
  toolName: "test_tool",
  args: { key: "value" },
  tier: ToolTier.Compensable,
});

// Assert on server state
console.log(server.effects.size); // 1

// Create test doubles
const effect = createMockEffect({ status: "committed" });
const session = createMockSession();
```

Cross-language parity assertions are also provided:

```typescript
import {
  assertCanonicalJsonParity,
  assertSignatureParity,
} from "@undolog/sdk/testing";

const result = assertCanonicalJsonParity(
  { b: 1, a: 2 },
  { a: 2, b: 1 },
);
console.log(result.pass); // true

const sigResult = assertSignatureParity(
  "550e8400-e29b-41d4-a716-446655440000",
  1,
  "send_email",
  { to: "alice@example.com", subject: "Hello" },
  "8f20ad25773b270753b417b05437f5644997cb43e70a11a9e3b4e6d9a9d32546",
);
console.log(sigResult.pass); // true (when signature matches)
```

## Tool tiers

| Tier          | Behaviour                                                              | Compensation | Approval |
| ------------- | ---------------------------------------------------------------------- | ------------ | -------- |
| `Safe`        | Executes immediately; no effect log entry.                             | None         | No       |
| `Compensable` | Effect is logged; can be undone via a compensation function.           | Optional     | No       |
| `Irreversible`| Effect is logged; execution requires explicit human approval.          | None         | Yes      |

## Error handling

All SDK errors extend `UndoLogError` and carry a machine-readable `code`:

```typescript
import {
  UndoLogError,
  AwaitingApprovalError,
  AuthenticationError,
  NotFoundError,
} from "@undolog/sdk";

try {
  await client.intercept({ toolName: "t", args: {}, tier: ToolTier.Irreversible });
} catch (err) {
  if (err instanceof AwaitingApprovalError) {
    // Human approval required before execution
    console.log(err.approvalId);
  } else if (err instanceof UndoLogError) {
    console.log(err.code, err.message);
  }
}
```

## API reference

Full API reference is generated with TypeDoc. See the
[documentation site](https://undolog.undobase.com) for details.

## License

Apache 2.0
