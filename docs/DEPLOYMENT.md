# Local deployment preparation: Vercel + Neon + GitHub Actions

Prepared November 6, 2026. Deployment, migrations, and ingestion remain gated. Create
**new QuakePH projects**; do not reuse CineMetrics/PanahonPH projects, databases,
credentials, Vercel project IDs, or workflows. Their settings were not inspected
or modified. Public hosting remains unverified until the checklist below passes.

## Compatibility and free limits

QuakePH is **Vite/React + FastAPI**, not Next.js. Current Vercel
[Services](https://vercel.com/docs/services) supports mixed services in one project
on all plans (Beta). Use the current `services` model, not older
`experimentalServices`, `builds`, or Python `/api` file-builder recipes.
[Services billing](https://vercel.com/docs/services/pricing) follows Functions
compute. Hobby is for personal/noncommercial projects; it is suitable for this
student portfolio, subject to usage and Beta availability in your account.

Checked official limits on November 6, 2026:

| Provider | Relevant free allowance / constraint |
| --- | --- |
| [Vercel Hobby](https://vercel.com/docs/plans/hobby) | 4 active CPU-hours, 360 provisioned-memory GB-hours, 1 million function invocations; 100 GB fast transfer and 10 GB origin transfer |
| [Vercel Functions](https://vercel.com/docs/functions/limitations) | Hobby 2 GB RAM; Fluid Compute maximum 300 seconds; Python bundle maximum 500 MB uncompressed. This API is configured for 30 seconds. |
| [Neon Free](https://neon.com/pricing) | 100 CU-hours/month/project and 1 GB storage/project; scale-to-zero can cause cold starts |
| [GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions) | Standard public-repository runners are free; private GitHub Free includes $12/month shared compute usage, not a guaranteed number of free jobs |

These are allowances, not a promise the application will always remain free.
Check account usage/budgets, including other projects sharing the same account.
Do not enable paid overages or upgrade plans as part of this checklist. Recheck
provider limits before release; pricing and Beta behavior can change.

There is no continuously running process on Vercel. FastAPI only serves reads.
GitHub Actions executes a bounded recent-feed import, then exits. The existing
local `app.worker`, historical imports, and reconciliation commands remain usable
with a direct database connection; they are not started during deployment.

## 1. Create a separate Neon target

In Neon, create a **new project named QuakePH**, PostgreSQL 17, and a database
named **`quakeph`**. Choose a region close to the Vercel function region (for
example AWS Singapore and Vercel Singapore `sin1`, if available in both accounts).
Do not select another project's branch or copy another project's credentials.

Copy two TLS connection strings for that same new database/branch:

- **Pooled** (`-pooler` hostname): Vercel API, short transactions.
- **Direct** (no `-pooler`): migrations, Actions imports, manual backfills,
  reconciliation, or local worker. Session advisory locks need direct connections.

Neon `postgresql://` strings are normalized to the installed psycopg 3 driver;
`postgresql+psycopg://` also works. Preserve URL-encoded credentials and
`sslmode=require` (or `verify-full`) and any supplied channel-binding parameters.
Never paste either URL into source, workflow inputs, a command argument, or chat.
Store them through provider secret/environment settings.

[Neon's pooling guide](https://neon.com/docs/connect/connection-pooling) recommends
pooled connections for serverless and direct ones for migrations/session features.
`DATABASE_POOL_MODE=null` uses SQLAlchemy NullPool, closes client connections after
checkout, disables connection-local automatic prepared statements, and applies
UTC/statement timeout **within each transaction**. No session startup settings
are sent to the transaction pooler. Local queue pooling remains the default.

A separate hosted database starts empty. **The existing local data/volume is
preserved; it is not automatically copied, deleted, or synchronized.** Initial
weekly ingestion does not reproduce older local history. To reproduce older source
data, run a bounded historical import with its own direct Neon environment after
initial verification. If exact existing local history/run records are needed,
plan a separately reviewed PostgreSQL 17 backup/restore into the empty new target;
this preparation does not export, transfer, or overwrite those records.

## 2. Exact Vercel settings

Create a new Vercel Hobby project for the QuakePH repository only:

| Setting | Value |
| --- | --- |
| Project name | `quakeph` (or another available QuakePH-specific name) |
| Root Directory | Repository root (`.`); **not** `frontend` or `backend` |
| Framework Preset | **Services** |
| Node.js version | **24.x** |
| Python | **3.12**, selected by `backend/.python-version` |
| Fluid Compute | Enabled |
| Function region | Closest available to your new Neon project |
| Root build/install/output overrides | Unset; service-specific settings come from `vercel.json` |
| Build-time database migrations/imports | None |
| Vercel cron / continuous worker | None |

The committed service definitions explicitly select:

- `frontend`: root `frontend`, framework `vite`, install `npm ci`, build
  `npm run build`, output **`dist`**. The output must include `dist/index.html`
  and `dist/assets/*`; serving the repository directory causes a homepage 404.
- `backend`: root `backend`, framework `fastapi`, entrypoint **`app.main:app`**.
  `backend/pyproject.toml` declares FastAPI and the other runtime dependencies;
  `backend/uv.lock` fixes versions. Vercel's
  [Python runtime](https://vercel.com/docs/functions/runtimes/python) discovers
  manifests/lockfiles within this service root. The backend build command imports that ASGI app after dependency installation,
  failing the build if FastAPI/imports are missing without connecting to the DB.
  Do not substitute the CLI-only
  `app/hosted.py` as the HTTP entrypoint or install Python packages in `frontend`.

[Routing](https://vercel.com/docs/services/routing) keeps the original request
path: `/api/earthquakes` reaches FastAPI as `/api/earthquakes`. API/health/docs
rules precede the frontend catch-all. Root `/` and static assets go to the Vite
service. Do not strip `/api` or set `VITE_API_BASE_URL=/api` (that doubles the prefix).
No Next.js configuration or legacy `@vercel/static-build`/`@vercel/python` builds
array is needed for Services.

### Vercel Production environment variables

| Name | Value / location |
| --- | --- |
| `DATABASE_URL` | **Secret pooled Neon URL** for the new `quakeph` database; backend runtime, never `VITE_` |
| `DATABASE_POOL_MODE` | `null` (literal string) |
| `DATABASE_CONNECT_TIMEOUT` | `10` seconds to allow Neon cold starts |
| `MIN_LATITUDE`, `MAX_LATITUDE` | `4`, `22` |
| `MIN_LONGITUDE`, `MAX_LONGITUDE` | `116`, `130` |
| `FRONTEND_ORIGINS` | `[]` for same-origin deployment; JSON array of exact URLs only if direct cross-origin API access is required |
| `VITE_API_BASE_URL` | **Unset or empty**; frontend already calls same-origin `/api/...` |
| `VITE_TILE_URL`, `VITE_TILE_ATTRIBUTION` | Optional public provider settings; defaults retain OpenStreetMap attribution |

No `POSTGRES_*`, `WORKER_*`, GitHub token, direct database URL, or Vercel token is
needed in the browser. Vercel project environments are shared across Services;
Vite exposes only `VITE_*` values. Never rename database secrets to that prefix.
Do not put production credentials into local frontend env files.
For Preview, use a separate QuakePH Neon branch/database and corresponding
pooled URL, or omit the DB secret and expect readiness/API failures. Never inject
production credentials into untrusted fork/PR previews. Rebuild/redeploy after
changing Vite environment values.

The API can use a dedicated read-only Neon role (SELECT on app tables, schema
USAGE). Keep the direct migration/import owner separate. Do not grant write access
to the API simply to work around a missing migration.

## 3. Exact GitHub settings and manual operations

Create the GitHub Environment **`quakeph-production`**, restricted to the trusted
`main` branch. Add its secret:

| GitHub name | Kind | Value |
| --- | --- | --- |
| `QUAKEPH_DATABASE_URL_DIRECT` | Environment secret | Direct TLS URL for the dedicated `quakeph` database; migration/import role |
| `QUAKEPH_SCHEDULE_ENABLED` | **Repository variable** | Leave absent or `false` until all verification passes |

No Neon API key, Vercel token, or GitHub personal token is required. Built-in
`GITHUB_TOKEN` has `contents: read`; checkout does not retain it. Actions are pinned
to verified upstream commit SHAs; Python installs use uv 0.13.6, Python 3.12 and the
committed lockfile. All three workflows share one non-cancelling concurrency
group. There are no push/PR triggers, arbitrary shell inputs, or secret artifacts.

After these changes are pushed with user authorization, use Actions:

1. **QuakePH — migrate database**: Run workflow on `main`, type `quakeph` in the
   confirmation input. It runs `python -m app.hosted migrate`, applying
   `alembic upgrade head` and checking schema drift. Inspect success before any import.
2. **QuakePH — initial or manual recent ingestion**: dispatch separately on `main`,
   confirmation `quakeph`. It runs `python -m app.hosted recent`, verifies migration
   head, then reuses existing recent-feed validation/upserts/tracking. Inspect run
   UUID, status, fetched/in-scope/inserted/updated/unchanged/rejected/failure counts.
3. Run the ingestion operation again. Check unique IDs and allow legitimate new
   events/revisions. Both migrations and ingestion must succeed, not merely start.
4. Deploy only when separately authorized, then complete the public tests below.

The cloud command rejects local hosts, pooled URLs, other database names, and
non-TLS URLs. It takes the same session advisory lock as the scheduled local
worker and logs only safe IDs/status/counters or an error class. It never prints
connection URLs, exception strings, or raw tracebacks. Keep errors in pipeline
tracking for diagnosis. A wrong Neon **project** cannot be inferred from a DB name:
verify the selected new project/branch in the console before adding the secret.

The equivalent manual commands, with a safely supplied process environment, are:

```sh
.tools/bin/uv run --directory backend --locked --no-dev python -m app.hosted migrate
.tools/bin/uv run --directory backend --locked --no-dev python -m app.hosted recent
```

Do not run these against the root local `.env`; the cloud-target guard deliberately
rejects that connection. These commands do not create Neon projects or databases.

### Enable a conservative schedule only after verification

`.github/workflows/quakeph-ingest-scheduled.yml` has **no active schedule trigger**.
Its proposed cron is commented out and its job additionally requires
`QUAKEPH_SCHEDULE_ENABLED=true`. To activate later, deliberately uncomment:

```yaml
schedule:
  - cron: '17 */6 * * *'
```

Then set the repository variable to `true`. This runs at **00:17, 06:17, 12:17,
18:17 UTC** (08:17, 14:17, 20:17, 02:17 PHT), roughly **120 jobs/30 days**. At an
illustrative two minutes/job, that is 240 Linux runner minutes, about $1.44 at the
currently listed $0.006/minute private-repository rate; actual duration and shared
account allowance decide cost. Standard public runners remain free. Each job has
a ten-minute cap. No artifacts/cache are uploaded by these workflows.

This cadence favors free quotas over near-real-time updates. The rolling week
feed supports overlap and deduplication; a delay exceeding seven days needs a
historical backfill. [GitHub schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) can be delayed/dropped and public inactive
repositories can have schedules disabled. Monitor Actions and provider quotas;
collection is not an alert service. Disable the variable to stop scheduled DB jobs.
There is no automatic cloud reconciliation/backfill schedule in this preparation.

One-shot successes now appear in the dashboard's collection timestamps even with
no persistent worker. Worker status and local schedule fields still describe only
`app.worker`; they do not prove GitHub runner liveness or represent its cron.
Use Actions for external schedule monitoring. A runner killed mid-import can leave
a recorded running attempt; do not infer activity from it. Subsequent upserts stay
idempotent, but manual recent attempts do not have historical checkpoint recovery.

## 4. Public verification checklist (not executed locally)

Replace `<domain>` with Vercel's actual assigned domain; no public URL is claimed:

- `https://<domain>/`: dashboard HTML (not FastAPI JSON or a 404); `assets/*` loads.
- `https://<domain>/health/live`: 200; `/health/ready`: 200 with Neon available.
- `https://<domain>/api/config`: correct coverage/box and Asia/Manila.
- `https://<domain>/api/earthquakes`, `/api/earthquakes/map`, `/api/stats`: JSON with
  actual stored source data. Unknown `/api/...` stays an API 404, not frontend HTML.
- `https://<domain>/docs` and `/openapi.json`: working API schema, not frontend fallback.
- Root query links: shared filters, inclusive Philippine end date, reload/back/forward,
  sorting/pagination, marker/details/USGS links, chart bounds, mobile/keyboard access.
- Refresh view: only GETs; no new ingestion attempts. Collection time advances only
  after a successful Actions import. A stopped/never-observed worker is expected.
- Cold-start readiness/API calls after Neon sleeps; browser console/network;
  OpenStreetMap tiles and visible attribution. OSM policy remains applicable.
- Vercel build output: runtime FastAPI present, function under 500 MB, `dist/index.html`
  published by the frontend service. Check bundle/logs without exposing secrets.
- Inspect Neon pipeline runs, row IDs, attempts and errors; repeat import has no duplicate
  IDs. Verify quotas before enabling the cron and variable.

Everything durable lives in PostgreSQL: raw JSON, runs, windows, locks/checkpoints,
revision history, schedules. There is **no persistent filesystem requirement**.
Vercel/GitHub filesystems are disposable; no SQLite, local checkpoint file, upload
store, or application log file is used. Do not depend on `/tmp` for saved progress.
Local PostgreSQL keeps its existing Compose volume and host port **55432**.


## Local verification record — November 6, 2026

- **150 backend tests passed** with `pytest --postgres`, including transaction-local
  UTC/timeout settings with NullPool, hosted target restrictions, redacted errors,
  migration-before-ingestion guard, partial-run exit codes, one-shot freshness,
  revision/deduplication, checkpoints, concurrency, and API routing contracts.
  The existing Starlette/httpx TestClient deprecation warning is non-failing.
- **30 frontend tests passed** in both default and America/New_York timezones;
  type checking, lint, and production build passed. `dist/index.html` and hashed
  assets exist; scans found no database URLs, Neon hosts, or server-secret names
  in browser output. Fixtures remain restricted to tests.
- Current `vercel.json` was checked against published Vercel schema constraints
  with a Draft 7 validator. Workflow YAML and main-branch/manual-only/secret/
  concurrency/timeout gates were checked locally. Upstream action SHAs were
  resolved from their official repositories. `actionlint` download timed out, so
  that executable's validation is **not** claimed.
- A clean isolated `uv sync --locked --no-dev --offline` installed **30 runtime
  packages**, including FastAPI 0.162.0. The configured HTTP module imported and
  `/health/live` and `/api/config` returned 200 through TestClient. The exact
  backend build-time import check passed. The local macOS runtime installation
  measured **57 MiB**; this is **not** the final Vercel/Linux bundle size.
- Linux x86_64 wheel installation was attempted, but downloads stalled/retried
  and the attempt was stopped. Linux native-wheel resolution, Vercel's actual
  dependency bundle/output/routing, and the Hobby account's Services selector
  remain **unverified** until a separately authorized hosted build.
- PostgreSQL was stopped at the first attempt; database tests could not connect.
  The existing Compose database was started with its existing volume, then the
  complete database suite and Alembic drift check passed. Final application rows
  remain **51 earthquakes / 45 pipeline runs**, with **0 leftover test schemas**.
  No live import, new source revision, Neon migration, or hosted request was fabricated.
- Both dependency lockfiles remain unchanged. Temporary isolated installations
  were removed. PostgreSQL remains running locally on **55432**; no API/frontend
  server or collection worker was started for this preparation.

During local preparation, no cloud project/database was created, no secret was set
remotely, no workflow was dispatched, and no push/deploy was performed. The user
subsequently reported creating the `quakeph` Neon database and configuring the
`quakeph-production` environment secret; their values were not read. Scheduled
collection remains disabled. CineMetrics and PanahonPH were not modified.


## Authorized repository handoff

The intended hosting configuration, tests, and guide are prepared for the
user-authorized commit/push to default branch `main`. No migration or ingestion
workflow is dispatched as part of that push. Scheduled ingestion retains both
its commented-out cron and its disabled-by-default repository-variable guard.

`vercel.json` sets `git.deploymentEnabled=false` to prevent this push from starting
a Git-triggered Vercel deployment. This follows the
[official Git configuration](https://vercel.com/docs/project-configuration/git-configuration#gitdeploymentenabled).
Keep it disabled until deployment is explicitly authorized; manual deployment
commands can still deploy and are not run here. Enable automatic deployments only
when intentionally requested later.

Migration workflow: **QuakePH — migrate database**
(`.github/workflows/quakeph-migrate.yml`). Select branch **main** and provide
required string input **confirmation = quakeph**. It uses environment
**quakeph-production**, secret **QUAKEPH_DATABASE_URL_DIRECT**. Creating the database
and adding that secret do not themselves apply migrations.

## Diagnose a rejected migration target safely

At commit `7ab0282`, `validate_target()` used one plain `ValueError` for four
conditions: host missing/not ending in `.neon.tech`, hostname containing
`-pooler`, database name not exactly `quakeph`, or `sslmode` not exactly `require`
or `verify-full`. The old log does not identify which condition failed. URL
parsing or later migration code can also raise a ValueError; do not infer that
any migration completed from the exception class alone.

Verify the GitHub Environment `quakeph-production` secret
`QUAKEPH_DATABASE_URL_DIRECT` contains just the direct URL (not a `psql` command,
quoted shell expression, project name, or pooled connection). In Neon's Connect
panel choose the correct separate QuakePH project/branch, database **quakeph**,
and connection pooling **off**, and preserve its TLS query parameters. A Neon
project named QuakePH does not establish that the connection's database path is
`/quakeph`; the default database may still be `neondb`. Do not paste the URL into
logs/chat or change the guard to accept another database.

New diagnostics print a stage plus fixed failed-check identifiers only:

| `failed_checks` item | Required correction/check |
| --- | --- |
| `database_url_present` | The named environment secret must exist and be nonempty. |
| `database_url_parseable` | Store only a valid connection URL; encode special password characters. |
| `neon_hostname` | Use a Neon hostname ending `.neon.tech`. |
| `direct_connection` | Copy the direct endpoint; hostname must not contain `-pooler`. |
| `database_name_quakeph` | Select the actual database `quakeph`; creating/renaming a project is insufficient. |
| `tls_sslmode` | Keep `sslmode=require` or `sslmode=verify-full` in the URL query. |

`python -m app.hosted check` validates settings/target only and returns before
creating an engine, connecting, acquiring a lock, or changing schema/data. The
migration workflow runs this preflight first and exits before migration if it
fails. Later failures identify stages such as `database_lock`, `migration_upgrade`,
or `schema_check`, without printing exception messages/URLs. Rerunning the full
migration workflow after preflight succeeds will still apply migrations; do that
only when authorized and ready. This diagnostic change itself runs no migration.
