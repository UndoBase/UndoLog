---
title: "How to configure and manage approval gates"
description: "## Prerequisites"
section: "guides"
---
# How to configure and manage approval gates

## Prerequisites

- [IRREVERSIBLE tools annotated](annotating-tools.md) in your Python code
- UndoLog proxy running on `http://localhost:8080`
- API key configured via `UNDOLOG_PROXY_API_KEYS`
- Dashboard accessible at `http://localhost:3000`

## What you'll build

You will send an IRREVERSIBLE tool call, inspect the pending approval, approve
it via REST, and observe the session resume. You will also review the approval
audit trail.

## Steps

### 1. Trigger a pending approval

Send an IRREVERSIBLE tool call through the proxy:

```bash
curl -X POST http://localhost:8080/mcp/tool_call \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: my-api-key" \
  -H "X-Org-Id: org-demo" \
  -d '{
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "tool_name": "delete_database",
    "step_index": 1,
    "args": {"db_name": "prod-db"}
  }'
```

The proxy returns HTTP 202 with:

```json
{
  "status": "pending_approval",
  "approval_id": "a1b2c3d4e5f6...",
  "retry_after": 5
}
```

### 2. List pending approvals

```bash
curl -s "http://localhost:8080/approvals?state=pending" \
  -H "X-Org-Id: org-demo" | jq .
```

Response:

```json
[
  {
    "id": "a1b2c3d4e5f6...",
    "org_id": "org-demo",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "effect_id": "eff-001",
    "tool_name": "delete_database",
    "args": "{\"db_name\":\"prod-db\"}",
    "status": "pending",
    "created_at": "2026-05-23T12:00:00Z"
  }
]
```

### 3. Approve or reject via REST

Approve:

```bash
curl -X POST "http://localhost:8080/approvals/a1b2c3d4e5f6.../approve" \
  -H "X-Org-Id: org-demo"
```

Reject:

```bash
curl -X POST "http://localhost:8080/approvals/a1b2c3d4e5f6.../reject" \
  -H "X-Org-Id: org-demo"
```

A successful decision returns:

```json
{"status": "approved", "approval_id": "a1b2c3d4e5f6..."}
```

If the approval is already resolved, you get HTTP 409:

```json
{"error": "approval already resolved"}
```

### 4. Approve from the dashboard

Navigate to `http://localhost:3000`. The dashboard shows pending approvals in
real time via SSE. Each row shows the tool name, arguments, and timestamps.
Click **Approve** or **Reject** to resolve.

### 5. Retry the tool call after approval

After approval, the agent retries the same step. The engine replays the cached
result:

```bash
curl -X POST http://localhost:8080/mcp/tool_call \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: my-api-key" \
  -H "X-Org-Id: org-demo" \
  -d '{
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "tool_name": "delete_database",
    "step_index": 1,
    "args": {"db_name": "prod-db"}
  }'
```

Response:

```json
{"status": "replayed", "effect_id": "eff-001", "result": {"status": "deleted"}}
```

### 6. Review the audit trail

Approval events are recorded in the `undolog_approval_events` table. Query it
directly:

```sql
SELECT ae.id, ae.approval_id, ae.event_type, ae.actor, ae.created_at
FROM undolog_approval_events ae
WHERE ae.org_id = 'org-demo'
ORDER BY ae.created_at DESC;
```

## Verify it works

Run the full lifecycle in one script:

```bash
SESSION_ID="test-session-$(uuidgen)"
APPROVAL_RESPONSE=$(curl -s -X POST http://localhost:8080/mcp/tool_call \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: my-api-key" \
  -H "X-Org-Id: org-demo" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"tool_name\": \"delete_database\",
    \"step_index\": 1,
    \"args\": {\"db_name\": \"test-db\"}
  }")
APPROVAL_ID=$(echo "$APPROVAL_RESPONSE" | jq -r '.approval_id')
echo "Approval ID: $APPROVAL_ID"

# Approve it
curl -s -X POST "http://localhost:8080/approvals/$APPROVAL_ID/approve" \
  -H "X-Org-Id: org-demo" | jq .

# Retry
curl -s -X POST http://localhost:8080/mcp/tool_call \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: my-api-key" \
  -H "X-Org-Id: org-demo" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"tool_name\": \"delete_database\",
    \"step_index\": 1,
    \"args\": {\"db_name\": \"test-db\"}
  }" | jq .
```

Expected final response:

```json
{"status": "replayed", "effect_id": "eff-...", "result": {"status": "deleted"}}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` on approval endpoints | Missing or invalid `X-Org-Id` | Send `X-Org-Id` matching the org that owns the approval |
| `404 Not Found` on approve/reject | Wrong approval ID | List pending approvals and copy the exact ID |
| `409 Conflict` on decision | Approval already resolved | Check `state=approved` or `state=rejected` to see the current status |
| Retry returns `pending_approval` again | Not yet approved | Wait for the approval webhook; check `GET /approvals?state=pending` |
| SSE dashboard not updating | Browser tab throttled | Open DevTools Network tab, verify SSE events arrive every 5s |
| Approval decision not persisted after proxy restart | In-memory store | Production deployments use the database-backed store via the engine |

## Next steps

- [Deploy with Docker Compose](deploying-with-docker.md)
- [Run UndoLog in production](running-in-production.md)
- [Write compensations for COMPENSABLE tools](writing-compensations.md)
