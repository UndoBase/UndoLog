/**
 * LangChain integration example.
 *
 * Demonstrates wrapping a LangChain DynamicStructuredTool with UndoLog effect
 * tracking using the createUndologTool() factory. Requires the `@langchain/core`
 * and `zod` packages.
 *
 * Run with: npx tsx examples/langchain.ts
 */

import { UndoLogClient, ToolTier, wrapTool, UndoLogSession, runWithSession } from "@undolog/sdk";
import { mockServer } from "@undolog/sdk/testing";

async function main() {
  const server = mockServer();

  const client = new UndoLogClient({
    baseUrl: "http://localhost",
    httpClient: server.httpClient,
  });

  // Manually wrap a tool with the core wrapTool() function.
  // (Directly using createUndologTool would require @langchain/core at runtime.)
  const getWeather = wrapTool(client, {
    name: "get_weather",
    description: "Get the weather for a location",
    tier: ToolTier.Safe,
    fn: async ({ location }: { location: string }) => {
      return `It is 72\u00b0F in ${location}`;
    },
  });

  const session = new UndoLogSession({ metadata: { source: "langchain-example" } });

  await runWithSession(session, async () => {
    const result = await getWeather({ location: "London" });
    console.log("Tool output:", result);
  });

  console.log("Total effects:", server.effects.size);
  console.log("Done.");
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
