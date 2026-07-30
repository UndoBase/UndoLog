/**
 * LangChain integration example.
 *
 * Demonstrates wrapping a LangChain DynamicStructuredTool with UndoLog effect
 * tracking using the createUndologTool() factory. Requires the `@langchain/core`
 * and `zod` packages.
 *
 * Run with: npx tsx examples/langchain.ts
 */

import { UndoLogClient, ToolTier, UndoLogSession, runWithSession } from "@undolog/sdk";
import { createUndologTool } from "@undolog/sdk/langchain";
import { mockServer } from "@undolog/sdk/testing";
import { z } from "zod";

async function main() {
  const server = mockServer();

  const client = new UndoLogClient({
    baseUrl: "http://localhost",
    httpClient: server.httpClient,
  });

  const getWeather = createUndologTool(
    client,
    {
      name: "get_weather",
      description: "Get the weather for a location",
      schema: z.object({ location: z.string() }),
      func: async ({ location }) => {
        return `It is 72\u00b0F in ${location}`;
      },
    },
    {
      toolName: "get_weather",
      tier: ToolTier.Safe,
    },
  );

  const session = new UndoLogSession({ metadata: { source: "langchain-example" } });

  await runWithSession(session, async () => {
    const result = await getWeather.invoke({ location: "London" });
    console.log("Tool output:", result);
  });

  console.log("Total effects:", server.effects.size);
  console.log("Done.");
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
