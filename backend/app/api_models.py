"""Typed dashboard contract. All datetime query values must explicitly carry an offset."""
from datetime import datetime, date, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Sort = Literal['time_desc', 'time_asc', 'magnitude_desc', 'magnitude_asc', 'depth_asc', 'depth_desc', 'updated_desc']
RunType = Literal['recent_week', 'historical_window', 'reconcile_event']
RunStatus = Literal['running', 'success', 'partial', 'failed']


class Filters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: AwareDatetime | None = Field(default=None, description='Inclusive ISO datetime with explicit timezone; normalized to UTC.')
    end: AwareDatetime | None = Field(default=None, description='Exclusive ISO datetime with explicit timezone; normalized to UTC.')
    min_magnitude: float | None = Field(default=None, allow_inf_nan=False)
    max_magnitude: float | None = Field(default=None, allow_inf_nan=False)
    min_depth: float | None = Field(default=None, allow_inf_nan=False, description='Kilometers; negative values are valid.')
    max_depth: float | None = Field(default=None, allow_inf_nan=False)
    place: str | None = Field(default=None, min_length=1, max_length=200, description='Case-insensitive literal substring, not a wildcard pattern.')
    include_outside: bool = Field(default=False, description='Include stored events outside current configured coordinate bounds.')
    include_deleted: bool = Field(default=False, description='Include explicitly deleted source events; missing/unresolved observations are not deletions.')

    @field_validator('start', 'end', mode='before')
    @classmethod
    def aware_iso(cls, value):
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
            if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError('An ISO datetime with explicit timezone is required')
            return parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError, TypeError) as exc:
            raise ValueError('Use a valid ISO datetime with explicit timezone, e.g. 2026-11-01T00:00:00Z') from exc

    @field_validator('place')
    @classmethod
    def clean_place(cls, value):
        if value is not None:
            value = value.strip()
            if not value or '\x00' in value:
                raise ValueError('place must contain non-NUL searchable text')
        return value

    @model_validator(mode='after')
    def ordered_ranges(self):
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError('start must precede exclusive end')
        for lower, upper in [(self.min_magnitude, self.max_magnitude), (self.min_depth, self.max_depth)]:
            if lower is not None and upper is not None and lower > upper:
                raise ValueError('numeric minimum must not exceed maximum')
        return self


class PageQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=25, ge=1, le=100)


class ListQuery(Filters, PageQuery):
    sort: Sort = 'time_desc'


class MapQuery(Filters):
    limit: int = Field(default=1000, ge=1, le=5000)


class StatsQuery(Filters):
    daily_days: int = Field(default=30, ge=1, le=366, description='Maximum Manila calendar days in the chart; full matching totals remain unbounded.')


class RunsQuery(PageQuery):
    run_type: RunType | None = None
    status: RunStatus | None = None


class Coverage(BaseModel):
    label: Literal['Philippines and surrounding region'] = 'Philippines and surrounding region'
    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float


class EventSummary(BaseModel):
    usgs_id: str
    event_time: AwareDatetime
    source_updated_at: AwareDatetime
    magnitude: float | None
    magnitude_type: str | None
    place: str | None
    longitude: float
    latitude: float
    depth_km: float
    review_status: str | None
    event_type: str
    source_state: str
    coverage_status: Literal['inside', 'outside']
    last_source_observation: str | None
    source_checked_at: AwareDatetime | None


class Pagination(BaseModel):
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


class EventList(BaseModel):
    items: list[EventSummary]
    total: int
    filters: Filters
    coverage: Coverage
    pagination: Pagination
    sort: Sort


class Revision(BaseModel):
    id: UUID
    previous_updated_at: AwareDatetime
    new_updated_at: AwareDatetime
    changes: dict
    pipeline_run_id: UUID
    recorded_at: AwareDatetime


class RevisionPage(BaseModel):
    items: list[Revision]
    total: int
    returned: int
    limit: int
    truncated: bool


class EventDetail(EventSummary):
    source_url: str | None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    coverage: Coverage
    recorded_coverage_status: str
    source_status_note: str
    revisions: RevisionPage


