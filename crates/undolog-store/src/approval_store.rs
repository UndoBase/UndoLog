//! ApprovalStore - repository for `undolog_approval_requests` and `undolog_approval_events`.

use sqlx::{
    postgres::{PgConnection, PgRow},
    PgPool, Row, Transaction,
};
use undolog_types::{
    approval::{ApprovalAction, ApprovalRequest, ApprovalState},
    errors::{UndoLogError, UndoLogResult},
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId},
};

/// Repository for `undolog_approval_requests` and `undolog_approval_events`.
#[derive(Clone)]
pub struct ApprovalStore {
    pool: PgPool,
}

impl ApprovalStore {
    /// Create a new approval repository over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Begin a new database transaction on the shared pool.
    pub async fn begin(&self) -> UndoLogResult<Transaction<'static, sqlx::Postgres>> {
        Ok(self.pool.begin().await?)
    }

    /// Persist a new approval request (state = `pending`).
    pub async fn create(&self, req: &ApprovalRequest) -> UndoLogResult<()> {
        let proposed_args = serde_json::to_value(&req.proposed_args)?;
        let agent_context = serde_json::to_value(&req.agent_context)?;

        sqlx::query(
            r#"
            INSERT INTO undolog_approval_requests (
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state, timeout_at, auto_approve_on_timeout, created_at
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7::text[],
                $8, $9, $10,
                'pending'::undolog_approval_state, $11, $12, now()
            )
            "#,
        )
        .bind(*req.approval_request_id.as_uuid())
        .bind(*req.org_id.as_uuid())
        .bind(*req.session_id.as_uuid())
        .bind(*req.effect_id.as_uuid())
        .bind(&req.tool_name)
        .bind(&req.irreversibility_reason)
        .bind(&req.risk_tags)
        .bind(&req.estimated_impact)
        .bind(&proposed_args)
        .bind(&agent_context)
        .bind(req.timeout_at)
        .bind(req.auto_approve_on_timeout)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    /// Resolve a pending approval request (approve, reject, modify, or timeout).
    ///
    /// Returns `Err(ApprovalAlreadyResolved)` if the request is not in `pending` state.
    #[allow(clippy::too_many_arguments)]
    pub async fn resolve(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
        action: &ApprovalAction,
        actor: &str,
        approved_args: Option<serde_json::Value>,
        note: Option<&str>,
    ) -> UndoLogResult<()> {
        let new_state = Self::approval_action_to_state(action);

        let rows = sqlx::query(
            r#"
            UPDATE undolog_approval_requests
            SET state         = $1::undolog_approval_state,
                resolved_at   = now(),
                resolved_by   = $2,
                approved_args = $3
            WHERE approval_request_id = $4
              AND org_id              = $5
              AND state               = 'pending'::undolog_approval_state
            "#,
        )
        .bind(new_state)
        .bind(actor)
        .bind(&approved_args)
        .bind(*req_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&mut *conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::ApprovalAlreadyResolved { approval_id: req_id.to_string() });
        }

        self.append_audit_event(conn, org_id, req_id, action, actor, note).await
    }

    /// Append an immutable audit event for every action taken on an approval.
    pub async fn append_audit_event(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
        action: &ApprovalAction,
        actor: &str,
        note: Option<&str>,
    ) -> UndoLogResult<()> {
        let action_str = Self::approval_action_to_string(action);

        sqlx::query(
            r#"
            INSERT INTO undolog_approval_events (
                event_id, approval_request_id, org_id,
                action, actor, note, occurred_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3::undolog_approval_action, $4, $5, now())
            "#,
        )
        .bind(*req_id.as_uuid())
        .bind(*org_id.as_uuid())
        .bind(action_str)
        .bind(actor)
        .bind(note)
        .execute(&mut *conn)
        .await?;

        Ok(())
    }

    /// Append an immutable audit event within an existing transaction.
    pub async fn append_audit_event_with_tx(
        &self,
        tx: &mut Transaction<'static, sqlx::Postgres>,
        req_id: &ApprovalRequestId,
        org_id: &OrgId,
        action: &ApprovalAction,
        actor: &str,
        note: Option<&str>,
    ) -> UndoLogResult<()> {
        let action_str = Self::approval_action_to_string(action);

        sqlx::query(
            r#"
            INSERT INTO undolog_approval_events (
                event_id, approval_request_id, org_id,
                action, actor, note, occurred_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3::undolog_approval_action, $4, $5, now())
            "#,
        )
        .bind(*req_id.as_uuid())
        .bind(*org_id.as_uuid())
        .bind(action_str)
        .bind(actor)
        .bind(note)
        .execute(&mut **tx)
        .await?;

        Ok(())
    }

    fn approval_action_to_string(action: &ApprovalAction) -> &'static str {
        match action {
            ApprovalAction::Approve => "approve",
            ApprovalAction::Modify => "modify",
            ApprovalAction::Reject => "reject",
            ApprovalAction::Timeout => "timeout",
        }
    }

    fn approval_action_to_state(action: &ApprovalAction) -> &'static str {
        match action {
            ApprovalAction::Approve | ApprovalAction::Modify => "approved",
            ApprovalAction::Reject => "rejected",
            ApprovalAction::Timeout => "timed_out",
        }
    }

    /// Auto-approve or auto-reject timed-out requests.
    ///
    /// Called by a background task on a regular interval. Records an audit
    /// event for each processed request so the approval lifecycle is
    /// fully traceable.
    ///
    /// Returns the number of requests processed.
    pub async fn process_timeouts(&self, org_id: &OrgId) -> UndoLogResult<u64> {
        let mut tx = self.pool.begin().await?;

        // Find pending approvals that have timed out.
        let rows: Vec<(ApprovalRequestId, bool)> = sqlx::query(
            r#"
            SELECT approval_request_id, auto_approve_on_timeout
            FROM undolog_approval_requests
            WHERE org_id = $1
              AND state  = 'pending'::undolog_approval_state
              AND timeout_at < now()
            "#,
        )
        .bind(*org_id.as_uuid())
        .fetch_all(&mut *tx)
        .await?
        .iter()
        .map(|row| {
            (
                ApprovalRequestId::from_uuid(row.try_get("approval_request_id").unwrap()),
                row.try_get("auto_approve_on_timeout").unwrap(),
            )
        })
        .collect();

        let count = rows.len() as u64;

        for (approval_id, auto_approve) in &rows {
            let new_state = if *auto_approve { "auto_approved" } else { "timed_out" };

            sqlx::query(
                r#"
                UPDATE undolog_approval_requests
                SET state       = $1::undolog_approval_state,
                    resolved_at = now(),
                    resolved_by = 'system:timeout'
                WHERE approval_request_id = $2
                  AND org_id = $3
                "#,
            )
            .bind(new_state)
            .bind(*approval_id.as_uuid())
            .bind(*org_id.as_uuid())
            .execute(&mut *tx)
            .await?;

            // Record audit event.
            self.append_audit_event_with_tx(
                &mut tx,
                approval_id,
                org_id,
                &ApprovalAction::Timeout,
                "system:timeout",
                None,
            )
            .await?;
        }

        tx.commit().await?;
        Ok(count)
    }

    /// Return all organisation IDs that have pending approval requests.
    ///
    /// Used by the background timeout processor to avoid scanning orgs with
    /// no pending approvals.
    pub async fn list_orgs_with_pending_approvals(&self) -> UndoLogResult<Vec<OrgId>> {
        let rows = sqlx::query_scalar(
            r#"
            SELECT DISTINCT org_id
            FROM undolog_approval_requests
            WHERE state = 'pending'::undolog_approval_state
            "#,
        )
        .fetch_all(&self.pool)
        .await?;

        Ok(rows.into_iter().map(OrgId::from_uuid).collect())
    }

    /// Load all unresolved approval requests for an organization.
    ///
    /// Returns the pending requests ordered by creation time ascending so the
    /// proxy can rebuild its approval view deterministically after a restart.
    pub async fn list_pending(&self, org_id: &OrgId) -> UndoLogResult<Vec<ApprovalRequest>> {
        let rows = sqlx::query(
            r#"
            SELECT
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state::text, timeout_at, auto_approve_on_timeout,
                resolved_at, resolved_by, approved_args, created_at
            FROM undolog_approval_requests
            WHERE org_id = $1
              AND state  = 'pending'::undolog_approval_state
            ORDER BY created_at ASC
            "#,
        )
        .bind(*org_id.as_uuid())
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter().map(map_approval_row).collect()
    }

    /// Load a single approval request by ID.
    ///
    /// Returns `Ok(None)` when the approval request does not exist.
    pub async fn get(
        &self,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
    ) -> UndoLogResult<Option<ApprovalRequest>> {
        let row = sqlx::query(
            r#"
            SELECT
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state::text, timeout_at, auto_approve_on_timeout,
                resolved_at, resolved_by, approved_args, created_at
            FROM undolog_approval_requests
            WHERE approval_request_id = $1
              AND org_id              = $2
            "#,
        )
        .bind(*req_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_optional(&self.pool)
        .await?;

        row.map(map_approval_row).transpose()
    }
}

