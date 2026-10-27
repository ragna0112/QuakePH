# QuakePH — Milestones 1–3

A student data engineering project covering **Philippines and surrounding region**
(4–22° latitude, 116–130° longitude). This configurable rectangle includes nearby
waters; it is not an exact national boundary.

The foundation, real USGS weekly ingestion, and resumable historical imports are
implemented. Earthquakes, pipeline attempts, and historical job/window progress
persist in PostgreSQL. The frontend is still a status shell; maps, search,
scheduling, and dashboard data APIs are not implemented.
No fixtures are inserted into the running application.

## How the pieces fit

- `backend/app/`: FastAPI serves HTTP endpoints; SQLAlchemy connects to PostgreSQL.
- `backend/alembic/`: Alembic tracks database schema versions. The baseline revision
  is followed by earthquake storage, pipeline runs, and historical job/window tables.
- `backend/tests/`: pytest checks health behavior and configuration validation.
- `frontend/`: React + TypeScript + Vite displays the application shell and API status.
  Vite forwards `/api` and `/health` requests to the backend during development.
- `compose.yaml`: runs PostgreSQL 17 with a persistent local Docker volume.

PostgreSQL connections use UTC. All event and run timestamps use timezone-aware
`TIMESTAMP WITH TIME ZONE` columns. The shell formats its check time explicitly in
Asia/Manila (UTC+08:00). Database credentials stay on the backend.

## Setup (macOS/Linux)

Requirements: Docker Desktop with its engine running, Python 3.9+ for the tooling
bootstrap, and Node 22.12+ (Node 24 LTS recommended). uv installs Python 3.12 for
the application, so the system Python does not need upgrading.

Run from the project root:

```sh
cd /Users/rich/Documents/Development/QuakePH
python3 -m venv .tools
.tools/bin/python -m pip install uv==0.13.6
.tools/bin/uv python install 3.12
[ -f .env ] || cp .env.example .env
.tools/bin/uv sync --directory backend --locked
npm --prefix frontend ci
```

Edit the private `.env` if desired. Keep `DATABASE_URL` consistent with the
`POSTGRES_*` values (URL-encode special characters in URL credentials). If you
change `POSTGRES_PORT`, update the URL port too. `.env` is ignored by Git. The
example password is only for this loopback-bound local development database.
PostgreSQL initialization settings apply when its data volume is first created;
changing `.env` does not change an existing database user's password. The example
now uses host port **55432**, matching the verified local configuration.

Start Docker Desktop, then:

```sh
docker compose up -d --wait
.tools/bin/uv run --directory backend --locked alembic upgrade head
.tools/bin/uv run --directory backend --locked alembic current
```

In terminal 1, from the project root:

