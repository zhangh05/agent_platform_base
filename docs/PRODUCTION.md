# Production operations

Agent Platform Base separates record storage, object storage, and the job queue.
They can be enabled independently; this allows the common production combination
of PostgreSQL records, S3 artifacts, and Redis workers.

## Adapter configuration

```bash
export AGENT_PLATFORM_RECORD_STORE_MODE=postgres
export AGENT_PLATFORM_DATABASE_URL='postgresql://...'
export AGENT_PLATFORM_OBJECT_STORE_MODE=s3
export AGENT_PLATFORM_OBJECT_STORE_BUCKET='agent-platform-artifacts'
export AGENT_PLATFORM_OBJECT_STORE_PREFIX='production'
export AGENT_PLATFORM_QUEUE_MODE=redis
export AGENT_PLATFORM_QUEUE_URL='redis://...'
```

`GET /api/health` is the lightweight liveness check. `GET /api/ready` performs
real writable/connectivity checks for record storage, object storage, and the
queue, and includes worker heartbeat state. It returns HTTP 503 when a required
dependency is unavailable. The 系统状态 page shows the same components.

Prometheus metrics are served at `/metrics`, and a JSON projection is available
at `/api/metrics`. Metrics use route templates rather than raw URLs, preventing
workspace/job IDs from creating unbounded labels. When API authentication is
enabled, both metric endpoints require the configured bearer token or session.

## Worker leases

Redis workers claim a job with a durable lease, periodically renew it, and place
stale leases back on the queue with an incremented attempt. Configure:

```bash
export AGENT_PLATFORM_JOB_LEASE_SECONDS=120
export AGENT_PLATFORM_WORKER_STALE_SECONDS=180
export AGENT_PLATFORM_WORKER_ID='worker-a'
```

The worker status endpoint reports the worker ID, attempt, heartbeat age, and a
`stale` state when a running worker stops renewing.

Queue execution is deliberately **at least once**: a process can lose its lease
after an external side effect but before acknowledgement. Extension and workflow
job handlers must therefore use idempotency keys (normally the job ID plus step
ID) for writes to external systems.

## Verified backup and restore

Backups contain the complete workspace data plane and runtime control records.
Every file has a SHA-256 entry in the archive manifest. Snapshot creation retries
if files change while they are copied; restore rejects traversal paths, links,
device files, duplicate paths, altered hashes, and inconsistent totals.

```bash
python3 scripts/backup_cli.py create
python3 scripts/backup_cli.py list
python3 scripts/backup_cli.py verify /path/to/backup.tar.gz
python3 scripts/backup_cli.py restore backup-... --confirm RESTORE
python3 scripts/backup_cli.py prune --keep 10
```

The default archive directory is `.agent-platform-backups` beside the workspace
root. Set `AGENT_PLATFORM_BACKUP_DIR` to encrypted off-host storage in production.
Restore atomically moves the current data plane to a named rollback directory
before activating the verified snapshot. The same operations are exposed under
`/api/admin/backups` and require an administrator in identity mode.

## Immutable release slots

Build the frontend first, then stage and activate a release:

```bash
npm --prefix frontend run build
python3 scripts/release_slots.py --release-root /opt/agent-platform stage 1.4.0
python3 scripts/release_slots.py --release-root /opt/agent-platform activate 1.4.0 \
  --health-url http://127.0.0.1:8011/api/ready
```

The service should execute from `/opt/agent-platform/current`. Activation swaps a
symlink atomically and retains the prior slot. If the optional readiness check
fails, the CLI immediately switches back. Manual rollback is:

```bash
python3 scripts/release_slots.py --release-root /opt/agent-platform rollback
```

Environment files, workspaces, logs, reports, virtual environments, and source
control metadata are never copied into immutable release slots. Keep configuration
and durable data outside the release directory.
