//! SQLite ApprovalStore: approval request and event operations.

use sqlx::sqlite::{SqliteConnection, SqlitePool};
use sqlx::{Row, Transaction};
use tracing::instrument;

use undolog_types::{
    approval::{ApprovalAction, ApprovalRequest, ApprovalState},
    errors::{UndoLogError, UndoLogResult},
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId},
};

use super::util::parse_timestamp;

/// Repository for approval requests and events (SQLite backend).
#[derive(Clone)]
pub struct ApprovalStore {
    pool: SqlitePool,
}

impl ApprovalStore {
    /// Create a new approval repository over the given SQLite pool.
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Begin a new SQLite transaction.
    pub async fn begin(&self) -> UndoLogResult<Transaction<'static, sqlx::Sqlite>> {
        Ok(self.pool.begin().await?)
    }

    /// Persist a new approval request (state = `pending`).
    #[instrument(skip(self, req), fields(org_id = %req.org_id, tool_name = %req.tool_name))]
    pub async fn create(&self, req: &ApprovalRequest) -> UndoLogResult<()> {
        let risk_tags = serde_json::to_string(&req.risk_tags).unwrap_or_else(|_| "[]".into());
        let proposed_args =
            serde_json::to_string(&req.proposed_args).unwrap_or_else(|_| "{}".into());
        let agent_context =
            serde_json::to_string(&req.agent_context).unwrap_or_else(|_| "{}".into());
        let timeout_at = req.timeout_at.to_rfc3339();
        let auto_approve = if req.auto_approve_on_timeout { 1 } else { 0 };

        sqlx::query(
            r#"INSERT INTO undolog_approval_requests (
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state, timeout_at, auto_approve_on_timeout, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                'pending', $11, $12, datetime('now')
            )"#,
        )
        .bind(req.approval_request_id.to_string())
        .bind(req.org_id.to_string())
        .bind(req.session_id.to_string())
        .bind(req.effect_id.to_string())
        .bind(&req.tool_name)
        .bind(&req.irreversibility_reason)
        .bind(risk_tags)
        .bind(&req.estimated_impact)
        .bind(proposed_args)
        .bind(agent_context)
        .bind(timeout_at)
        .bind(auto_approve)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    /// Resolve a pending approval request within a transaction.
    #[allow(clippy::too_many_arguments)]
    #[instrument(skip(self, conn, approved_args, note), fields(org_id = %org_id, req_id = %req_id))]
    pub async fn resolve(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
        action: &ApprovalAction,
        actor: &str,
        approved_args: Option<serde_json::Value>,
        note: Option<&str>,
    ) -> UndoLogResult<()> {
        let new_state = Self::approval_action_to_state(action);
        let approved_args_str =
            approved_args.map(|a| serde_json::to_string(&a).unwrap_or_else(|_| "null".into()));

        let rows = sqlx::query(
            r#"UPDATE undolog_approval_requests
             SET state = $1, resolved_at = datetime('now'), resolved_by = $2, approved_args = $3
             WHERE approval_request_id = $4 AND org_id = $5 AND state = 'pending'"#,
        )
        .bind(new_state)
        .bind(actor)
        .bind(approved_args_str)
        .bind(req_id.to_string())
        .bind(org_id.to_string())
        .execute(&mut *conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::ApprovalAlreadyResolved { approval_id: req_id.to_string() });
        }

        self.append_audit_event(conn, org_id, req_id, action, actor, note).await
    }

    /// Append an immutable audit event within a transaction.
    pub async fn append_audit_event(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
        action: &ApprovalAction,
        actor: &str,
        note: Option<&str>,
    ) -> UndoLogResult<()> {
        let event_id = ApprovalRequestId::new();
        let action_str = Self::approval_action_to_string(action);

        sqlx::query(
            r#"INSERT INTO undolog_approval_events (
                event_id, approval_request_id, org_id, action, actor, note, occurred_at
            ) VALUES ($1, $2, $3, $4, $5, $6, datetime('now'))"#,
        )
        .bind(event_id.to_string())
        .bind(req_id.to_string())
        .bind(org_id.to_string())
        .bind(action_str)
        .bind(actor)
        .bind(note)
        .execute(conn)
        .await?;
        Ok(())
    }

    /// Auto-approve or auto-reject timed-out requests.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn process_timeouts(&self, org_id: &OrgId) -> UndoLogResult<u64> {
        let mut tx = self.pool.begin().await?;

        let org_id_str = org_id.to_string();

        let rows: Vec<(String, bool)> = sqlx::query(
            r#"SELECT approval_request_id, auto_approve_on_timeout
             FROM undolog_approval_requests
             WHERE org_id = $1 AND state = 'pending' AND timeout_at < datetime('now')"#,
        )
        .bind(&org_id_str)
        .fetch_all(&mut *tx)
        .await?
        .iter()
        .map(|row| {
            (
                row.try_get::<String, _>("approval_request_id").unwrap(),
                row.try_get("auto_approve_on_timeout").unwrap(),
            )
        })
        .collect();

        let count = rows.len() as u64;

        for (approval_id_str, auto_approve) in &rows {
            let new_state = if *auto_approve { "auto_approved" } else { "timed_out" };

            sqlx::query(
                r#"UPDATE undolog_approval_requests
                 SET state = $1, resolved_at = datetime('now'), resolved_by = 'system:timeout'
                 WHERE approval_request_id = $2 AND org_id = $3"#,
            )
            .bind(new_state)
            .bind(approval_id_str)
            .bind(&org_id_str)
            .execute(&mut *tx)
            .await?;

            let approval_id: ApprovalRequestId = approval_id_str
                .parse()
                .map_err(|e: uuid::Error| UndoLogError::Internal(e.to_string()))?;
            self.append_audit_event(
                &mut tx,
                org_id,
                &approval_id,
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
    #[instrument(skip(self))]
    pub async fn list_orgs_with_pending_approvals(&self) -> UndoLogResult<Vec<OrgId>> {
        let rows: Vec<String> = sqlx::query_scalar(
            r#"SELECT DISTINCT org_id FROM undolog_approval_requests WHERE state = 'pending'"#,
        )
        .fetch_all(&self.pool)
        .await?;

        let mut orgs = Vec::new();
        for org_id_str in rows {
            let uuid: uuid::Uuid = org_id_str
                .parse()
                .map_err(|e| UndoLogError::Internal(format!("Invalid org_id: {e}")))?;
            orgs.push(OrgId::from(uuid));
        }
        Ok(orgs)
    }

    /// Load all unresolved approval requests for an organization.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn list_pending(&self, org_id: &OrgId) -> UndoLogResult<Vec<ApprovalRequest>> {
        let rows = sqlx::query(
            r#"SELECT
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state, timeout_at, auto_approve_on_timeout,
                resolved_at, resolved_by, approved_args, created_at
             FROM undolog_approval_requests
             WHERE org_id = $1 AND state = 'pending'
             ORDER BY created_at ASC"#,
        )
        .bind(org_id.to_string())
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter().map(map_approval_row).collect()
    }

    /// Load a single approval request by ID.
    #[instrument(skip(self), fields(org_id = %org_id, req_id = %req_id))]
    pub async fn get(
        &self,
        org_id: &OrgId,
        req_id: &ApprovalRequestId,
    ) -> UndoLogResult<Option<ApprovalRequest>> {
        let row = sqlx::query(
            r#"SELECT
                approval_request_id, org_id, session_id, effect_id,
                tool_name, irreversibility_reason, risk_tags,
                estimated_impact, proposed_args, agent_context,
                state, timeout_at, auto_approve_on_timeout,
                resolved_at, resolved_by, approved_args, created_at
             FROM undolog_approval_requests
             WHERE approval_request_id = $1 AND org_id = $2"#,
        )
        .bind(req_id.to_string())
        .bind(org_id.to_string())
        .fetch_optional(&self.pool)
        .await?;

        row.map(map_approval_row).transpose()
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
}

fn map_approval_row(row: sqlx::sqlite::SqliteRow) -> UndoLogResult<ApprovalRequest> {
    let approval_id_str: String = row.try_get("approval_request_id")?;
    let org_id_str: String = row.try_get("org_id")?;
    let session_id_str: String = row.try_get("session_id")?;
    let effect_id_str: String = row.try_get("effect_id")?;
    let state_str: String = row.try_get("state")?;

    let approval_id: uuid::Uuid = approval_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid approval_request_id: {e}")))?;
    let org_id: uuid::Uuid =
        org_id_str.parse().map_err(|e| UndoLogError::Internal(format!("Invalid org_id: {e}")))?;
    let session_id: uuid::Uuid = session_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid session_id: {e}")))?;
    let effect_id: uuid::Uuid = effect_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid effect_id: {e}")))?;

    let state = match state_str.as_str() {
        "pending" => ApprovalState::Pending,
        "approved" => ApprovalState::Approved,
        "rejected" => ApprovalState::Rejected,
        "timed_out" => ApprovalState::TimedOut,
        "auto_approved" => ApprovalState::AutoApproved,
        other => return Err(UndoLogError::Internal(format!("unknown approval state: {other}"))),
    };

    let risk_tags_str: String = row.try_get("risk_tags")?;
    let risk_tags: Vec<String> = serde_json::from_str(&risk_tags_str).unwrap_or_default();

    let proposed_args_str: String = row.try_get("proposed_args")?;
    let proposed_args: serde_json::Value =
        serde_json::from_str(&proposed_args_str).unwrap_or(serde_json::Value::Null);

    let agent_context_str: String = row.try_get("agent_context")?;
    let agent_context: serde_json::Value = serde_json::from_str(&agent_context_str)
        .unwrap_or(serde_json::Value::Object(serde_json::Map::new()));

    let approved_args_str: Option<String> = row.try_get("approved_args")?;
    let approved_args = approved_args_str.and_then(|s| serde_json::from_str(&s).ok());

    Ok(ApprovalRequest {
        approval_request_id: ApprovalRequestId::from(approval_id),
        org_id: OrgId::from(org_id),
        session_id: SessionId::from(session_id),
        effect_id: EffectId::from(effect_id),
        tool_name: row.try_get("tool_name")?,
        irreversibility_reason: row.try_get("irreversibility_reason")?,
        risk_tags,
        estimated_impact: row.try_get("estimated_impact")?,
        proposed_args,
        agent_context,
        state,
        timeout_at: parse_timestamp(&row.try_get::<String, _>("timeout_at")?),
        auto_approve_on_timeout: row.try_get("auto_approve_on_timeout")?,
        resolved_at: row.try_get::<Option<String>, _>("resolved_at")?.map(|s| parse_timestamp(&s)),
        resolved_by: row.try_get("resolved_by")?,
        approved_args,
        created_at: parse_timestamp(&row.try_get::<String, _>("created_at")?),
    })
}
