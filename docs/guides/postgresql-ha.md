---
title: "PostgreSQL High Availability"
description: "WAL streaming, read replicas, health checks, and connection pooling for production UndoLog deployments."
section: "guides"
---
# PostgreSQL High Availability

This guide covers PostgreSQL high availability for production UndoLog deployments: WAL streaming for point-in-time recovery, read replicas for read-heavy workloads, health checks, and connection pooling.

---

## Prerequisites

- PostgreSQL 16 or later
- A primary database server with `wal_level = logical` or `wal_level = replica`
- Network connectivity between primary and replicas

---

## 1. WAL streaming for point-in-time recovery

Write-ahead log (WAL) streaming allows point-in-time recovery (PITR) by continuously archiving WAL segments to a backup location.

### Enable WAL archiving on the primary

```ini
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /wal_archive/%f && cp %p /wal_archive/%f'
max_wal_senders = 3
wal_keep_size = 1GB
```

### Create a replication user

```sql
CREATE USER undolog_replicator WITH REPLICATION LOGIN PASSWORD 'secure_password';
```

### Configure `pg_hba.conf`

```
# TYPE  DATABASE        USER                ADDRESS         METHOD
host    replication     undolog_replicator  10.0.0.0/8      scram-sha-256
```

### Base backup

Before archiving can be useful, take a base backup:

```bash
pg_basebackup -h localhost -U undolog_replicator -D /backups/base -Ft -z -P
```

### Restore from WAL archive

To restore to a specific point in time:

```bash
# Create recovery signal file
touch /var/lib/postgresql/data/recovery.signal

# Set restore command in postgresql.conf
restore_command = 'cp /wal_archive/%f %p'
recovery_target_time = '2026-09-11 14:30:00+00'
```

---

## 2. Read replicas

Read replicas offload read-heavy queries from the primary. UndoLog's `TierRegistry` refresh and `list_pending_approvals` are read-only operations suitable for replicas.

### Streaming replication setup

On the primary, configure:

```ini
wal_level = replica
max_wal_senders = 5
wal_keep_size = 2GB
hot_standby = on
```

On the replica, configure `primary_conninfo`:

```ini
# postgresql.conf (replica)
primary_conninfo = 'host=primary port=5432 user=undolog_replicator password=secure_password'
```

### Using a read replica for tier registry refresh

The engine refreshes the `TierRegistry` from the database periodically. For read-heavy deployments, configure the engine's `DATABASE_URL` to point at a read replica, or use a separate connection string for registry reads if your deployment supports it.

The `TierRegistry` refresh loop runs in the background and is the only read-heavy operation suitable for replicas. All write operations (intercept, commit, fail, approve, reject) must go to the primary.

### Limitations

- Replicas have replication lag (typically milliseconds to seconds).
- The effect log and approval writes must always go to the primary.
- Do not route `intercept`, `commit`, `fail`, `approve`, or `reject` RPCs to a replica. These require advisory locks and transaction-scoped writes.

---

## 3. Connection pooling with PgBouncer

PgBouncer in transaction mode is the recommended connection pooler for UndoLog. It reuses server-side connections across client transactions, reducing connection overhead.

### Configuration

```ini
; pgbouncer.ini
[databases]
undolog = host=primary port=5432 dbname=undolog

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 200
default_pool_size = 25
min_pool_size = 5
reserve_pool_size = 5
reserve_pool_timeout = 3
server_idle_timeout = 300
server_connect_timeout = 5
server_login_retry = 3
```

### Pool mode selection

| Mode | Description | When to use |
|------|-------------|-------------|
| `transaction` | Connection returned after each transaction | **Recommended.** Works with UndoLog's per-transaction `SET LOCAL` for RLS. |
| `session` | Connection held for entire client session | Use only if application requires session state (e.g. `SET` commands outside transactions). |
| `statement` | Connection returned after each statement | **Not compatible** with UndoLog. Advisory locks and RLS require transaction scope. |

### Tuning pool size

The optimal pool size depends on concurrent engine instances and workload:

```
pool_size = (concurrent_engine_instances * avg_concurrent_transactions) + headroom
```

For a typical deployment with 2 engine instances and 10 concurrent transactions each:

```
default_pool_size = 25  # (2 * 10) + 5 headroom
```

### Monitoring pool health

