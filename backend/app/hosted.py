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


class TargetValidationError(ValueError):
    """Only fixed check names are safe to expose; never retain the input URL."""
    def __init__(self, failed_checks):
        self.failed_checks = failed_checks
        super().__init__('Hosted target validation failed')


def validate_target(settings):
    if not settings.database_url:
        raise TargetValidationError(['database_url_present'])
    try:
        url = make_url(settings.database_url)
    except Exception:
        raise TargetValidationError(['database_url_parseable']) from None
    failed = []
    if not url.host or not url.host.endswith('.neon.tech'):
        failed.append('neon_hostname')
    if url.host and '-pooler' in url.host:
        failed.append('direct_connection')
    if url.database != 'quakeph':
        failed.append('database_name_quakeph')
    if url.query.get('sslmode') not in ('require', 'verify-full'):
        failed.append('tls_sslmode')
    if failed:
        raise TargetValidationError(failed)


def migration_config():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    return config


def run(operation):
    stage = 'settings'
    try:
        settings = get_settings()
        stage = 'target_validation'
        validate_target(settings)
        if operation == 'check':
            print(json.dumps({'operation': operation, 'status': 'success', 'stage': stage}))
            return 0
        stage = 'engine_initialization'
        engine = get_engine()
        # Same direct-session lock as scheduled workers; never use the pooler.
        stage = 'database_lock'
        with session_lock(engine, SCHEDULE_LOCK):
            stage = 'migration_configuration'
            config = migration_config()
            if operation == 'migrate':
                stage = 'migration_upgrade'
                command.upgrade(config, 'head')
                stage = 'schema_check'
                command.check(config)
                result = {'operation': 'migrate', 'status': 'success'}
            else:
                stage = 'migration_head_check'
                with engine.connect() as connection:
                    if set(MigrationContext.configure(connection).get_current_heads()) != set(ScriptDirectory.from_config(config).get_heads()):
                        raise RuntimeError('Apply migrations before collection')
                stage = 'recent_ingestion'
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
        failure = {'operation': operation, 'status': 'failed', 'stage': stage,
                   'reason': type(exc).__name__,
                   'detail': 'Check the named stage; target values and credentials are intentionally omitted.'}
        if isinstance(exc, TargetValidationError):
            failure['failed_checks'] = exc.failed_checks
        print(json.dumps(failure))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['check', 'migrate', 'recent'])
    args = parser.parse_args()
    return run(args.operation)


if __name__ == '__main__':
    raise SystemExit(main())