class Geometry(BaseModel):
    type: Literal['Point'] = 'Point'
    coordinates: tuple[float, float]


class MapProperties(BaseModel):
    event_time: AwareDatetime
    magnitude: float | None
    place: str | None
    depth_km: float
    source_state: str
    coverage_status: Literal['inside', 'outside']
    last_source_observation: str | None


class Feature(BaseModel):
    type: Literal['Feature'] = 'Feature'
    id: str
    geometry: Geometry
    properties: MapProperties


class MapMetadata(BaseModel):
    total: int
    returned: int
    limit: int
    truncated: bool
    filters: Filters
    coverage: Coverage
    sort: Literal['time_desc'] = 'time_desc'


class MapData(BaseModel):
    type: Literal['FeatureCollection'] = 'FeatureCollection'
    features: list[Feature]
    metadata: MapMetadata


class DailyCount(BaseModel):
    day: date
    count: int


class DailyChart(BaseModel):
    timezone: Literal['Asia/Manila'] = 'Asia/Manila'
    start_day: date
    end_day: date
    days: list[DailyCount]
    events_in_chart: int
    events_outside_chart: int
    truncated: bool
    coverage_verified: Literal[False] = False
    note: str = 'Counts describe stored matching events only. Zero does not prove complete source coverage.'


class Statistics(BaseModel):
    matching_count: int
    strongest: EventSummary | None
    latest: EventSummary | None
    filters: Filters
    coverage: Coverage
    daily: DailyChart


class SafeError(BaseModel):
    kind: str
    reason: str | None
    message: str
    http_status: int | None = None
    attempt: int | None = None
    record_index: int | None = None


class ErrorPage(BaseModel):
    items: list[SafeError]
    recorded_count: int
    truncated: bool


class Counters(BaseModel):
    fetched: int
    in_scope: int
    inserted: int
    updated: int
    unchanged: int
    rejected: int
    out_of_scope: int
    excluded_type: int
    failed_request_attempts: int


class RunScope(BaseModel):
    coverage: Coverage | None
    start: AwareDatetime | None
    exclusive_end: AwareDatetime | None
    event_id: str | None


class RunSummary(BaseModel):
    id: UUID
    run_type: str
    status: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    counters: Counters
    scope: RunScope
    import_window_id: UUID | None
    import_job_id: UUID | None
    reconciliation_id: UUID | None
    errors: ErrorPage
    owner_confirmation: Literal['not_verified'] = 'not_verified'


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    pagination: Pagination
    run_type: RunType | None
    status: RunStatus | None


class WorkerState(BaseModel):
    id: UUID
    recorded_status: str
    recorded_activity: str
    started_at: AwareDatetime
    heartbeat_at: AwareDatetime
    heartbeat_age_seconds: float
    heartbeat_fresh: bool
    stopped_at: AwareDatetime | None
    error: SafeError | None


class TaskState(BaseModel):
    name: str
    recorded_status: str
    recorded_running: bool
    worker_id: UUID | None
    interval_seconds: float
    last_attempt_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    last_attempt_id: UUID | None
    last_success_id: UUID | None
    next_run_at: AwareDatetime
    overdue: bool
    reconciliation_id: UUID | None
    reconciliation_scope: RunScope | None
    error: SafeError | None
    owner_confirmation: Literal['not_verified'] = 'not_verified'
    scheduled_time_is_guarantee: Literal[False] = False


class PipelineStatus(BaseModel):
    recent_attempt_at: AwareDatetime | None = None
    recent_success_at: AwareDatetime | None = None
    observed_at: AwareDatetime
    worker_state: Literal['not_observed', 'alive', 'stale', 'stopped']
    freshness_seconds: int
    workers: list[WorkerState]
    workers_total: int
    workers_truncated: bool
    scheduled_lock_held: bool
    tasks: list[TaskState]
    recent_failures: list[RunSummary]
    failures_limit: int
    failures_truncated: bool
    note: str = 'Heartbeat freshness is separate from collection success. Running records and a held lock do not confirm an individual owner. Due times require a running worker; they are not execution guarantees.'
