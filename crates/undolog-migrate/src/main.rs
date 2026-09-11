//! UndoLog Standalone Migration CLI
//!
//! Applies, rolls back, and reports the status of database migrations.
//! Uses sqlx `_sqlx_migrations` table for version tracking and idempotent
//! execution. Rollback SQL is hardcoded in this binary to avoid embedding
//! rollback statements in migration files that are also executed by
//! PostgreSQL's `docker-entrypoint-initdb.d`.
//!
//! Usage:
//!     undolog-migrate up [--database-url URL] [--dry-run]
//!     undolog-migrate down [--database-url URL] [--dry-run]
//!     undolog-migrate status [--database-url URL]

use std::env;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha384};
use sqlx::postgres::{PgPool, PgPoolOptions};
use tracing::{info, warn};

/// Advisory lock number used to prevent concurrent migration runs.
const ADVISORY_LOCK_KEY: i64 = 83729;

/// Default timeout for acquiring the advisory lock (seconds).
const LOCK_TIMEOUT_SECS: u64 = 30;

/// Interval between retry attempts when trying to acquire the lock (milliseconds).
const LOCK_RETRY_MS: u64 = 200;

// ── CLI entry point ─────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args: Vec<String> = env::args().skip(1).collect();

    let database_url = parse_database_url(&args)?;

    let subcommand = args.first().map(|s| s.as_str()).unwrap_or("status");

    let dry_run = args.contains(&"--dry-run".to_string());

    match subcommand {
        "up" => run_up(&database_url, dry_run).await,
        "down" => run_down(&database_url, dry_run).await,
        "status" => run_status(&database_url).await,
        "--help" | "-h" => {
            print_help();
            Ok(())
        }
        other => {
            bail!("Unknown subcommand: {other}. Use 'up', 'down', or 'status'.");
        }
    }
}

// ── Subcommands ─────────────────────────────────────────────────────────────

/// Apply all pending migrations.
async fn run_up(database_url: &str, dry_run: bool) -> Result<()> {
    let pool = connect_pool(database_url).await?;

    info!("Acquiring advisory lock for migration");
    acquire_advisory_lock(&pool).await?;

    let pending = get_pending_migrations(&pool).await?;

    if pending.is_empty() {
        info!("No pending migrations");
        println!("Already up to date.");
        return Ok(());
    }

    info!(count = pending.len(), "Found pending migrations");

    for (version, description) in &pending {
        if dry_run {
            println!("[dry-run] Would apply: {description} ({version})");
            continue;
        }

        info!(version = %version, description = %description, "Applying migration");
        apply_migration(&pool, *version, description).await?;
        println!("Applied: {description} ({version})");
    }

    if !dry_run {
        println!("All migrations applied successfully.");
    }

    Ok(())
}

/// Roll back the last applied migration.
async fn run_down(database_url: &str, dry_run: bool) -> Result<()> {
    let pool = connect_pool(database_url).await?;

    info!("Acquiring advisory lock for migration");
    acquire_advisory_lock(&pool).await?;

    let last_applied = get_last_applied_migration(&pool).await?;

    let (version, description) = match last_applied {
        Some(v) => v,
        None => {
            println!("No migrations to roll back.");
            return Ok(());
        }
    };

    if dry_run {
        println!("[dry-run] Would roll back: {description} ({version})");
        return Ok(());
    }

    let down_sql =
        get_rollback_sql(version).context(format!("Migration {version} has no rollback SQL"))?;

    info!(version = %version, "Rolling back migration");

    let mut tx = pool.begin().await.context("Failed to begin transaction")?;

    sqlx::raw_sql(&down_sql).execute(&mut *tx).await.context("Failed to execute rollback SQL")?;

    sqlx::query("DELETE FROM _sqlx_migrations WHERE version = $1")
        .bind(version)
        .execute(&mut *tx)
        .await
        .context("Failed to remove migration record")?;

    tx.commit().await.context("Failed to commit rollback transaction")?;

    println!("Rolled back: {description} ({version})");

    Ok(())
}

