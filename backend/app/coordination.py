"""Session locks and short, fenced transactions; never idle in a transaction."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


class JobBusy(RuntimeError):
    pass


class OwnershipLost(RuntimeError):
    pass


class StopRequested(RuntimeError):
    pass


_guards = ContextVar('guards', default=())
_fences = ContextVar('fences', default=())
_stop = ContextVar('stop', default=None)


def check_guards(allow_stopping=False):
    stop = _stop.get()
    if stop is not None and stop.is_set() and not allow_stopping:
        raise StopRequested('Shutdown requested; unfinished work remains resumable')
    for guard in _guards.get():
        guard.check()


@contextmanager
def stopping_with(stop):
    token = _stop.set(stop)
    try:
        yield
    finally:
        _stop.reset(token)


@contextmanager
def fenced_by(callback):
    token = _fences.set(_fences.get() + (callback,))
    try:
        yield
    finally:
        _fences.reset(token)


@contextmanager
def transaction(engine, allow_stopping=False):
    check_guards(allow_stopping)
    with engine.begin() as connection:
        for fence in _fences.get():
            fence(connection)
        yield connection
        # A disconnect/stop during processing must roll back the whole checkpoint.
        check_guards(allow_stopping)


class SessionGuard:
    def __init__(self, connection, key):
        self.connection, self.key = connection, key
        self.physical = connection.connection.dbapi_connection
        self.pid = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()

    def check(self):
        connection = self.connection
        if connection.invalidated or connection.closed:
            raise OwnershipLost('Coordination connection lost')
        try:
            if connection.connection.dbapi_connection is not self.physical:
                raise OwnershipLost('Coordination connection changed')
            held = connection.execute(text('''SELECT EXISTS (
                SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype='advisory'
                AND classid=:high AND objid=:low AND objsubid=1 AND granted)'''),
                {'pid': self.pid, 'high': (self.key & ((1 << 64)-1)) >> 32,
                 'low': self.key & ((1 << 32)-1)}).scalar_one()
            if not held:
                raise OwnershipLost('Coordination lock no longer held')
        except SQLAlchemyError as exc:
            raise OwnershipLost('Coordination connection unavailable') from exc


@contextmanager
def session_lock(engine, identity):
    namespace = engine.get_execution_options().get('schema_translate_map', {}).get(None, 'public')
    key = int.from_bytes(hashlib.sha256(f'{namespace}:{identity}'.encode()).digest()[:8], 'big', signed=True)
    # AUTOCOMMIT is essential: holding the session lock does not hold a DB transaction.
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        acquired = False
        try:
            acquired = connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar_one()
            if not acquired:
                raise JobBusy(f'{identity} is already being executed; no work was changed')
            guard = SessionGuard(connection, key)
            token = _guards.set(_guards.get() + (guard,))
            try:
                yield guard
            finally:
                _guards.reset(token)
        finally:
            if acquired:
                try:
                    if not connection.invalidated:
                        connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                except SQLAlchemyError:
                    # Never return a possibly locked physical connection to the pool.
                    connection.invalidate()