fn map_approval_row(row: PgRow) -> UndoLogResult<ApprovalRequest> {
    use uuid::Uuid;

    let approval_id_uuid: Uuid = row.try_get("approval_request_id")?;
    let org_id_uuid: Uuid = row.try_get("org_id")?;
    let session_id_uuid: Uuid = row.try_get("session_id")?;
    let effect_id_uuid: Uuid = row.try_get("effect_id")?;
    let state_str: String = row.try_get("state")?;

    let state = match state_str.as_str() {
        "pending" => ApprovalState::Pending,
        "approved" => ApprovalState::Approved,
        "rejected" => ApprovalState::Rejected,
        "timed_out" => ApprovalState::TimedOut,
        "auto_approved" => ApprovalState::AutoApproved,
        other => return Err(UndoLogError::Internal(format!("unknown approval state: {other}"))),
    };

    Ok(ApprovalRequest {
        approval_request_id: ApprovalRequestId::from(approval_id_uuid),
        org_id: OrgId::from(org_id_uuid),
        session_id: SessionId::from(session_id_uuid),
        effect_id: EffectId::from(effect_id_uuid),
        tool_name: row.try_get("tool_name")?,
        irreversibility_reason: row.try_get("irreversibility_reason")?,
        risk_tags: row.try_get("risk_tags")?,
        estimated_impact: row.try_get("estimated_impact")?,
        proposed_args: row.try_get("proposed_args")?,
        agent_context: row.try_get("agent_context")?,
        state,
        timeout_at: row.try_get("timeout_at")?,
        auto_approve_on_timeout: row.try_get("auto_approve_on_timeout")?,
        resolved_at: row.try_get("resolved_at")?,
        resolved_by: row.try_get("resolved_by")?,
        approved_args: row.try_get("approved_args")?,
        created_at: row.try_get("created_at")?,
    })
}
