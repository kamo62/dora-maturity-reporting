#!/usr/bin/env python3
"""
Database migration management script for DevOps Metrics.

Usage:
    python scripts/migrate.py                    # Run all pending migrations
    python scripts/migrate.py --status           # Show migration status
    python scripts/migrate.py --check            # Exit 2 if up-to-date, 0 if ran, 1 on error
    python scripts/migrate.py --history          # Show migration history
    python scripts/migrate.py --downgrade N      # Downgrade N versions

Environment Variables:
    DATABASE_URL        - Required. Database connection string
    MIGRATION_TIMEOUT   - Optional. Max wait time for DB (default: 30s)
    RUN_ENV             - Optional. Environment type (local/dev/prod)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, List

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from devops_metrics.config.access import validate_database_url


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    # env.py reads DATABASE_URL from environment
    return cfg


def _script_dir(cfg: Config) -> ScriptDirectory:
    return ScriptDirectory.from_config(cfg)


def check_database_connectivity(url: str, timeout: int = 30) -> bool:
    """Verify database is accessible before running migrations."""
    validate_database_url(url)
    deadline = time.time() + max(1, int(timeout))
    last_err: Optional[Exception] = None

    # Convert SQLAlchemy URL format to psycopg format if needed
    psycopg_url = url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")

    # Prefer psycopg if available for faster check
    try:
        import psycopg

        while time.time() < deadline:
            try:
                with psycopg.connect(psycopg_url, connect_timeout=5) as conn:  # type: ignore[arg-type]
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        return True
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.0)
        # psycopg available but connection failed
        if last_err:
            print(f"Database connectivity check failed: {last_err}")
        return False
    except ImportError:
        # Fallback to SQLAlchemy engine connect when psycopg not available
        engine = create_engine(url, pool_pre_ping=True, future=True)
        try:
            while time.time() < deadline:
                try:
                    with engine.connect() as conn:
                        conn.execute(text("SELECT 1"))
                        return True
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    time.sleep(1.0)
            if last_err:
                print(f"Database connectivity check failed: {last_err}")
            return False
        finally:
            engine.dispose()


def get_current_migration_version(url: str) -> Optional[str]:
    """Get current migration version from database (None if table missing)."""
    validate_database_url(url)
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            # Check if alembic_version table exists
            exists = conn.execute(
                text(
                    "SELECT to_regclass('public.alembic_version') IS NOT NULL"
                    if conn.dialect.name == "postgresql"
                    else "SELECT 1"
                )
            ).scalar()
            # If not postgres, attempt select and catch failure
            try:
                row = conn.execute(text("SELECT version_num FROM alembic_version"))
            except Exception:
                return None
            ver = row.scalar()
            return str(ver) if ver else None
    except Exception:
        return None
    finally:
        engine.dispose()


def get_head_version(cfg: Config) -> Optional[str]:
    try:
        sd = _script_dir(cfg)
        return sd.get_current_head()
    except Exception:
        return None


def get_pending_migrations(url: str) -> List[str]:
    cfg = _alembic_config()
    head = get_head_version(cfg)
    current = get_current_migration_version(url)
    if head is None:
        return []
    if current is None:
        # None implies no alembic_version yet
        return [head]
    if current == head:
        return []
    # Build path from current to head
    try:
        sd = _script_dir(cfg)
        revs = list(sd.iterate_revisions(head, current))
        return [r.revision for r in revs]
    except Exception:
        # Fallback: unknown pending state
        return [head]


def run_migrations(url: str) -> bool:
    validate_database_url(url)
    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Alembic upgrade failed: {e}")
        return False


def show_history() -> None:
    cfg = _alembic_config()
    sd = _script_dir(cfg)
    for rev in sd.walk_revisions():
        print(f"{rev.revision} - {rev.doc or rev.message}")


def show_status(url: str) -> int:
    cfg = _alembic_config()
    head = get_head_version(cfg)
    current = get_current_migration_version(url)
    print(f"Current: {current or 'None'} | Head: {head or 'None'}")
    pend = get_pending_migrations(url)
    if pend:
        print(f"Pending migrations: {', '.join(pend)}")
        return 1
    print("Migrations up-to-date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage database migrations")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--check", action="store_true", help="Exit 2 if up-to-date, 0 if ran, 1 on error")
    parser.add_argument("--history", action="store_true", help="Show migration history")
    parser.add_argument("--downgrade", type=str, default=None, help="Downgrade target (e.g., -1)")
    parser.add_argument("--timeout", type=int, default=None, help="Database connection timeout in seconds")
    args = parser.parse_args()

    # Parse MIGRATION_TIMEOUT with error handling
    if args.timeout is None:
        try:
            args.timeout = int(os.getenv("MIGRATION_TIMEOUT", "30"))
        except ValueError as e:
            print(f"Invalid MIGRATION_TIMEOUT: '{os.getenv('MIGRATION_TIMEOUT')}' must be an integer, using default 30")
            args.timeout = 30

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required")
        return 1

    if args.history:
        show_history()
        return 0

    if not check_database_connectivity(url, timeout=args.timeout):
        return 1

    if args.status:
        return show_status(url)

    if args.downgrade:
        cfg = _alembic_config()
        try:
            command.downgrade(cfg, args.downgrade)
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"Downgrade failed: {e}")
            return 1

    if args.check:
        pend = get_pending_migrations(url)
        if not pend:
            # up-to-date
            return 2
        ok = run_migrations(url)
        return 0 if ok else 1

    # Default action: run migrations to head
    ok = run_migrations(url)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
