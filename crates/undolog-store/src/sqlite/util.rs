//! Shared utilities for SQLite row mapping.

/// Parse an RFC 3339 timestamp string into `chrono::DateTime<Utc>`.
///
/// Falls back to `Utc::now()` if the string cannot be parsed. This matches
/// the PostgreSQL backend behaviour where timestamps are always valid (enforced
/// by the database), but SQLite stores them as plain TEXT.
pub(crate) fn parse_timestamp(s: &str) -> chrono::DateTime<chrono::Utc> {
    chrono::DateTime::parse_from_rfc3339(s)
        .map(|dt| dt.with_timezone(&chrono::Utc))
        .unwrap_or_else(|_| chrono::Utc::now())
}
