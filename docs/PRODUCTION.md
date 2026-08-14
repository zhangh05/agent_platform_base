# Production operations

LZCore separates record storage, object storage, and the job queue.
They can be enabled independently; this allows the common production combination
of PostgreSQL records, S3 artifacts, and Redis workers.

## Supported production profile

The repository includes a fail-closed single-node production profile at
`deployment/compose.production.yml`. It runs a Gunicorn backend, job worker,
static Nginx frontend, TLS gateway, PostgreSQL, Redis, S3-compatible object
storage, Prometheus, Alertmanager and Grafana. Copy
`deployment/.env.production.example`, create the referenced secret and TLS
files, then validate before starting:

```bash
docker compose --env-file /etc/lzcore/production.env \
  -f deployment/compose.production.yml config
docker compose --env-file /etc/lzcore/production.env \
  -f deployment/compose.production.yml up -d --build
docker compose --env-file /etc/lzcore/production.env \
  -f deployment/compose.production.yml exec backend \
  python scripts/production_preflight.py --live
```

The public listener is TLS-only. Frontend and backend ports remain internal to
the Compose network. API, login and session secrets are mounted as files rather
than committed or baked into images.

Python execution requires a verified image digest and a Docker daemon. Use a
dedicated rootless daemon/socket where possible; never expose an unauthenticated
Docker TCP endpoint. The backend fails closed when the daemon or pinned image is
unavailable.

This profile intentionally runs one Web process and one worker. Filesystem data
that has not moved into PostgreSQL/S3 is shared through `lzcore-data`; do not
scale Web or worker replicas until the distributed integration suite and the
target volume's locking semantics have been validated.

## Single-server Compose profile

An existing single host that intentionally keeps filesystem records, workspace
data and provider configuration on that host can use
`deployment/compose.server.yml`. It manages the backend, worker and static
frontend without requiring the PostgreSQL/Redis/MinIO/TLS stack. The public
HTTP entry defaults to port `5273`; the backend binds only to loopback port
`8011` and is reached by the frontend proxy.

Keep the existing root-only `.env.local`, create `deployment/.env` with the
Docker socket group id, and make the bind-mounted runtime directories writable
by container uid/gid `10001`:

```bash
printf 'LZCORE_DOCKER_SOCKET_GID=%s\n' \
  "$(stat -c '%g' /var/run/docker.sock)" > deployment/.env
chown -R 10001:10001 workspaces config/providers
docker compose -f deployment/compose.server.yml config
docker compose -f deployment/compose.server.yml up -d --build --remove-orphans
docker compose -f deployment/compose.server.yml ps
```

Do not run `start.sh` or retain screen-managed backend/frontend processes on
the same ports while this Compose project is active.

## Enterprise login

Optional OpenID Connect login is enabled with
`LZCORE_OIDC_ENABLED=true`, an HTTPS issuer, client ID, client-secret
file and `LZCORE_PUBLIC_URL`. OIDC users are never auto-provisioned:
the administrator must create the matching username and workspace grants first,
so an identity-provider account cannot invent local privileges. Password login
can remain as the bootstrap recovery path. OIDC complements rather than replaces
the route/role/workspace authorization matrix.

Start the profile with the OIDC override after adding the optional values shown
in `.env.production.example`:

```bash
docker compose --env-file /etc/lzcore/production.env \
  -f deployment/compose.production.yml -f deployment/compose.oidc.yml up -d
```

## Adapter configuration

```bash
export LZCORE_RECORD_STORE_MODE=postgres
export LZCORE_DATABASE_URL='postgresql://...'
export LZCORE_OBJECT_STORE_MODE=s3
export LZCORE_OBJECT_STORE_BUCKET='lzcore-artifacts'
export LZCORE_OBJECT_STORE_PREFIX='production'
export LZCORE_QUEUE_MODE=redis
export LZCORE_QUEUE_URL='redis://...'
export LZCORE_APPROVAL_TTL_SECONDS=1800
export LZCORE_CONTINUATION_STALL_SECONDS=900
export LZCORE_CONTINUATION_RETENTION_DAYS=30
```

普通 Agent 的高风险审批以最终 approval id 和加密 continuation 一致创建。
执行线程会持续写入 heartbeat；超过 stall 阈值的 `running` 记录只会转为
`stalled` 并告警，平台不会自动重放结果未知的外部操作。管理员可在系统状态页
核对异常记录并将其关闭，关闭操作不会重新执行工具。终态记录按 retention 天数
清理，密文在拒绝、失败、完成、过期或人工关闭时立即删除。

`GET /api/health` is the lightweight liveness check. `GET /api/ready` performs
real writable/connectivity checks for record storage, object storage, and the
queue, and includes worker heartbeat state. It returns HTTP 503 when a required
dependency is unavailable. The 系统状态 page shows the same components.

Prometheus metrics are served at `/metrics`, and a JSON projection is available
at `/api/metrics`. Metrics use route templates rather than raw URLs, preventing
workspace/job IDs from creating unbounded labels. When API authentication is
enabled, both metric endpoints require the configured bearer token or session.
The supplied Prometheus configuration reads that token from the same Docker
secret. Alert rules and the initial Grafana dashboard live under
`deployment/observability`; replace the placeholder Alertmanager receiver before
go-live and follow `docs/OPERATIONS_RUNBOOK.md` during incidents.

## Worker leases

Redis workers claim a job with a durable lease, periodically renew it, and place
stale leases back on the queue with an incremented attempt. Configure:

```bash
export LZCORE_JOB_LEASE_SECONDS=120
export LZCORE_WORKER_STALE_SECONDS=180
export LZCORE_WORKER_ID='worker-a'
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

The default archive directory is `.lzcore-backups` beside the workspace
root. Set `LZCORE_BACKUP_DIR` to encrypted off-host storage in production.
Restore atomically moves the current data plane to a named rollback directory
before activating the verified snapshot. The same operations are exposed under
`/api/admin/backups` and require an administrator in identity mode.

## Immutable release slots

Build the frontend first, then stage and activate a release:

```bash
npm --prefix frontend run build
python3 scripts/release_slots.py --release-root /opt/lzcore stage 1.4.0
python3 scripts/release_slots.py --release-root /opt/lzcore activate 1.4.0 \
  --health-url http://127.0.0.1:8011/api/ready
```

The service should execute from `/opt/lzcore/current`. Activation swaps a
symlink atomically and retains the prior slot. If the optional readiness check
fails, the CLI immediately switches back. Manual rollback is:

```bash
python3 scripts/release_slots.py --release-root /opt/lzcore rollback
```

Environment files, workspaces, logs, reports, virtual environments, and source
control metadata are never copied into immutable release slots. Keep configuration
and durable data outside the release directory.