```sh
.tools/bin/uv run --directory backend --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In terminal 2, from the project root:

```sh
npm --prefix frontend run dev
```

Open <http://127.0.0.1:5174> and API docs at <http://127.0.0.1:8000/docs>.
Port 5174 is intentional because 5173 was already occupied on the development
machine. If 8000 or 5174 is occupied, stop the relevant process or update the Vite
port/proxy configuration consistently. `npm run preview` only previews static
build output and does not provide the development API proxy.

## Check it works

```sh
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:5174/api/config
docker compose ps
.tools/bin/uv run --directory backend --locked pytest
npm --prefix frontend run build
npm --prefix frontend run lint
```

- `/health/live`: HTTP 200 means the Python process answers requests, even if the
  database is down.
- `/health/ready`: HTTP 200 means a real `SELECT 1` succeeded; HTTP 503 means the
  database is unavailable. It checks connectivity, not migration currency. Use
  `alembic current` to confirm `69e8baad5d8e (head)` separately.
- The dashboard should say **API and database ready** and show configured bounds.
  Refresh the page after changing server/database state; status is checked on load.
- To observe the difference, run `docker compose stop db`, check both health URLs,
  then run `docker compose up -d --wait` and refresh the browser.

Stop web servers with Ctrl+C. `docker compose down` stops PostgreSQL while keeping
its named data volume. Do not remove the volume if you need its data.

## Milestone 1 verification record

Passed: dependency resolution with `backend/uv.lock` and `frontend/package-lock.json`,
8 pytest tests, TypeScript/production build, frontend lint, Compose configuration
validation, Alembic offline SQL generation and revision discovery, live HTTP 200
for liveness and HTTP 503 for unavailable database, and browser rendering with
configuration loaded through Vite's proxy.

Completed remaining Milestone 1 verification on **2026-10-27, Asia/Manila
(UTC+08:00)** against a real PostgreSQL 17 container:

- Started Docker Desktop; `docker info` reported Engine 29.4.1.
- `docker compose up -d --wait` started a healthy database. Port 5432 was
  occupied, so the ignored local `.env` uses `POSTGRES_PORT=55432` and a matching
  `DATABASE_URL` port. The committed Compose configuration and lockfiles are unchanged.
- `alembic upgrade head` succeeded online; `alembic current` and a direct query
  of `alembic_version` both confirmed `0001_baseline` at head.
- The running backend returned HTTP 200 for `/health/live` and `/health/ready`;
  readiness returned `{"status":"ready","database":"ok"}`. The Vite proxy
  also returned readiness HTTP 200.
- Browser inspection confirmed the exact text **API and database ready** and
  an explicit Asia/Manila (UTC+08:00) check-time label.
- Created the isolated, disposable table
  `public.quakeph_m1_persistence_verification` with row
  `(1, 'milestone1-persistence-20261027')`. Ran
  `docker compose up -d --force-recreate --wait db`; the container ID changed
  from `455121069a7d…` to `5ae02e7bd09e…` while its mounted volume remained
  `quakeph_postgres_data`. The exact row survived. Dropped the verification
  table and confirmed `to_regclass(...) IS NULL` returned true.
- `SHOW timezone` returned `UTC`.
- Ran `docker compose stop db`: liveness remained HTTP 200 and readiness
  returned HTTP 503 with `{"status":"not_ready","database":"unavailable"}`.
  Restored PostgreSQL using `docker compose up -d --wait db`, confirmed healthy
  status, and rechecked both endpoints: HTTP 200. Alembic remained at head.

No remaining blockers for Milestone 1. No volume was deleted, and
`docker compose down -v` was never run. No application code or dependency
lockfiles changed during this verification. The original passing test run emitted
an installed Starlette/httpx TestClient deprecation warning; it was non-failing.

The supplied GitHub repository is configured as the local `origin` remote:
`https://github.com/ragna0112/QuakePH.git`. Local commits do not imply a push.

## Milestone 2: ingestion and storage

From the project root, preserving your existing private `.env` and port 55432:

```sh
.tools/bin/uv sync --directory backend --locked
docker compose up -d --wait
.tools/bin/uv run --directory backend --locked alembic upgrade head
.tools/bin/uv run --directory backend --locked alembic current
.tools/bin/uv run --directory backend --locked python -m app.ingestion
```

Run the ingestion command again whenever you want to refresh the rolling week.
It prints the run UUID, status, counters, and error details. Exit code 0 means
success; partial or failed runs exit 1. This is a manual command, not a scheduler.
`httpx` moved from the development group to runtime dependencies at its existing
locked version; no installed package versions were upgraded.

A USGS feature becomes a row like this: fetch the real weekly GeoJSON, validate
its envelope and features, read coordinates as **longitude, latitude, depth in
kilometers**, convert integer epoch milliseconds to UTC, filter valid earthquake
features to the inclusive configured box, and write them in one database
transaction. Null magnitude, place, magnitude type, review status, and URL are
allowed. Valid negative magnitude/depth are accepted. ID, times, event type,
and finite coordinates/depth are required. Unknown extra properties remain in
`raw_event`, which holds the latest accepted complete feature.

