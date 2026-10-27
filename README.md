# QuakePH — Milestones 1–2

A student data engineering project covering **Philippines and surrounding region**
(4–22° latitude, 116–130° longitude). This configurable rectangle includes nearby
waters; it is not an exact national boundary.

The foundation and real USGS weekly ingestion are implemented. Earthquakes and
pipeline runs persist in PostgreSQL. The frontend is still a status shell; maps,
search, historical imports, scheduling, and dashboard data APIs are not implemented.
No fixtures are inserted into the running application.

## How the pieces fit

- `backend/app/`: FastAPI serves HTTP endpoints; SQLAlchemy connects to PostgreSQL.
- `backend/alembic/`: Alembic tracks database schema versions. The baseline revision
  starts with a baseline, followed by earthquake storage and pipeline run tables.
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
  `alembic current` to confirm `1522abd3414e (head)` separately.
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
process can leave a run `running`; automatic recovery is not implemented.
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

## References and next steps

Official setup references: [FastAPI](https://fastapi.tiangolo.com/deployment/manually/),
[Vite](https://vite.dev/guide/), [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[PostgreSQL image](https://hub.docker.com/_/postgres).
USGS sources: [USGS catalog documentation](https://earthquake.usgs.gov/fdsnws/event/1/)
and [recent all-week GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson).

Milestone 2 stops at ingestion and storage. Historical imports, scheduling,
dashboard data endpoints, and Leaflet map features remain for later milestones.
Nothing has been pushed or deployed.
