# Follow one earthquake through QuakePH

QuakePH stores real USGS data before displaying it. The browser reads our database
through our API; opening the dashboard does not ask USGS to import events.

## One event becomes a map marker

On November 5, 2026, the real recent-feed import first collected **`us7000tvwj`**,
M5.0, 207 km ESE of Sarangani, Philippines. Its event time was
`2026-11-04T07:40:12.288Z` (November 4, 3:40 PM Asia/Manila), with depth 49.074 km.
This is a documented observation, not a fixture; future USGS revisions can change it.

1. `backend/app/ingestion.py` requests the weekly GeoJSON with httpx. It validates
   the response envelope before `normalize()` reads each feature. Coordinates
   mean longitude, latitude, depth; millisecond times become UTC datetimes.
2. Valid earthquake features inside the configured box reach `upsert_event()`.
   `backend/app/models.py` defines their SQLAlchemy table. A database transaction
   writes the accepted event and its run counters in PostgreSQL.
3. `backend/app/api.py` queries stored rows. `backend/app/main.py` registers its
   read-only routes. FastAPI validates filters and serializes typed responses
   defined in `backend/app/api_models.py`.
4. `frontend/src/filters.ts` converts applied Philippine dates to UTC instants.
   `frontend/src/App.tsx` requests list, map, statistics, and pipeline information.
   `frontend/src/data.ts` handles independent requests, cancellation, and cache.
5. `frontend/src/components/MapPanel.tsx` draws the GeoJSON coordinate with Leaflet.
   Magnitude controls marker size/color; a popup links to stored details and the
   USGS source. `Details.tsx` displays event/source/collection times and revisions.

Python handles collection and server logic. PostgreSQL supplies persistent rows,
transactions, uniqueness, and coordination locks. FastAPI is the read interface.
React manages browser interactions; Leaflet draws the map. Tiles come directly
from OpenStreetMap with attribution, separately from earthquake data.

## Why importing twice does not duplicate rows

`earthquakes.usgs_id` is the primary key. `upsert_event()` uses PostgreSQL conflict
handling for concurrent inserts. An existing ID remains one row. On November 5,
the first import inserted one new event and kept five unchanged; the second kept
all six unchanged. Total stored rows and distinct IDs were both 51 afterward.
Idempotency refers to event identity/content: attempt logs and last-seen times can
still advance when you rerun an import.

## How a revision changes a row

USGS supplies a source-updated timestamp. Only a strictly newer one replaces the
stored scientific content/raw event. Older or identical payloads cannot overwrite
it. First-seen remains fixed; last-seen advances independently. Accepted tracked
field changes are stored in `revision_history` and linked to the accepting
`pipeline_runs` row. The timestamp-only revision case is also recorded.

No live revision appeared during Milestone 7. Controlled PostgreSQL tests in
`backend/tests/test_ingestion.py` and `backend/tests/test_worker.py` verify newer,
older, identical, concurrent, and rollback cases without altering real events.

## How an interrupted backfill resumes

A backfill collects older data. `backend/app/historical.py` creates an `import_jobs`
row with the original dates, bounds, and settings, then durable `import_windows`.
A completed window is a checkpoint. Its accepted event writes and completed
status commit atomically. A later failed window cannot erase earlier checkpoints.

Resume acquires the job's PostgreSQL session advisory lock before recovering
unfinished attempts, skips completed windows, and retries remaining work using
saved settings. An ownership token fences a stale executor. A running timestamp
alone does not establish that a process has died. The lock releases when the
connection closes; detection may take time. See `backend/app/coordination.py`.

Catalog windows split adaptively at the 20,000 result limit, down to a 1ms minimum.
Source requests include endpoints, but local acceptance is [start,end). A source
changing during requests means the whole job cannot guarantee a source snapshot.

The original seven-day January 2020 job resumed successfully in Milestone 3.
Milestone 7 reran all historical tests, including saved-settings resume, interruption,
lock contention, overflow, and atomic checkpoint rollback. It did not repeat the
live backfill simply to reproduce already recorded evidence.

