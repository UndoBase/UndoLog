/**
 * LangChain integration example.
 *
 * Demonstrates wrapping a LangChain DynamicStructuredTool with UndoLog effect
 * tracking using the createUndologTool() factory. Shows tool creation, agent
 * invocation, and approval handling. Requires the `@langchain/core` and `zod`
 * packages.
 *
 * Run with: npx tsx examples/langchain.ts
 */

import { UndoLogClient, ToolTier, UndoLogSession, runWithSession } from "@undolog/sdk";
import { AwaitingApprovalError } from "@undolog/sdk/errors";
import { createUndologTool } from "@undolog/sdk/langchain";
import { mockServer } from "@undolog/sdk/testing";
import { z } from "zod";

async function main() {
  const server = mockServer({
    tools: { get_weather: ToolTier.Safe, delete_db: ToolTier.Irreversible },
  });

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
    { tier: ToolTier.Safe },
  );

  const deleteDb = createUndologTool(
    client,
    {
      name: "delete_db",
      description: "Delete a database",
      schema: z.object({ name: z.string() }),
      func: async ({ name }) => {
        return `Database "${name}" deleted`;
      },
    },
    { tier: ToolTier.Irreversible },
  );

  const session = new UndoLogSession({ metadata: { source: "langchain-example" } });

  await runWithSession(session, async () => {
    const weather = await getWeather.invoke({ location: "London" });
    console.log("Weather:", weather);

    try {
      await deleteDb.invoke({ name: "production" });
    } catch (err) {
      if (err instanceof AwaitingApprovalError) {
        console.log("Approval required:", err.approvalId);
        await client.approve(err.approvalId);
        console.log("Approved:", err.toolName);
      }
    }
  });

  console.log("Done.");
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