/// Show the current migration status.
async fn run_status(database_url: &str) -> Result<()> {
    let pool = connect_pool(database_url).await?;

    let all_migrations = get_all_migrations();
    let applied = get_applied_versions(&pool).await?;

    println!("Migration Status");
    println!("================");
    println!();

    for &(version, ref description) in &all_migrations {
        let status = if applied.contains(&version) { "Applied" } else { "Pending " };
        println!("  [{status}] {version} - {description}");
    }

    println!();
    println!("Total: {} applied, {} pending", applied.len(), all_migrations.len() - applied.len());

    Ok(())
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Connect to PostgreSQL with connection pooling.
async fn connect_pool(database_url: &str) -> Result<PgPool> {
    PgPoolOptions::new()
        .max_connections(2)
        .connect(database_url)
        .await
        .context("Failed to connect to PostgreSQL")
}

/// Acquire a PostgreSQL advisory lock with a timeout.
///
/// Uses `pg_try_advisory_lock` in a retry loop. If the lock cannot be acquired
/// within `LOCK_TIMEOUT_SECS`, returns an error instead of blocking forever.
async fn acquire_advisory_lock(pool: &PgPool) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(LOCK_TIMEOUT_SECS);

    loop {
        let acquired: bool = sqlx::query_scalar::<_, bool>("SELECT pg_try_advisory_lock($1)")
            .bind(ADVISORY_LOCK_KEY)
            .fetch_one(pool)
            .await
            .context("Failed to query advisory lock")?;

        if acquired {
            return Ok(());
        }

        if Instant::now() >= deadline {
            bail!(
                "Could not acquire migration lock within {LOCK_TIMEOUT_SECS}s. \
                 Another migration process may be running."
            );
        }

        warn!("Migration lock held by another process, retrying in {LOCK_RETRY_MS}ms");
        tokio::time::sleep(Duration::from_millis(LOCK_RETRY_MS)).await;
    }
}

/// Get the set of migration versions that have already been applied.
///
/// Checks both `_sqlx_migrations` (populated by this CLI) and
/// `undolog_schema_migrations` (populated by `docker-entrypoint-initdb.d`)
/// so the CLI correctly reflects migrations applied via either path.
async fn get_applied_versions(pool: &PgPool) -> Result<Vec<i64>> {
    let mut versions: Vec<i64> = Vec::new();

    // Check _sqlx_migrations (populated by this CLI).
    let rows: Vec<(i64,)> = sqlx::query_as("SELECT version FROM _sqlx_migrations ORDER BY version")
        .fetch_all(pool)
        .await
        .unwrap_or_default();
    versions.extend(rows.into_iter().map(|(v,)| v));

    // Also check undolog_schema_migrations (populated by docker-entrypoint-initdb.d).
    // The version column is TEXT like '0001', '0002', etc. Map to i64.
    let legacy_rows: Vec<(String,)> =
        sqlx::query_as("SELECT version FROM undolog_schema_migrations ORDER BY version")
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    for (v,) in legacy_rows {
        if let Ok(num) = v.parse::<i64>() {
            if !versions.contains(&num) {
                versions.push(num);
            }
        } else if let Ok(num) = v.trim_start_matches('0').parse::<i64>() {
            if !versions.contains(&num) {
                versions.push(num);
            }
        }
    }

    versions.sort();
    Ok(versions)
}

/// Get the last applied migration (highest version).
async fn get_last_applied_migration(pool: &PgPool) -> Result<Option<(i64, String)>> {
    let row: Option<(i64, String)> = sqlx::query_as(
        "SELECT version, description FROM _sqlx_migrations ORDER BY version DESC LIMIT 1",
    )
    .fetch_optional(pool)
    .await
    .context("Failed to query migration status")?;

    Ok(row)
}

/// Get pending migrations (applied versions not in the full list).
async fn get_pending_migrations(pool: &PgPool) -> Result<Vec<(i64, String)>> {
    let all = get_all_migrations();
    let applied = get_applied_versions(pool).await?;

    Ok(all.into_iter().filter(|(v, _)| !applied.contains(v)).collect())
}

/// Parse the --database-url argument from the args list.
fn parse_database_url(args: &[String]) -> Result<String> {
    for (i, arg) in args.iter().enumerate() {
        if arg == "--database-url" {
            return args.get(i + 1).cloned().context("--database-url requires a value");
        }
        if arg.starts_with("--database-url=") {
            return Ok(arg.trim_start_matches("--database-url=").to_string());
        }
    }

    env::var("DATABASE_URL")
        .context("No database URL provided. Set DATABASE_URL or use --database-url URL.")
}

/// Print help message.
fn print_help() {
    println!(
        "\
UndoLog Migration CLI

USAGE:
    undolog-migrate <COMMAND> [OPTIONS]

COMMANDS:
    up       Apply all pending migrations
    down     Roll back the last applied migration
    status   Show current migration status

OPTIONS:
    --database-url URL   PostgreSQL connection URL (or set DATABASE_URL)
    --dry-run            Preview SQL without executing
    -h, --help           Print help"
    );
}

// ── Migration data ──────────────────────────────────────────────────────────