## What happens when a request fails

Shared HTTP handling in `ingestion.py` uses explicit timeouts and at most three
attempts for transient network errors, 429, and relevant server errors. Backoff and
Retry-After are bounded. Permanent client errors fail rather than loop forever.
Request failures remain in attempt history even if a retry succeeds.

A recent run with rejected features is partial, not fully successful. A historical
window with rejected records rolls back its writes and persists its failure;
earlier checkpoints survive. A valid empty feed/window is different from an invalid
response. CLI failures/incomplete work exit nonzero. If PostgreSQL itself is down,
tracking may be unwritable; the command cannot claim durable recorded progress.
Controlled tests cover these failures; we never deliberately disrupt USGS.

`backend/app/worker.py` schedules collection/reconciliation separately from
FastAPI. A global advisory lock prevents overlapping scheduled owners. Heartbeats
observe process activity, not successful ingestion. Ctrl+C/SIGTERM requests a stop
at a safe boundary. Stopping collection still leaves stored records readable.
`backend/app/reconcile.py` checks a bounded period and saved event IDs; missing
responses do not imply deletion, and old dates need manual reconciliation.

## Inspect the event and its run

Start the database/API using the [README](../README.md). From the repository root:

```sh
curl -s http://127.0.0.1:8001/api/earthquakes/us7000tvwj
curl -s 'http://127.0.0.1:8001/api/pipeline/runs?run_type=recent_week&page_size=5'
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Inside psql:

```sql
SELECT usgs_id, magnitude, event_time, source_updated_at, first_seen_at, last_seen_at
FROM earthquakes WHERE usgs_id = 'us7000tvwj';
SELECT id, status, started_at, ended_at, inserted, unchanged, errors
FROM pipeline_runs WHERE id = 'cd11c0f9-08e6-4d42-8a05-1f72b6b327d0';
SELECT usgs_id, new_updated_at, changes, pipeline_run_id FROM revision_history
WHERE usgs_id = 'us7000tvwj' ORDER BY recorded_at DESC;
```

The run UUID is the actual first-collection run observed during Milestone 7.
Initial creation is not a revision; an empty revision query is valid. Events are
not linked to a first-run foreign key, so this first-collection association comes
from the recorded import evidence; accepted revisions have explicit run links.

## Short interview demo

Have Docker Engine running, dependencies installed, and migrations applied first.

1. Start API and frontend using README terminal commands. Open
   <http://127.0.0.1:5174>. Point out coverage, PHT labels, and collection vs browser
   fetch time. A stopped worker can still serve this stored view.
2. Run the following twice and compare printed inserted/updated/unchanged counters:

   ```sh
   .tools/bin/uv run --directory backend --locked python -m app.ingestion
   ```

   New events or source revisions between requests are legitimate. In psql, run
   `SELECT count(*), count(DISTINCT usgs_id) FROM earthquakes;` to check identity.
3. Refresh view, set dates that contain your stored data, sort, open a marker and
   event details, and follow its actual source URL. Show exact chart values and
   pipeline attempt counters. Never move dates silently to imply live coverage.
4. Explain a checkpoint using existing job/window rows:

   ```sql
   SELECT id, status, executions FROM import_jobs;
   SELECT job_id, start_at, end_at, status, attempts FROM import_windows;
   ```

   Use a printed job UUID with `python -m app.historical resume JOB_ID` via the
   same uv command. An already complete job should skip work; controlled tests
   demonstrate an unfinished job without intentionally interrupting USGS requests.
5. If a scheduled task is due, demonstrate the bounded worker and show its stopped
   record afterward:

   ```sh
   .tools/bin/uv run --directory backend --locked python -m app.worker --max-tasks 1
   ```

   It waits for due work; it is not a force-run option. For a continuous worker use
   `python -m app.worker` via uv, then Ctrl+C. Close the demo by stating the bounded
   reconciliation, incomplete source coverage, and local-only verification limits.
