#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Push auto-execute-after-approval to UndoLog
# Based on docs/contributing/COMMIT_AND_PR_STANDARDS.md
# ─────────────────────────────────────────────────────────────

BRANCH="feat/auto-execute-approved-irreversible"

echo "1. Create branch from main"
git checkout main
git pull origin main
git checkout -b "$BRANCH"

echo ""
echo "2. Stage all changes (categories 1-6: crates, proto, services, docs, changelog, prompts)"
git add \
  CHANGELOG.md \
  crates/undolog-types/src/errors.rs \
  crates/undolog-store/src/approval_store.rs \
  crates/undolog-store/src/effect_store.rs \
  crates/undolog-store/src/session_store.rs \
  crates/undolog-engine/src/engine.rs \
  crates/undolog-engine/src/grpc/mod.rs \
  crates/undolog-engine/src/tier_registry.rs \
  proto/undolog.proto \
  services/undolog-proxy/internal/protocol/types.go \
  services/undolog-proxy/internal/engine/client.go \
  services/undolog-proxy/internal/engine/client_test.go \
  services/undolog-proxy/internal/engine/grpc_transport.go \
  services/undolog-proxy/internal/engine/pb/undolog.pb.go \
  services/undolog-proxy/internal/engine/pb/undolog_grpc.pb.go \
  services/undolog-proxy/internal/approval/handler.go \
  services/undolog-proxy/internal/approval/handler_test.go \
  services/undolog-proxy/internal/proxy/handler.go \
  services/undolog-proxy/internal/proxy/proxy_test.go \
  services/undolog-proxy/internal/proxy/server.go \
  docs/reference/api/python-sdk.md \
  docs/reference/tool-tiers.md \
  docs/reference/database-schema.md \
  docs/getting-started/installation.md \
  docs/guides/configuring-approval-gates.md \
  docs/guides/annotating-tools.md

echo ""
echo "3. Commit"
git commit -m "feat(engine): auto-execute approved irreversible tools via proxy

Approve transitions pending->approved. Proxy runs tool and commits."

echo ""
echo "4. Push"
git push -u origin "$BRANCH"

echo ""
echo "5. Open PR at:"
echo "   https://github.com/UndoBase/UndoLog/compare/$BRANCH"
echo ""
echo "PR title: feat(engine): auto-execute approved irreversible tools via proxy"
echo ""
echo "PR description (copy-paste into GitHub):"
cat <<- BODY
## What this PR does

When a human approves an irreversible tool, the proxy now runs the
tool directly and commits the result inline -- no separate retry
needed. Before this change, approve() only resolved the approval
request; the effect stayed in pending and no execution was triggered.

### Engine (Rust)

- \`approve()\` transitions \`pending -> approved\`, resumes the session,
  and returns \`ApprovalResult\` (effect_id, session_id, tool_name, args)
- \`reject()\` now transitions \`pending -> rejected\` (was stuck in pending)
- \`intercept()\` auto-creates sessions on first use
- \`approval_store.get()\` added: load approval data before resolution
- \`effect_store.update_args_snapshot()\` added: keeps audit trail
  accurate when the approver modifies args
- \`rows_affected()\` guards on \`approve_effect\`, \`reject_effect\`, and
  \`update_args_snapshot\` prevent silent no-ops on wrong state
- Tool tier registry reads \`compensation_ref\` and
  \`irreversibility_reason\` from DB for proper tier construction
- Removed \`ON CONFLICT DO NOTHING\` from effect inserts (advisory lock
  + \`find_by_signature\` provide sufficient deduplication)

### Proto

- \`ApproveResponse\` changed from empty to 5 fields: \`effect_id\`,
  \`session_id\`, \`tool_name\`, \`tool_version\`, \`args\`

### Proxy (Go)

- \`resolveDecision()\` on approve: calls \`engine.Approve()\`, executes
  the tool via \`ExecuteApprovedFn\` callback, commits the result
- Execution failure after approval keeps the effect in \`approved\`
  state; the error is logged and the API still returns 200
- \`CommitRequest\`, \`FailRequest\`, \`ApproveRequest\`, \`RejectRequest\`
  gain \`OrgID\` and \`SessionID\` fields
- \`EngineClient.Approve()\` returns \`ApproveResponse\`
- \`call()\` made generic: \`call[T any]\` for reuse across RPCs

### Docs

- Updated endpoint paths, IRREVERSIBLE state flow, approve response
  format, and retry-after-approval descriptions

### Bugs fixed

- \`approve_effect()\`, \`reject_effect()\`, \`update_args_snapshot()\`
  now return \`InvalidStateTransition\` on zero rows affected
- \`reject()\` reordered to load approval before resolving (prevents
  \`ApprovalNotFound\` on the callback)

## Type of change

- [X] feat: new feature
- [X] fix: bug fix

## How to test

\`\`\`bash
cargo build --workspace && cargo test --workspace
cd services/undolog-proxy && go build ./... && go test ./... -count=1
\`\`\`

Rust: 37 unit tests pass, 7 integration tests skipped (need DB).
Go: all tests pass (approval, engine, proxy, lock, sse).

## Checklist

### Code
- [X] All new pub items have doc comments
- [X] No unwrap() or expect() in production code paths
- [X] No println! or dbg! left in code
- [X] State transitions have explicit WHERE guards
- [X] \`cargo check\` passes full workspace
- [X] \`go vet\` passes

### Documentation
- [X] CHANGELOG.md updated under [Unreleased]
- [X] All 6 affected doc files updated

### Size note

The diff is approximately 750 lines across 26 files. This exceeds
the 500-line code PR limit for a single PR. The changes form one
logical unit: the engine, proto, and proxy changes are coupled
(the proxy depends on the new \`ApproveResponse\` fields, which
depend on the engine returning execution data). Breaking this into
stacked PRs would leave intermediate states with broken tests.

BODY

echo ""
echo "After merge, delete the branch:"
echo "  git checkout main && git pull && git branch -d $BRANCH"