The USGS ID is the primary key. PostgreSQL `ON CONFLICT DO NOTHING` safely
handles concurrent inserts; a conditional UPDATE replaces content only when the
incoming source-updated time is strictly newer. Identical and older revisions
preserve content/raw JSON and first-seen time, while last-seen advances using the
greatest observed UTC time. Rows missing from a later rolling feed are retained.

### Inspect data

Open a PostgreSQL prompt without putting credentials in shell arguments:

```sh
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Then run:

```sql
SELECT usgs_id, event_time, magnitude, place, latitude, longitude, depth_km,
       source_updated_at, first_seen_at, last_seen_at
FROM earthquakes ORDER BY event_time DESC LIMIT 20;

-- Explicitly label local display time; stored timestamps remain UTC.
SELECT usgs_id, event_time AT TIME ZONE 'Asia/Manila' AS event_time_asia_manila
FROM earthquakes ORDER BY event_time DESC LIMIT 20;

SELECT id, started_at, ended_at, status, fetched, in_scope,
       inserted, updated, unchanged, rejected, out_of_scope, excluded_type,
       failed_request_attempts, errors
FROM pipeline_runs ORDER BY started_at DESC LIMIT 20;

SELECT usgs_id, count(*) FROM earthquakes
GROUP BY usgs_id HAVING count(*) > 1;
```

### Counters and failure behavior

- `fetched`: number of features in a validated response. Invalid responses fail;
  a valid FeatureCollection with matching count 0 succeeds with zero counts.
- `rejected`: malformed features, with reasons and feature indexes recorded in
  `errors` (first 100 record errors retained; counter still counts all).
- `excluded_type`: valid features whose type is not `earthquake`, regardless of
  location. Malformed non-earthquake features are counted as rejected first.
- `out_of_scope`: valid earthquake features outside the inclusive box.
- `in_scope`: valid in-box earthquakes visited by the processing loop.
- `inserted`, `updated`, `unchanged`: committed per-feature outcomes. Unchanged
  means existing content was retained, even though last-seen can advance. A feed
  containing repeated IDs can produce multiple outcomes for one stored row.
- `failed_request_attempts`: transport failures and non-success HTTP responses,
  including attempts recovered by a later retry. Each attempt is committed to
  the run independently, with attempt number, HTTP status, and exception type.

For completed success/partial processing,
`fetched = rejected + excluded_type + out_of_scope + in_scope`, and
`in_scope = inserted + updated + unchanged`. A run with any rejected features is
`partial`, never fully successful, but its valid rows commit. An HTTP, response,
or database failure marks the run `failed`; its earthquake transaction rolls
back and inserted/updated/unchanged are reset to zero. Fetched/in-scope/rejection
counts then describe only validated/visited input, not committed rows. Runs have
source URL, scope bounds and label, UTC start/end times, and error details.

HTTP uses a 10-second connect timeout and 30-second read/write/pool timeouts.
There are at most 3 attempts for transport errors, 408, 429, 500, 502, 503, and
504, with exponential backoff (1 then 2 seconds). Retry-After seconds or HTTP
dates override the delay, bounded to 0–30 seconds. Other HTTP errors are not
retried; malformed successful responses fail without retrying.

Tracking starts and finalizes in separate committed transactions so a data
rollback cannot erase failure history. If PostgreSQL itself cannot persist
tracking, the CLI exits 1 and explicitly reports that limitation. A killed
recent-feed process can still leave a run `running`; historical jobs have the
explicit lock-based recovery described below.
Concurrent transactions may fail on lock timeout/deadlock; PostgreSQL preserves
uniqueness/revision ordering and the run fails rather than claiming success.

### Backend verification

```sh
.tools/bin/uv run --directory backend --locked pytest --postgres
.tools/bin/uv run --directory backend --locked alembic check
```

`--postgres` tests use actual PostgreSQL with a unique `quakeph_test_<uuid>`
schema per test and remove only that schema afterward. The configured database
user needs CREATE SCHEMA permission. Tests never clear application tables or
modify real source rows. Without `--postgres`, integration tests are skipped.

Executed on **2026-10-28, Asia/Manila (UTC+08:00)**:

- All 48 backend tests passed with `pytest --postgres`, including 7 isolated
  PostgreSQL integration tests. The existing Starlette/httpx deprecation warning
  remains non-failing. Frontend production build and lint passed after correcting
  the shell text to reflect backend ingestion; no dashboard features were added.
- Online migration reached `1522abd3414e`; `alembic check` found no schema drift.
- Two real USGS imports succeeded, both with zero request failures or rejections:

| Run | Fetched | In scope | Inserted | Updated | Unchanged | Out of scope | Excluded type |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `b7021f95-7d86-4f6b-b6aa-e83ccb018530` | 1882 | 5 | 5 | 0 | 0 | 1836 | 41 |
| `98f49481-17cc-47ad-ae79-8570667cd87c` | 1882 | 5 | 0 | 0 | 5 | 1836 | 41 |

- Direct SQL confirmed 5 rows and 5 distinct USGS IDs; duplicate query returned
  no rows. No test schemas remained. Later requests may legitimately contain
  new events or revised records, so their counts can differ.
- Controlled PostgreSQL fixtures proved insert deduplication, concurrent writes,
  newer revision replacement, older/identical revision preservation, first/last
  seen behavior, partial/empty runs, retry accounting, and failed-run persistence
  after a rolled-back earthquake insert. Real source rows were not edited.

## Milestone 3: historical backfills with checkpoints

A **backfill** collects older earthquakes through the USGS catalog. A **checkpoint**
is a completed time window saved in PostgreSQL in the same transaction as its
accepted earthquake rows. **Idempotency** means reimporting those events retains
one row per USGS ID and applies only newer revisions. **Resume** means reopening
an existing job by UUID and continuing its unfinished windows.

### Commands

Run from the project root, keeping the private `.env` and PostgreSQL host port
**55432** unchanged. No new dependencies or lockfile changes are needed.

```sh
docker compose up -d --wait
.tools/bin/uv run --directory backend --locked alembic upgrade head
.tools/bin/uv run --directory backend --locked python -m app.historical import --start 2020-01-01 --end 2020-01-08 --window-days 1
```

The command prints and flushes the durable job ID before starting network work.
If interrupted or failed, replace the UUID below with that job ID:

```sh
.tools/bin/uv run --directory backend --locked python -m app.historical resume df688a1e-a05b-48a2-8e6d-dd9d05ac5ac7
```

This example job already succeeded on this machine, so resuming it simply reports
its existing completed progress without making requests. To refresh a completed
range against newer catalog data, run a **new import** for the same range.
Resume never re-fetches completed windows. It uses the saved geographic scope,
source, window boundaries, limit, ordering, event type, and timeout settings,
even if environment defaults have changed. It still connects to the currently
configured database, where the job must exist.

The recent-feed CLI remains unchanged:

```sh
.tools/bin/uv run --directory backend --locked python -m app.ingestion
```

Historical commands exit 0 only for a fully successful job. Failed/incomplete or
busy jobs exit 1; malformed dates, UUIDs, and invalid/reversed ranges exit 2.

### Dates, boundaries, and bounded requests

Inputs must be calendar dates in `YYYY-MM-DD`. Both dates mean **UTC midnight**,
not midnight in Asia/Manila. The internal range is **[start, end)**: include the
start, exclude the end. For example, `2020-01-01` to `2020-01-08` covers all seven
UTC days January 1–7 (January 1 08:00 through January 8 08:00 Asia/Manila, with the
latter instant excluded). Equal or reversed ranges are rejected before DB work.

The implementation follows the [official USGS catalog documentation](https://earthquake.usgs.gov/fdsnws/event/1/):

- Initial windows default to 7 days; `--window-days` accepts 1–31. Requests filter
  `eventtype=earthquake` and the saved bounding box, return GeoJSON, and request
  `orderby=time-asc`. Accepted records are also sorted locally by time, ID, and
  source revision for stable ordering of timestamp ties.
- USGS caps query results at **20,000**. A count request checks each window first.
  At or above that limit, the window is conservatively bisected in time instead
  of importing possibly truncated results. Split parents and their children are
  saved atomically; only children become event checkpoints.
- Query requests explicitly set `limit=20000`. A query returning that many
  features also triggers subdivision, covering growth after the count request.
  HTTP 400 triggers a fresh count; only confirmed saturation is treated as
  overflow. Other bad queries fail instead of generating endless subdivisions.
  There is no offset pagination.
- Splits use integer millisecond boundaries. A **1ms minimum window** still at
  the limit fails with an explicit completeness error. There is no silent
  truncation or spatial fallback. That job cannot finish until the source drops
  below the cap or a differently scoped new job can be used.
- USGS includes both time endpoints. Requests use those inclusive bounds, then
  the application filters each record to its window's [start, end). A feature at
  a shared endpoint may be fetched twice but is accepted in the later window
  only. The USGS ID primary key and existing revision-aware upserts also protect
  against duplicates. Nothing deletes rows absent from later responses.
- HTTP 204 and valid empty FeatureCollections complete an empty window.
  Catalog metadata may omit `count`; when present it must match the features
  list. Envelope/status checks remain mandatory. Recent-feed validation retains
  its original required-count behavior.

**The catalog can change between requests.** Counts and results are not a source
snapshot, and checkpointed windows are not automatically revisited on resume.
Source origin times, additions, and revisions can change during a backfill. A
new job can refresh a range, but cannot guarantee a point-in-time source snapshot.

### Progress, failures, and safe recovery

`import_jobs` stores the requested range, settings snapshot, execution count,
status, timestamps, and last error. `import_windows` stores the window tree,
status, attempt count, last-attempt timestamps, counters, and last error.
Each window attempt has a separate `pipeline_runs` row linked by
`import_window_id`, including errors from prior failed attempts.

- Window states are `pending`, `running`, `completed`, `failed`, or `split`.
  Split parents describe planning work, not extra completed time coverage.
  Completed progress is the completed leaf windows; all remaining leaves must
  complete before the job is successful.
- `executions` counts accepted job executions/resumes; resuming an already
  successful job leaves it unchanged. `window_attempts` counts processing tries,
  including failed, interrupted, and splitting tries. These are not event totals
  or the number of HTTP calls (a try can make count/query requests and retries).
- The final JSON's `committed_counters` sums **completed windows only**, not all
  attempts. `fetched` can include shared endpoint features; `outside_time` counts
  valid earthquakes filtered by the exclusive time boundary. The other counters
  retain Milestone 2 meanings. Planning parents store `catalog_count` capacity
  observations; these can become stale and are not imported event totals.
- `total_failed_request_attempts` sums HTTP/transport failures across **all**
  attempts, including failed attempts and split parents. Individual attempt
  errors retain URL, HTTP status, retry number, and exception type. Failed
  response validation is a window error, not a failed HTTP request.
- Historical validation is stricter than the recent feed's partial-run policy:
  any rejected record fails that window and rolls back all of its event writes.
  Rejection reasons/indexes (first 100) and full rejection counts persist in the
  failed attempt; inserted/updated/unchanged reset to zero. A new attempt on
  resume retries the entire window. Earlier completed windows remain committed.
- The shared HTTP policy still uses at most 3 attempts with bounded backoff and
  Retry-After handling, 10-second connect and 30-second other HTTP timeouts.
  Processing stops at the first failed window; later work remains pending.
- A PostgreSQL advisory transaction lock prevents another executor from running
  the same job. It is held on a dedicated connection and released on disconnect
  or process exit. Resume acquires this lock **before** treating earlier running
  windows/attempts as interrupted; it never assumes that a timestamp alone proves
  abandonment. A new ownership token is checked under a job-row lock in every
  write transaction, preventing an old worker from committing after a takeover.

If the database is unavailable, tracking itself may not be writable; the CLI
reports the job as incomplete rather than claiming success. Database detection
of a dead connection can take time; a busy job is not force-unlocked. Recent-feed
runs have no historical job lock and are not recovered by this command. There
is no scheduler, progress API, cancellation UI, or automatic resume daemon.

### Inspect jobs and attempts

Open the same `psql` prompt documented above, then run:

```sql
SELECT id, start_at, end_at, status, executions, scope, settings, last_error
FROM import_jobs ORDER BY created_at DESC;

