# Final verification — Milestone 7

Executed **November 5, 2026**, Asia/Manila (UTC+08:00), from Milestone 6 commit
`257a706`. Times below are explicitly UTC unless labelled PHT. This record
separates checks rerun now from earlier evidence. No push or deployment occurred.

## Initial state and protection

Before starting anything, `lsof`, `docker info`, and `docker compose ps` found:

- Existing API: PID **18490**, loopback **8001**.
- Existing Vite development server: PID **19005**, loopback **5174**.
- Existing `quakeph-db-1`: healthy PostgreSQL on loopback **55432**.
- Worker stopped; no worker process was found/left active by Milestone 6.
- Git working tree clean. Existing application data: **50 events, 42 runs**
  (Milestone 6 handoff; Milestone 7 reads confirmed 51/45 after three imports).

The existing services were preserved. No application table was cleared, real
source row edited, volume deleted, or unrelated application interrupted.

## Reproducible installation and schema (executed now)

Observed versions: Python **3.12.16**, uv **0.13.6**, Node **25.9.0**, npm **11.12.1**,
Docker Engine **29.4.1**, PostgreSQL **17.11**. Backend supports Python >=3.12,<3.14;
its version file selects 3.12. Frontend locked dependencies require Node 22.13+
in the 22 line or >=24 (24 LTS recommended). System Python 3.9 can bootstrap uv.

- Copied committed backend `pyproject.toml`, `uv.lock`, `.python-version` into a
  fresh OS temporary directory. `uv sync --locked` installed **35 packages** into
  its separate `.venv` with no lockfile update.
- Copied committed frontend manifests into a separate temporary directory.
  `npm ci` **from that directory** installed **115 packages**, audited 116,
  reported **0 vulnerabilities**. Copied frontend source/config there and ran
  its production build successfully. Temporary files were removed after checks.
- `npm ci --prefix /temporary/path/frontend` from the repository root initially
  reported a missing `frontend@0.0.0` lock entry. Running `npm ci` with the actual
  project directory as cwd succeeded with the exact same lockfile. The final
  README uses `(cd frontend && npm ci)`. No dependency regeneration was needed.
- Created a unique disposable `quakeph_m7_<uuid>` **database** on the existing
  PostgreSQL server. Using the isolated Python install and a process-only
  `DATABASE_URL` override, `alembic upgrade head` applied the full chain to
  `74ef76e7aa5e`; `alembic current` confirmed head; `alembic check` reported
  **No new upgrade operations detected**. Removed only that disposable database.
- A second disposable database plus the isolated Python environment verified
  actual API startup: `/health/live`, `/health/ready`, `/api/earthquakes` (total 0),
  and `/api/pipeline/status` returned **200**. Stopped that temporary API and
  dropped its database. This distinguishes fresh setup from already-running API.
- Application database `alembic check` also found **no schema drift**. Existing
  data/volume stayed intact. Existing live/readiness URLs also returned **200**.

## Regression results (executed now)

| Check | Result |
| --- | --- |
| `uv run --directory backend --locked pytest --postgres` | **135 passed**, 1 non-failing deprecation warning |
| PostgreSQL integration isolation | Unique test schemas; **0** left afterward |
| Frontend Vitest | **29 passed** |
| `TZ=America/New_York vitest run` | **29 passed** |
| TypeScript project check | Passed |
| oxlint | Passed |
| Vite production build | Passed, including isolated clean-install build |
| Alembic fresh/app schema checks | Passed |
| `git diff --check` | Passed |

The warning is Starlette's existing TestClient/httpx deprecation. Dependency
versions were preserved. Tests use controlled transport/record fixtures only in
test modules. All PostgreSQL-specific tests actually ran with `--postgres`.

Historical tests rerun include date boundaries, neighboring-window deduplication,
oversize splitting, minimum-window overflow, empty responses, earlier checkpoints
surviving a later failure, resume with saved settings, interrupted-owner recovery,
contention/fencing, and atomic checkpoint rollback. Ingestion/worker tests rerun
include concurrent inserts, newer/older/identical revisions, revision-history
atomicity, request failures, recorded errors, scheduled ownership, and SIGTERM.

