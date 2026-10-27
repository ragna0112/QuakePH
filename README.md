# QuakePH — Milestone 1

A student data engineering project covering **Philippines and surrounding region**
(4–22° latitude, 116–130° longitude). This configurable rectangle includes nearby
waters; it is not an exact national boundary.

Only the foundation is implemented. No earthquakes, imports, map, search, scheduler,
or monitoring history are implemented yet. No sample events are shown in the app.

## How the pieces fit

- `backend/app/`: FastAPI serves HTTP endpoints; SQLAlchemy connects to PostgreSQL.
- `backend/alembic/`: Alembic tracks database schema versions. The baseline revision
  creates only Alembic's version tracking table; earthquake tables come later.
- `backend/tests/`: pytest checks health behavior and configuration validation.
- `frontend/`: React + TypeScript + Vite displays the application shell and API status.
  Vite forwards `/api` and `/health` requests to the backend during development.
- `compose.yaml`: runs PostgreSQL 17 with a persistent local Docker volume.

PostgreSQL connections use UTC. Future event columns must use timezone-aware
`TIMESTAMP WITH TIME ZONE` values. The shell formats its check time explicitly in
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
cp .env.example .env
.tools/bin/uv sync --directory backend --locked
npm --prefix frontend ci
```

Edit the private `.env` if desired. Keep `DATABASE_URL` consistent with the
`POSTGRES_*` values (URL-encode special characters in URL credentials). If you
change `POSTGRES_PORT`, update the URL port too. `.env` is ignored by Git. The
example password is only for this loopback-bound local development database.
PostgreSQL initialization settings apply when its data volume is first created;
changing `.env` does not change an existing database user's password.

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
  `alembic current` to confirm `0001_baseline (head)` separately.
- The dashboard should say **API and database ready** and show configured bounds.
  Refresh the page after changing server/database state; status is checked on load.
- To observe the difference, run `docker compose stop db`, check both health URLs,
  then run `docker compose up -d --wait` and refresh the browser.

Stop web servers with Ctrl+C. `docker compose down` stops PostgreSQL while keeping
its named data volume. Do not remove the volume if you need its data.

## Verification on this machine

Passed: dependency resolution with `backend/uv.lock` and `frontend/package-lock.json`,
8 pytest tests, TypeScript/production build, frontend lint, Compose configuration
validation, Alembic offline SQL generation and revision discovery, live HTTP 200
for liveness and HTTP 503 for unavailable database, and browser rendering with
configuration loaded through Vite's proxy.

Blocked: Docker CLI and Compose are installed, but the Docker engine was not
running. Container startup, online migration, persistence, and readiness against a
real PostgreSQL server remain unverified. Successful readiness is covered by a
mocked probe test, not a real database integration test. The installed Starlette
emits a deprecation warning about its httpx TestClient integration; tests pass.

## References and next steps

Official setup references: [FastAPI](https://fastapi.tiangolo.com/deployment/manually/),
[Vite](https://vite.dev/guide/), [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[PostgreSQL image](https://hub.docker.com/_/postgres).
Future sources: [USGS catalog documentation](https://earthquake.usgs.gov/fdsnws/event/1/)
and [recent all-week GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson).

Stop here for Milestone 1. Leaflet, ingestion, event models, and scheduled jobs
will be added only in later milestones. Nothing has been deployed.
