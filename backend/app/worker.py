"""Small persistent scheduler. Run separately from FastAPI: python -m app.worker."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import json
import os
import signal
import socket
from threading import Event, Thread
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.database import get_engine
from app.coordination import (JobBusy, OwnershipLost, StopRequested, session_lock,
                              transaction, fenced_by, stopping_with)
from app.ingestion import ingest, utcnow
from app.reconcile import create_reconciliation, execute_reconciliation
from app.models import workers, scheduled_tasks as tasks, pipeline_runs as runs

SCHEDULE_LOCK = 'quakeph:scheduled-ingestion:v1'


@contextmanager
def shutdown_signals(stop):
    def requested(signum, frame):
        stop.set()  # No SQL or exception-throwing inside a signal handler.
    previous = {sig: signal.signal(sig, requested) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class Worker:
    def __init__(self, engine, settings, clock=utcnow, stop=None, client=None):
        self.engine, self.settings, self.clock = engine, settings, clock
        self.stop, self.client = stop or Event(), client
        self.id, self.activity = uuid4(), 'idle'
        self.last_error = None
        with engine.begin() as conn:
            conn.execute(workers.insert().values(id=self.id, hostname=socket.gethostname(), pid=os.getpid(),
                started_at=clock(), heartbeat_at=clock(), status='running', activity='idle'))
            for name, interval in [('recent', settings.worker_recent_seconds),
                                   ('reconcile', settings.worker_reconcile_seconds)]:
                statement = insert(tasks).values(name=name, interval_seconds=interval,
                    next_run_at=clock(), status='pending')
                conn.execute(statement.on_conflict_do_update(index_elements=[tasks.c.name],
                    set_={'interval_seconds': interval}))

    def heartbeat(self, status='running'):
        # Independent liveness write: intentionally not conditioned on task success.
        with self.engine.begin() as conn:
            conn.execute(workers.update().where(workers.c.id == self.id).values(
                heartbeat_at=self.clock(), status=status, activity=self.activity, last_error=self.last_error,
                **({'stopped_at': self.clock()} if status != 'running' else {})))

    def heartbeat_loop(self, finished):
        while not finished.wait(self.settings.worker_heartbeat_seconds):
            try:
                self.heartbeat()
            except Exception:
                # If PostgreSQL is down it cannot receive a heartbeat. Task writes
                # still fail independently; don't claim freshness in that case.
                pass

    def recover_tasks(self):
        # Caller holds the GLOBAL schedule session lock. No other scheduled owner
        # can be alive with its lock. Manual pipeline runs are never guessed abandoned.
        with transaction(self.engine) as conn:
            abandoned = conn.execute(select(tasks).where(tasks.c.status == 'running').with_for_update()).mappings().all()
            for task in abandoned:
                error = {'reason': 'interrupted', 'detail': 'Previous schedule owner no longer holds its lock'}
                if task['name'] == 'recent' and task['last_attempt_id']:
                    previous = conn.execute(select(runs).where(runs.c.id == task['last_attempt_id'])).mappings().first()
                    if previous and previous['status'] == 'running':
                        conn.execute(runs.update().where(runs.c.id == previous['id']).values(
                            status='failed', ended_at=self.clock(), errors=previous['errors'] + [error]))
                conn.execute(tasks.update().where(tasks.c.name == task['name']).values(
                    status='failed', last_error=error, next_run_at=self.clock()))

    def tick(self):
        if self.stop.is_set():
            return None
        self.heartbeat()
        try:
            with session_lock(self.engine, SCHEDULE_LOCK), stopping_with(self.stop):
                self.recover_tasks()
                with transaction(self.engine) as conn:
                    due = conn.execute(select(tasks).where(tasks.c.next_run_at <= self.clock())
                        .order_by(tasks.c.next_run_at, tasks.c.name.asc()).limit(1).with_for_update()).mappings().first()
                    if due is None:
                        return None
                    token = uuid4()
                    attempt_id = uuid4() if due['name'] == 'recent' else due['reconciliation_id']
                    if due['name'] == 'reconcile' and attempt_id is None:
                        # Boundaries are frozen once, not recomputed on retries/restart.
                        end = self.clock()
                        end = end.replace(microsecond=(end.microsecond // 1000)*1000)
                        attempt_id = create_reconciliation(self.engine,
                            end - timedelta(days=self.settings.worker_reconcile_days), end,
                            self.settings, connection=conn)
                    conn.execute(tasks.update().where(tasks.c.name == due['name']).values(
                        status='running', worker_id=self.id, owner_token=token, attempts=tasks.c.attempts + 1,
                        last_attempt_at=self.clock(), last_attempt_id=attempt_id,
                        reconciliation_id=attempt_id if due['name'] == 'reconcile' else None,
                        next_run_at=self.clock() + timedelta(seconds=due['interval_seconds']), last_error=None))

                def fence(conn):
                    owner = conn.execute(select(tasks.c.owner_token).where(tasks.c.name == due['name'])
                                         .with_for_update()).scalar_one()
                    if owner != token:
                        raise OwnershipLost('Scheduled task ownership changed')

                self.activity = due['name']
                self.heartbeat()
                with fenced_by(fence):
                    try:
                        if due['name'] == 'recent':
                            if self.client is None:
                                with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                                    headers={'User-Agent': 'QuakePH-student-project/0.4'}) as client:
                                    outcome = ingest(self.engine, self.settings, client, self.stop.wait, run_id=attempt_id)
                            else:
                                outcome = ingest(self.engine, self.settings, self.client, self.stop.wait, run_id=attempt_id)
                        else:
                            outcome = execute_reconciliation(self.engine, attempt_id, self.client, self.stop.wait)
                        successful = outcome['status'] == 'success'
                        error = None if successful else {'reason': 'task_incomplete', 'outcome': outcome}
                    except OwnershipLost:
                        raise
                    except Exception as exc:
                        successful = False
                        outcome = {'status': 'failed', 'reason': type(exc).__name__}
                        error = outcome
                    with transaction(self.engine, allow_stopping=True) as conn:
                        # Treat a shutdown before completion as failure, never success.
                        successful = successful and not self.stop.is_set()
                        if not successful and error is None:
                            error = {'reason': 'shutdown_requested'}
                        conn.execute(tasks.update().where(tasks.c.name == due['name']).values(
                            status='success' if successful else 'failed', last_error=error,
                            next_run_at=self.clock() + timedelta(seconds=(due['interval_seconds'] if successful
                                else self.settings.worker_retry_seconds)),
                            **({'last_success_at': self.clock(), 'last_success_id': attempt_id,
                                'reconciliation_id': None} if successful else {})))
                self.last_error = error
                return {'task': due['name'], 'attempt_id': str(attempt_id), 'outcome': outcome}
        except JobBusy:
            self.activity = 'standby'
            return None
        finally:
            self.activity = 'idle'
            self.heartbeat()

    def close(self, status='stopped'):
        self.activity = 'stopped'
        self.heartbeat(status)

    def run(self, max_tasks=None):
        finished = Event()
        heartbeat_thread = Thread(target=self.heartbeat_loop, args=(finished,), daemon=True)
        heartbeat_thread.start()
        count = 0
        status = 'stopped'
        try:
            while not self.stop.is_set():
                outcome = self.tick()
                if outcome is not None:
                    print(json.dumps(outcome, default=str), flush=True)
                    count += 1
                    if max_tasks is not None and count >= max_tasks:
                        break
                self.stop.wait(self.settings.worker_poll_seconds)
            return 0
        except StopRequested:
            return 0
        except Exception as exc:
            status = 'failed'
            self.last_error = {'reason': type(exc).__name__}
            print(json.dumps({'worker_id': str(self.id), 'status': 'failed', 'reason': type(exc).__name__}), flush=True)
            return 1
        finally:
            finished.set()
            heartbeat_thread.join(timeout=5)
            self.close(status)


def main():
    parser = argparse.ArgumentParser(description='Collect recent USGS data and reconcile on persistent schedules.')
    parser.add_argument('--max-tasks', type=int, help='Stop after this many attempted due tasks (verification only)')
    args = parser.parse_args()
    if args.max_tasks is not None and args.max_tasks < 1:
        parser.error('--max-tasks must be positive')
    stop = Event()
    with shutdown_signals(stop):
        try:
            worker = Worker(get_engine(), get_settings(), stop=stop)
            print(json.dumps({'worker_id': str(worker.id), 'pid': os.getpid()}), flush=True)
            return worker.run(args.max_tasks)
        except Exception as exc:
            print(json.dumps({'status': 'failed', 'reason': type(exc).__name__}))
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