/// All known migrations in order.
///
/// Version numbers match the file prefix in `migrations/`. The description is
/// recorded in `_sqlx_migrations` for each applied migration.
fn get_all_migrations() -> Vec<(i64, String)> {
    vec![
        (
            1,
            "Initial schema - orgs, projects, tool registry, effect log, undo stack, approvals"
                .to_string(),
        ),
        (2, "Add retry configuration columns to undolog_undo_stack".to_string()),
        (3, "Seed demo organisation and tool registrations".to_string()),
        (4, "Seed a second demo organisation for multi-tenant demos".to_string()),
    ]
}

/// Apply a single migration by version.
async fn apply_migration(pool: &PgPool, version: i64, description: &str) -> Result<()> {
    let sql = get_up_sql(version).context(format!("Unknown migration version: {version}"))?;

    sqlx::raw_sql(&sql)
        .execute(pool)
        .await
        .context(format!("Failed to apply migration {version}"))?;

    // Compute SHA-384 checksum of the migration SQL, matching sqlx's format.
    let checksum = compute_checksum(&sql);

    // The _sqlx_migrations table uses BIGINT for version and BYTEA for checksum.
    sqlx::query(
        "INSERT INTO _sqlx_migrations \
         (version, description, success, checksum, execution_time) \
         VALUES ($1, $2, true, $3, 0)",
    )
    .bind(version)
    .bind(description)
    .bind(checksum)
    .execute(pool)
    .await
    .context("Failed to record migration")?;

    Ok(())
}

/// Compute SHA-384 checksum of SQL content, stored as BYTEA.
///
/// Matches the checksum format used by sqlx's `_sqlx_migrations` table so the
/// CLI and `sqlx::migrate!()` are interoperable.
fn compute_checksum(sql: &str) -> Vec<u8> {
    let mut hasher = Sha384::new();
    hasher.update(sql.as_bytes());
    hasher.finalize().to_vec()
}

/// Get the up SQL for a migration version.
///
/// Returns the entire file contents. Migration files are plain SQL with no
/// markers; they are executed both by this CLI and by PostgreSQL's
/// `docker-entrypoint-initdb.d` (via `psql`), so they must contain only
/// forward SQL.
fn get_up_sql(version: i64) -> Option<String> {
    match version {
        1 => Some(include_str!("../../../migrations/0001_initial.sql").trim().to_string()),
        2 => Some(
            include_str!("../../../migrations/0002_add_undo_stack_retry_fields.sql")
                .trim()
                .to_string(),
        ),
        3 => Some(include_str!("../../../migrations/0003_seed_demo_data.sql").trim().to_string()),
        4 => {
            Some(include_str!("../../../migrations/0004_seed_demo_org_two.sql").trim().to_string())
        }
        _ => None,
    }
}