## Live data flow (executed now)

Two `python -m app.ingestion` commands and one due scheduled recent task fetched
real USGS weekly data. Every run succeeded with **0 rejected records and 0 failed
request attempts**. Each fetched **1,936** features: **6 in scope**, **1,888 out
of scope**, **42 excluded non-earthquake types**.

| Run UUID | UTC start → end | Inserted | Updated | Unchanged |
| --- | --- | ---: | ---: | ---: |
| `cd11c0f9-08e6-4d42-8a05-1f72b6b327d0` | 12:07:49.964938 → 12:07:51.127559 | 1 | 0 | 5 |
| `94b63e86-22b5-4db2-9257-66abdadef949` | 12:08:04.385002 → 12:08:06.886589 | 0 | 0 | 6 |
| `820c3478-8aac-4d5c-80b6-d6e924a7de20` (scheduled) | 12:08:05.462930 → 12:08:06.932692 | 0 | 0 | 6 |

The repeat/manual and scheduled requests overlapped briefly; IDs remained unique.
Final SQL: **51 rows / 51 distinct USGS IDs**, **45 pipeline runs**.
No live revision appeared; controlled PostgreSQL tests establish revision handling.

New real event **`us7000tvwj`**, M5.0, 207 km ESE of Sarangani, Philippines:

- Origin: `2026-11-04T07:40:12.288Z` = Nov 4, **3:40 PM PHT**.
- Source updated: `2026-11-04T08:00:21.040Z`.
- First collected: `2026-11-05T12:07:51.050228Z`.
- Last seen after scheduled task: `2026-11-05T12:08:06.837501Z`.
- `/api/earthquakes/us7000tvwj` exposed it; production preview rendered its marker,
  table row, summary, detail times, and actual USGS source link.
- Opened the link's actual destination in the browser; USGS rendered
  **M5.0 – 207 km ESE of Sarangani, Philippines** at
  `https://earthquake.usgs.gov/earthquakes/eventpage/us7000tvwj/executive`.

Worker command: `python -m app.worker --max-tasks 1` via the documented uv setup.
Worker **`4ce9bf40-56d9-4efb-8aaf-da3c09780128`**, PID **21321**, started
`12:08:05.411084Z`, executed the already-due scheduled recent task, then reached
its safe completion boundary and exited **0**. Its persisted status is **stopped**,
`stopped_at=12:08:06.947277Z`; PID no longer exists. No verification worker remains.
Live shutdown used the bounded normal-exit option; SIGTERM behavior was verified
by the controlled PostgreSQL test, not a second live upstream disruption.

### Earlier evidence reused (not rerun live)

Milestone 3, October 28: actual historical range
**[2020-01-01T00:00:00Z, 2020-01-08T00:00:00Z)** yielded **12 events** across seven
completed daily windows. Job `df688a1e-a05b-48a2-8e6d-dd9d05ac5ac7` resumed after a
real optional-metadata validation failure and completed (2 executions, 8 attempts).
Reimport job `98214226-b4ad-4fd9-8e96-dbd46d947345` kept all **12 unchanged**.
Milestone 4 verified bounded real reconciliation and three due worker tasks.
Milestone 1 verified data persistence after container recreation and readiness
failure vs continuing liveness during a database outage. Those disruptive checks
were not repeated against the running application in Milestone 7. Full records
remain in [MILESTONE_HISTORY.md](MILESTONE_HISTORY.md).

## Production-preview browser checks (executed now)

Built frontend served at **4174** against real API **8001**, while existing Vite
**5174** stayed running. Preview inherits `server.proxy` (official Vite docs checked;
the former README assertion that preview had no proxy was corrected).

- Desktop **1440×900**, mobile **390×844**: no page-wide horizontal overflow.
  Screenshots in `docs/screenshots/final-dashboard-{desktop,mobile}.png` use real data.