SELECT job_id, status, count(*) AS windows, sum(attempts) AS window_attempts
FROM import_windows GROUP BY job_id, status ORDER BY job_id, status;

SELECT job_id, id, parent_id, start_at, end_at, status, attempts,
       started_at, ended_at, counters, last_error
FROM import_windows ORDER BY job_id, start_at, end_at;

SELECT w.job_id, r.import_window_id, r.id AS attempt_id, r.status,
       r.started_at, r.ended_at, r.fetched, r.inserted, r.updated, r.unchanged,
       r.rejected, r.failed_request_attempts, r.errors
FROM pipeline_runs r JOIN import_windows w ON w.id = r.import_window_id
ORDER BY r.started_at DESC;
```

### Milestone 3 verification

```sh
.tools/bin/uv run --directory backend --locked pytest --postgres
.tools/bin/uv run --directory backend --locked alembic check
```

Executed on **2026-10-28, Asia/Manila (UTC+08:00)**:

- Migration `69e8baad5d8e` applied online; Alembic found no schema drift.
- 72 backend tests passed, including 23 PostgreSQL test cases in disposable
  schemas. Tests covered UTC dates, exact start/end events, adjacent-window
  deduplication, preflight and query-time overflow, minimum-window failure,
  empty responses, earlier checkpoints surviving failure, resume with changed
  defaults, interrupted-run recovery, same-job lock contention, stale-owner
  fencing, atomic checkpoint rollback, rejected-record persistence, and reimports.
  The existing Starlette/httpx deprecation warning remains non-failing.
- Live import used **[2020-01-01T00:00:00Z, 2020-01-08T00:00:00Z)**, with seven
  daily windows. Its first attempt exposed a legitimate empty catalog response
  without `metadata.count`; it failed safely without a checkpoint. After fixing
  catalog validation and adding a regression test, the same job resumed.
  That failed attempt is retained in pipeline history (no fabricated outage).

| Job | Executions | Window attempts | Completed windows | Fetched | Inserted | Updated | Unchanged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `df688a1e-a05b-48a2-8e6d-dd9d05ac5ac7` | 2 | 8 | 7 | 12 | 12 | 0 | 0 |
| `98214226-b4ad-4fd9-8e96-dbd46d947345` | 1 | 7 | 7 | 12 | 0 | 0 | 12 |

Both jobs finished successfully, with no pending/failed windows, rejected records,
or failed HTTP attempts. SQL confirmed 12 rows in that historical interval and
17 total stored rows with 17 distinct USGS IDs. The duplicate-ID query returned
no rows; no test schemas remained. Controlled failures used test transports and
isolated PostgreSQL data, not deliberate USGS disruptions or edits to real rows.
Both dependency lockfiles and the PostgreSQL configuration are unchanged.

## References and next steps

Official setup references: [FastAPI](https://fastapi.tiangolo.com/deployment/manually/),
[Vite](https://vite.dev/guide/), [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[PostgreSQL image](https://hub.docker.com/_/postgres).
USGS sources: [USGS catalog documentation](https://earthquake.usgs.gov/fdsnws/event/1/)
and [recent all-week GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson).

Milestone 3 stops at resumable historical imports. Scheduling, dashboard data
endpoints, and Leaflet map features remain for later milestones.
Nothing has been pushed or deployed.