/// Get the rollback SQL for a migration version.
///
/// Rollback SQL is hardcoded here rather than embedded in the migration files
/// because `migrations/` is mounted into PostgreSQL's `/docker-entrypoint-initdb.d`.
/// SQL files in that directory are executed verbatim by `psql`, which treats
/// `--` lines as comments. Embedding rollback statements (DROP TABLE, etc.)
/// alongside forward statements would destroy the schema on first startup.
fn get_rollback_sql(version: i64) -> Option<String> {
    match version {
        1 => Some(
            "\
DROP VIEW IF EXISTS undolog_active_sessions;

DROP TRIGGER IF EXISTS undolog_approval_counter ON undolog_approval_requests;
DROP FUNCTION IF EXISTS undolog_update_approval_counter();

DROP TRIGGER IF EXISTS undolog_effect_log_counter ON undolog_effect_log;
DROP FUNCTION IF EXISTS undolog_update_session_counters();

DROP POLICY IF EXISTS undolog_session_snapshots_isolation ON undolog_session_snapshots;
DROP POLICY IF EXISTS undolog_approval_events_isolation ON undolog_approval_events;
DROP POLICY IF EXISTS undolog_approval_requests_isolation ON undolog_approval_requests;
DROP POLICY IF EXISTS undolog_undo_stack_isolation ON undolog_undo_stack;
DROP POLICY IF EXISTS undolog_effect_log_isolation ON undolog_effect_log;
DROP POLICY IF EXISTS undolog_sessions_isolation ON undolog_sessions;
DROP POLICY IF EXISTS undolog_compensation_registry_isolation ON undolog_compensation_registry;
DROP POLICY IF EXISTS undolog_tool_registry_isolation ON undolog_tool_registry;
DROP POLICY IF EXISTS undolog_projects_isolation ON undolog_projects;
DROP POLICY IF EXISTS undolog_orgs_isolation ON undolog_orgs;

ALTER TABLE IF EXISTS undolog_session_snapshots    DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_approval_events      DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_approval_requests    DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_undo_stack           DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_effect_log           DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_sessions             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_compensation_registry DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_tool_registry        DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_projects             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS undolog_orgs                 DISABLE ROW LEVEL SECURITY;

DROP TABLE IF EXISTS undolog_session_snapshots;
DROP TABLE IF EXISTS undolog_approval_events;
DROP TABLE IF EXISTS undolog_approval_requests;
DROP TABLE IF EXISTS undolog_undo_stack;
DROP TABLE IF EXISTS undolog_effect_log;
DROP TABLE IF EXISTS undolog_sessions;
DROP TABLE IF EXISTS undolog_compensation_registry;
DROP TABLE IF EXISTS undolog_tool_registry;
DROP TABLE IF EXISTS undolog_projects;
DROP TABLE IF EXISTS undolog_orgs;
DROP TABLE IF EXISTS undolog_schema_migrations;

DROP TYPE IF EXISTS undolog_approval_action;
DROP TYPE IF EXISTS undolog_approval_state;
DROP TYPE IF EXISTS undolog_session_state;
DROP TYPE IF EXISTS undolog_effect_state;
DROP TYPE IF EXISTS undolog_tool_tier;

DROP FUNCTION IF EXISTS uuidv7_or_random();"
                .to_string(),
        ),
        2 => Some(
            "\
ALTER TABLE undolog_undo_stack
  DROP COLUMN IF EXISTS max_retries,
  DROP COLUMN IF EXISTS retry_backoff_ms;"
                .to_string(),
        ),
        3 => Some(
            "\
DELETE FROM undolog_tool_registry
  WHERE org_id = '00000000-0000-0000-0000-000000000001';
DELETE FROM undolog_orgs
  WHERE org_id = '00000000-0000-0000-0000-000000000001';"
                .to_string(),
        ),
        4 => Some(
            "\
DELETE FROM undolog_tool_registry
  WHERE org_id = '00000000-0000-0000-0000-000000000002';
DELETE FROM undolog_orgs
  WHERE org_id = '00000000-0000-0000-0000-000000000002';"
                .to_string(),
        ),
        _ => None,
    }
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_database_url_from_flag() {
        let args = vec![
            "undolog-migrate".to_string(),
            "up".to_string(),
            "--database-url".to_string(),
            "postgresql://localhost/test".to_string(),
        ];
        assert_eq!(parse_database_url(&args).unwrap(), "postgresql://localhost/test");
    }

    #[test]
    fn parse_database_url_from_inline() {
        let args = vec![
            "undolog-migrate".to_string(),
            "up".to_string(),
            "--database-url=postgresql://localhost/test".to_string(),
        ];
        assert_eq!(parse_database_url(&args).unwrap(), "postgresql://localhost/test");
    }

    #[test]
    fn parse_database_url_requires_value() {
        let args =
            vec!["undolog-migrate".to_string(), "up".to_string(), "--database-url".to_string()];
        assert!(parse_database_url(&args).is_err());
    }

    #[test]
    fn get_up_sql_returns_content() {
        let sql = get_up_sql(1);
        assert!(sql.is_some());
        assert!(sql.unwrap().contains("CREATE TABLE"));
    }

    #[test]
    fn get_rollback_sql_returns_content() {
        let sql = get_rollback_sql(2);
        assert!(sql.is_some());
        assert!(sql.unwrap().contains("DROP COLUMN"));
    }

    #[test]
    fn get_all_migrations_returns_four() {
        let migrations = get_all_migrations();
        assert_eq!(migrations.len(), 4);
    }

    #[test]
    fn get_all_migrations_versions_are_sequential() {
        let migrations = get_all_migrations();
        for (i, &(version, _)) in migrations.iter().enumerate() {
            assert_eq!(version, (i + 1) as i64);
        }
    }

    #[test]
    fn get_rollback_sql_returns_none_for_unknown() {
        assert!(get_rollback_sql(999).is_none());
    }

    #[test]
    fn get_up_sql_returns_none_for_unknown() {
        assert!(get_up_sql(999).is_none());
    }

    #[test]
    fn compute_checksum_is_deterministic() {
        let sql = "CREATE TABLE test (id int);";
        let a = compute_checksum(sql);
        let b = compute_checksum(sql);
        assert_eq!(a, b);
    }

    #[test]
    fn compute_checksum_differs_for_different_content() {
        let a = compute_checksum("CREATE TABLE test (id int);");
        let b = compute_checksum("CREATE TABLE test (id text);");
        assert_ne!(a, b);
    }

    #[test]
    fn compute_checksum_returns_48_bytes() {
        let checksum = compute_checksum("test");
        assert_eq!(checksum.len(), 48, "SHA-384 produces 48 bytes");
    }
}
