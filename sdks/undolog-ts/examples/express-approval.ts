/**
 * Express-style approval workflow example.
 *
 * Demonstrates an HTTP server that uses UndoLog's human-in-the-loop approval
 * workflow for Irreversible-tier tools. Uses Node's built-in http module so
 * no external web framework dependency is needed.
 *
 * Run with: npx tsx examples/express-approval.ts
 * Then: curl http://localhost:3000/approve/<approvalId>
 */

import * as http from "node:http";
import { UndoLogClient, ToolTier, wrapTool, AwaitingApprovalError, UndoLogSession, runWithSession } from "@undolog/sdk";
import { mockServer } from "@undolog/sdk/testing";

async function main() {
  const server = mockServer();

  const client = new UndoLogClient({
    baseUrl: "http://localhost",
    httpClient: server.httpClient,
  });

  // An irreversible tool that requires human approval before execution.
  const deleteUser = wrapTool(client, {
    name: "delete_user",
    description: "Permanently delete a user account",
    tier: ToolTier.Irreversible,
    fn: async ({ userId }: { userId: string }) => {
      return { deleted: true, userId };
    },
  });

  // Store pending approvals for the HTTP endpoint to resolve.
  const pendingApprovals = new Map<string, { userId: string }>();

  // Start a session and execute the irreversible tool.
  // The AwaitingApprovalError is caught and the approvalId is stored.
  const session = new UndoLogSession({ metadata: { source: "express-example" } });

  await runWithSession(session, async () => {
    try {
      await deleteUser({ userId: "42" });
    } catch (err) {
      if (err instanceof AwaitingApprovalError) {
        pendingApprovals.set(err.approvalId, { userId: "42" });
        console.log(`Approval required: ${err.approvalId}`);
        console.log(`To approve: curl http://localhost:3000/approve/${err.approvalId}`);
        return;
      }
      throw err;
    }
  });

  // HTTP server to handle approval/rejection requests.
  const httpServer = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
    const parts = url.pathname.split("/").filter(Boolean);

    if (parts[0] === "approve" && parts[1]) {
      const approvalId = parts[1];
      if (pendingApprovals.has(approvalId)) {
        await client.approve(approvalId);
        pendingApprovals.delete(approvalId);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "approved", approvalId }));
      } else {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Approval not found" }));
      }
    } else if (parts[0] === "reject" && parts[1]) {
      const approvalId = parts[1];
      if (pendingApprovals.has(approvalId)) {
        await client.reject(approvalId, "Rejected by user");
        pendingApprovals.delete(approvalId);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "rejected", approvalId }));
      } else {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Approval not found" }));
      }
    } else {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          pendingApprovals: Array.from(pendingApprovals.keys()),
          instructions: "Use /approve/:id or /reject/:id to resolve pending approvals",
        }),
      );
    }
  });

  const PORT = 3000;
  httpServer.listen(PORT, () => {
    console.log(`Approval server listening on http://localhost:${PORT}`);
    console.log(`Pending approvals: ${pendingApprovals.size}`);
  });
}

main().catch((err: unknown) => {
  console.error("Example failed:", err);
  process.exit(1);
});
