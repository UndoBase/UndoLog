/**
 * Raw intercept workflow example.
 *
 * Demonstrates the low-level UndoLog effect lifecycle using client.intercept(),
 * client.commit(), and AwaitingApprovalError handling without any framework
 * adapter. Useful for understanding the underlying primitives.
 *
 * For the framework-specific adapter, see undologTool() from
 * @undobase/undolog-sdk/vercel-ai-sdk.
 *
 * Run with: npx tsx examples/vercel-ai-sdk.ts
 */

import { UndoLogClient, ToolTier, UndoLogSession, runWithSession } from "@undobase/undolog-sdk";
import { mockServer } from "@undobase/undolog-sdk/testing";

async function main() {
  const server = mockServer();

  const client = new UndoLogClient({
    baseUrl: "http://localhost",
    httpClient: server.httpClient,
  });

  // Create a session for effect tracking.
  const session = new UndoLogSession({ metadata: { source: "vercel-example" } });

  await runWithSession(session, async () => {
    // Intercept an irreversible tool to demonstrate the approval workflow.
    try {
      await client.intercept({
        toolName: "delete_user",
        args: { userId: "42" },
        tier: ToolTier.Irreversible,
      });
    } catch (err) {
      // AwaitingApprovalError is expected for Irreversible tools.
      console.log("Approval workflow triggered", err instanceof Error ? err.message : String(err));
    }

    // Intercept a Compensable tool (succeeds immediately).
    const effect = await client.intercept({
      toolName: "send_email",
      args: { to: "user@example.com", subject: "Welcome" },
      tier: ToolTier.Compensable,
      compensation: { fnName: "recall_email" },
    });
    console.log("Effect created:", effect.effectId);

    await client.commit(effect.effectId);
    console.log("Effect committed");
  });

  console.log("Total effects:", server.effects.size);
  console.log("Done.");
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
