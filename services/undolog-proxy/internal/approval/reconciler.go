// Package approval stores and resolves approval requests for irreversible calls.
//
// The reconciler keeps the proxy's in-memory approval view in sync with the
// engine, which is the durable source of truth, so a proxy restart does not
// orphan pending approvals.
package approval

import (
	"context"
	"log/slog"
	"time"

	"undolog-proxy/internal/protocol"
)

// ReconcileApprovals restores the proxy's approval view from the engine for the
// given organizations. Pending approvals the engine still holds are upserted
// into the in-memory store so a proxy restart does not orphan them. It returns
// the identifiers the engine confirmed as still pending and the organizations
// whose lookup failed. The retention sweep uses both: confirmed-pending records
// are always kept, and the records of a failed organization are left untouched
// until the next cycle restores its status, so a transient engine error never
// sweeps approvals a human may still decide on.
func ReconcileApprovals(ctx context.Context, engine protocol.EngineClient, store *Store, orgIDs []string, logger *slog.Logger) (active map[string]struct{}, failedOrgs map[string]struct{}) {
	if logger == nil {
		logger = slog.Default()
	}
	active = make(map[string]struct{})
	failedOrgs = make(map[string]struct{})
	for _, orgID := range orgIDs {
		resp, err := engine.ListPendingApprovals(ctx, protocol.ListPendingApprovalsRequest{OrgID: orgID})
		if err != nil {
			// Deliberately log-and-continue: a transient engine failure for one
			// organization must not abort the whole reconciliation loop or take
			// down the proxy. The org is tracked as failed so the retention
			// sweep leaves its records untouched until the next cycle.
			logger.Warn("approval reconcile failed", "org_id", orgID, "error", err)
			failedOrgs[orgID] = struct{}{}
			continue
		}
		for _, rec := range resp.Records {
			store.UpsertPending(Record{
				ID:        rec.ApprovalID,
				OrgID:     rec.OrgID,
				SessionID: rec.SessionID,
				EffectID:  rec.EffectID,
				ToolName:  rec.ToolName,
				Args:      append([]byte(nil), rec.Args...),
				Status:    StatusPending,
				CreatedAt: time.UnixMilli(rec.CreatedAtUnix).UTC(),
			})
			active[rec.ApprovalID] = struct{}{}
		}
	}
	return active, failedOrgs
}

// defaultReconcileInterval is used when configuration supplies a non-positive
// interval, which would otherwise make time.NewTicker panic.
const defaultReconcileInterval = 60 * time.Second

// RunApprovalReconciler periodically reconciles the approval view from the
// engine and sweeps records older than the retention window so the in-memory
// store stays bounded. It runs until ctx is canceled.
func RunApprovalReconciler(ctx context.Context, engine protocol.EngineClient, store *Store, orgIDs []string, interval, retention time.Duration, logger *slog.Logger) {
	if logger == nil {
		logger = slog.Default()
	}
	if interval <= 0 {
		logger.Warn("approval reconcile interval must be positive; using default", "default", defaultReconcileInterval)
		interval = defaultReconcileInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		active, failedOrgs := ReconcileApprovals(ctx, engine, store, orgIDs, logger)
		removed := store.Sweep(time.Now().UTC().Add(-retention), active, failedOrgs)
		if removed > 0 {
			logger.Info("approval store swept", "removed", removed)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
