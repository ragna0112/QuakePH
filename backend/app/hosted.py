"""One-shot cloud operations. Never called by HTTP or during a Vercel build."""
import argparse
import json
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.coordination import session_lock
from app.database import get_engine
from app.ingestion import ingest
from app.worker import SCHEDULE_LOCK


def validate_target(settings):
    url = make_url(settings.database_url)
    if (not url.host or not url.host.endswith('.neon.tech')
            or '-pooler' in url.host or url.database != 'quakeph'
            or url.query.get('sslmode') not in ('require', 'verify-full')):
        raise ValueError('Use the dedicated QuakePH direct Neon database with TLS')


def migration_config():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    return config


def run(operation):
    try:
        settings = get_settings()
        validate_target(settings)
        engine = get_engine()
        # Same direct-session lock as scheduled workers; never use the pooler.
        with session_lock(engine, SCHEDULE_LOCK):
            config = migration_config()
            if operation == 'migrate':
                command.upgrade(config, 'head')
                command.check(config)
                result = {'operation': 'migrate', 'status': 'success'}
            else:
                with engine.connect() as connection:
                    if set(MigrationContext.configure(connection).get_current_heads()) != set(ScriptDirectory.from_config(config).get_heads()):
                        raise RuntimeError('Apply migrations before collection')
                with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                                  headers={'User-Agent': 'QuakePH-student-project/0.2'}) as client:
                    outcome = ingest(engine, settings, client)
                # Only IDs, status and numeric counters enter Actions logs.
                result = {key: value for key, value in outcome.items() if key != 'errors'}
                result['operation'] = 'recent'
        print(json.dumps(result))
        return 0 if result['status'] == 'success' else 1
    except Exception as exc:
        # Do not log exception strings, tracebacks, connection URLs or credentials.
        print(json.dumps({'operation': operation, 'status': 'failed', 'reason': type(exc).__name__,
                          'detail': 'Check the QuakePH target, migration state and database availability.'}))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['migrate', 'recent'])
    args = parser.parse_args()
    return run(args.operation)


if __name__ == '__main__':
    raise SystemExit(main())