```bash
# Check active connections
psql -h pgbouncer -p 6432 -U postgres -c "SHOW POOLS;"

# Check client/server connection counts
psql -h pgbouncer -p 6432 -U postgres -c "SHOW STATS;"
```

---

## 4. Health checks

### Engine health endpoint

The engine exposes an HTTP health endpoint on port 9090:

```bash
curl -fsS http://<engine>:9090/
# {"status":"ok","service":"undolog-engine"}
```

### PostgreSQL health check

```bash
# Check if PostgreSQL is accepting connections
pg_isready -h localhost -p 5432 -U postgres

# Check replication lag (on replica)
SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;
```

### Docker Compose health check

```yaml
services:
  postgres:
    image: postgres:16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
```

---

## 5. Failover

### Automatic failover with Patroni

[Patroni](https://github.com/zalando/patroni) provides automatic failover for PostgreSQL clusters using etcd, Consul, or ZooKeeper as the distributed consensus store.

```yaml
# patroni.yml
scope: undolog
namespace: /db/
name: node1

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    postgresql:
      use_pg_rewind: true
      parameters:
        wal_level: replica
        max_wal_senders: 5
        max_replication_slots: 5

postgresql:
  listen: 0.0.0.0:5432
  data_dir: /var/lib/postgresql/data/patroni
  authentication:
    replication:
      username: undolog_replicator
      password: secure_password
    superuser:
      username: postgres
      password: secure_password
```

### Manual failover

If Patroni is not available, perform manual failover:

```bash
# 1. Stop writes to the primary
# 2. Promote the replica
pg_ctlcluster 16 main promote

# 3. Update DATABASE_URL to point to the new primary
export DATABASE_URL=postgres://postgres:postgres@new-primary:5432/undolog

# 4. Restart the engine
```

### Application-level resilience

The engine handles database reconnections automatically:

- Advisory lock retries (`UNDOLOG_LOCK_MAX_ATTEMPTS`) handle transient connection failures.
- The `TierRegistry` refresh loop reconnects on failure.
- The approval timeout processor continues processing after reconnecting.

---

## 6. Backup and recovery

### Logical backup

```bash
# Daily backup
pg_dump -h localhost -U postgres undolog | gzip > /backups/undolog-$(date +%Y%m%d).sql.gz

# Restore
gunzip -c /backups/undolog-20260911.sql.gz | psql -h localhost -U postgres undolog
```

### WAL-based backup

For point-in-time recovery, combine base backups with WAL archiving:

```bash
# Continuous WAL archiving
archive_command = 'rsync -a %p wal-archive/%f'

# Recovery to specific time
recovery_target_time = '2026-09-11 14:30:00+00'
```

---

## 7. Monitoring

### Key metrics

| Metric | Warning threshold | Critical threshold | Source |
|--------|------------------|-------------------|--------|
| Replication lag | > 1s | > 10s | `pg_last_xact_replay_timestamp()` |
| Active connections | > 80% of pool | > 95% of pool | `pg_stat_activity` |
| Advisory lock wait time | > 100ms | > 500ms | Engine logs |
| Transaction rate | Baseline deviation > 50% | Baseline deviation > 200% | `pg_stat_database` |

### Prometheus queries

```promql
# Replication lag in seconds
pg_replication_lag{instance="undolog-replica"}

# Active connections
pg_stat_activity_count{state="active",datname="undolog"}

# Transactions per second
rate(pg_stat_database_xact_commit{datname="undolog"}[5m])
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Replication lag growing | Replica overloaded or network latency | Check replica resource usage; increase `max_wal_senders` on primary |
| Connection pool exhaustion | Pool size too small for workload | Increase `default_pool_size` in PgBouncer; check for connection leaks |
| Advisory lock timeout | High concurrent writes with same signature | Increase `UNDOLOG_LOCK_MAX_ATTEMPTS`; check if tool signatures are duplicated |
| WAL archive gaps | Archive command failing | Verify `archive_command` permissions and disk space |
| Failover not triggered | Patroni DCS unreachable | Check etcd/Consul cluster health; verify Patroni logs |

---

## See also

- [Database comparison](../reference/database-comparison.md): feature comparison across PostgreSQL, SQLite, and MySQL
- [Running in production](running-in-production.md): production deployment checklist
- [Database schema](../reference/database-schema.md): table definitions and indexes
