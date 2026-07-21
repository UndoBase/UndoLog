/**
 * Basic UndoLog SDK example.
 *
 * Demonstrates creating a client, wrapping a tool, and running it within a
 * session context. Uses the in-memory mock server so no real server is needed.
 *
 * Run with: npx tsx examples/basic.ts
 */

import { UndoLogClient, ToolTier, wrapTool, UndoLogSession, runWithSession } from "@undobase/undolog-sdk";
import { mockServer } from "@undobase/undolog-sdk/testing";

async function main() {
  // Create a mock server so this example works without a real UndoLog backend.
  const server = mockServer();

  const client = new UndoLogClient({
    baseUrl: "http://localhost",
    httpClient: server.httpClient,
  });

  // Wrap a Compensable tool: the effect is logged and can be rolled back.
  const sendEmail = wrapTool(client, {
    name: "send_email",
    description: "Send an email to a recipient",
    tier: ToolTier.Compensable,
    fn: async ({ to, subject }: { to: string; subject: string }) => {
      return { sent: true, to, subject };
    },
    compensation: {
      fnName: "recall_email",
      args: { to: "{{to}}", subject: "{{subject}}" },
    },
  });

  // Wrap a Safe tool: bypasses effect registration entirely (zero overhead).
  const getWeather = wrapTool(client, {
    name: "get_weather",
    description: "Get the weather for a location",
    tier: ToolTier.Safe,
    fn: async ({ location }: { location: string }) => {
      return { temperature: 72, condition: "sunny", location };
    },
  });

  // Create a session and run tool calls within it.
  const session = new UndoLogSession({
    metadata: { example: "basic", userId: "demo" },
  });

  const output = await runWithSession(session, async () => {
    const emailResult = await sendEmail({ to: "alice@example.com", subject: "Hello" });
    const weatherResult = await getWeather({ location: "London" });
    return { emailResult, weatherResult };
  });

  console.log("Tool results:", JSON.stringify(output, null, 2));
  console.log("Effects stored on server:", server.effects.size);
  console.log("Done.");
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
