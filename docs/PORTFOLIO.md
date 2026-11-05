# Portfolio wording

## Short project description

QuakePH is a local earthquake data engineering project for the Philippines and
surrounding region. It collects real USGS data into PostgreSQL, supports resumable
historical imports and bounded reconciliation, and exposes a read-only FastAPI
and React/Leaflet dashboard with filters, event revisions, and pipeline monitoring.

## Three resume bullets

- Built a Python/PostgreSQL USGS ingestion pipeline with revision-aware upserts,
  bounded HTTP retries, and persistent attempt counters; verified repeated live
  imports preserved unique event IDs.
- Implemented resumable historical imports with atomic window checkpoints,
  adaptive request subdivision, and PostgreSQL advisory-lock coordination;
  tested interruption recovery and concurrent ownership using isolated schemas.
- Created a typed FastAPI and responsive React/Leaflet dashboard with shared
  Philippine-date filters, URL navigation, independent panel failures, and
  pipeline visibility; verified 135 backend tests and 29 frontend tests in two
  timezone configurations, plus desktop/mobile production-preview checks.

## 60-second interview explanation

“QuakePH is my earthquake data pipeline and dashboard for the Philippines and
nearby waters. Python fetches real USGS data, validates it, and stores it in
PostgreSQL. Each event has a unique USGS ID, so rerunning an import does not
create duplicates. A newer source timestamp can update the event while keeping
its first-seen time and recording revision history.

For older data, I split the date range into windows and save each completed
window with its event writes in one transaction. If a later request fails, resume
continues from unfinished work. PostgreSQL locks prevent competing job owners.
A separate worker schedules collection and bounded reconciliation.

FastAPI exposes stored data to a React and Leaflet dashboard with Philippine-day
filters and pipeline status. I verified real imports, repeat imports, worker
execution, isolated database tests, and browser behavior. Coverage is a bounding
box, the source is not a fixed snapshot, and local collection pauses when the
worker stops. This is a verified local portfolio project; I have not verified
public hosting or complete earthquake coverage.”

Use these claims with the [verification record](VERIFICATION.md). No performance,
user-count, uptime, deployment, or completeness claim is implied.