- Default Oct 30–Nov 5: **6 matching records/markers**, strongest/newest M5.0
  Sarangani. Expanding start to Oct 4: **39 matching records**, four list pages.
- Applied date changes, sorted by magnitude, changed to page 2, browser back to
  page 1, forward to page 2, then reloaded; state restored from URL.
- Mobile Nov 4–Nov 4 filter: **1 matching record** across table/map/statistics,
  displaying Nov 4 **3:40 PM PHT**. API checks with UTC boundaries
  `2026-11-03T16:00:00Z` to `2026-11-04T16:00:00Z` returned total **1**;
  the next Manila day returned **0**. Exact-at-boundary cases are controlled tests.
- Long UI range Jan 1, 2020–Nov 5, 2026: total **51**; chart showed
  **Nov 5, 2025–Nov 5, 2026** and explicitly stated **12 matching events fall
  outside this chart's date range; summary statistics still include them**.
  Expanded the exact-values table.
- Empty place `zzzz-no-stored-match`: **0**, dates unchanged. Reset restored defaults.
- Keyboard Enter opened real map popup; magnitude/place/depth/PHT time/details
  were shown. Fit results, Reset view, zoom, and Refresh view were exercised.
  Refresh retained zoom (tile URLs remained at zoom 6).
- Detail keyboard focus stayed inside on Shift+Tab; Escape returned focus to
  popup opener. Mobile detail width **356px** in a **390px** page; Escape restored
  the table opener. Actual source destination rendered successfully as above.
- Pipeline showed stopped worker, successful/attempted recent times, reconciliation,
  scheduled next times, counters/errors. Attempt pagination reached page 2 and back.
  Refresh read stored data without starting a worker.
- Browser console: **no warnings/errors** captured. Visible map tile images loaded
  with **0 failed images**. Real API responses rendered successfully; no full
  browser network trace capability was available.
- Actual stored data has fewer than the UI's 1,000 map limit: UI truncation wording
  was verified with controlled fixtures, **not** claimed as a live truncated map.
  A real API request `map?limit=1` returned **1 of 51, truncated=true**.
- Obsolete requests, same-filter cached errors, partial panel failures and tile
  failures were exercised by frontend tests; browser-level offline interception
  was unavailable. No production fixtures or deliberate upstream failures used.

Before/after the final API/browser read-only sequence, SQL counts remained
**51 events / 45 runs / 3 worker records**. No worker was running during it.
This supports the check that dashboard reads do not initiate ingestion.

## Repository review (executed now)

Tracked-file review found no real `.env`, private keys/token patterns, installed
dependencies, build output, database files, production imports of test fixtures,
machine-specific absolute paths in runtime code, or Vite/React starter branding.
Credential-pattern matches were only the explicit local example in `.env.example`
and a test-only credential projection fixture in `backend/tests/test_api.py`.
No real credentials were printed or added. Screenshot files contain the public
dashboard and real public USGS data. Both dependency lockfiles remain unchanged.
The former long README is archived; its milestone-era statements are historical.
Final screenshot review found clipped mobile depth units. A scoped CSS change
widened the depth column and allowed wrapping; type checking, lint, and build
passed again. Mobile widths 390 and 320 were rechecked after the fix.

## Scope and process handoff

Unverified: physical devices, screen-reader software, public hosting, full browser
network trace, actual browser timezone switching (New York timezone was executed
in Vitest), and live UI map truncation at >1,000 events. These are not claimed passes.
No blocking failure remains for the local portfolio setup. A deprecation warning
and source/tile availability limitations remain; no completeness, snapshot, or
uptime guarantee is made.

Preserved: original API PID **18490** on **8001**, Vite PID **19005** on **5174**,
and healthy PostgreSQL **55432**. The temporary **4174 preview** was stopped after
checks, both temporary migration databases were dropped, isolated installs were
removed, and the verification worker exited. Browser viewport overrides reset.
No push/deployment. Milestone 7 ends with documentation and verification only.
