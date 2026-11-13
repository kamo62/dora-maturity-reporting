from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, create_engine
from sqlalchemy import Column, Text
from sqlalchemy.types import JSON


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org: Optional[str] = Field(default=None, index=True, max_length=256)
    project: Optional[str] = Field(default=None, index=True, max_length=256)
    repo: Optional[str] = Field(default=None, index=True, max_length=256)
    branch: Optional[str] = Field(default=None, index=True, max_length=256)
    commit_sha: Optional[str] = Field(default=None, index=True, max_length=64)

    executed_at: datetime = Field(index=True)
    environment: Optional[str] = Field(default=None, max_length=64)
    run_label: Optional[str] = Field(default=None, max_length=256)

    tool_version: Optional[str] = Field(default=None, max_length=64)
    thresholds_version: Optional[str] = Field(default=None, max_length=64)
    config_hash: Optional[str] = Field(default=None, max_length=128)
    calculation_version: Optional[str] = Field(default=None, max_length=64)

    # Status tracking
    status: Optional[str] = Field(default=None, index=True, max_length=32)
    error: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Timing
    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)

    # Artifacts path
    artifacts_dir: Optional[str] = Field(default=None, sa_column=Column(Text))


class RawMetric(SQLModel, table=True):
    __tablename__ = "raw_metrics"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    org: Optional[str] = Field(default=None, index=True, max_length=256)
    project: Optional[str] = Field(default=None, index=True, max_length=256)
    repo: Optional[str] = Field(default=None, index=True, max_length=256)

    run_id: UUID = Field(foreign_key="runs.id", index=True)
    source_system: str = Field(index=True, max_length=64)
    source_key: str = Field(index=True, max_length=256)
    raw_data: Dict[str, Any] = Field(sa_column=Column(JSON))
    collected_at: datetime = Field(index=True)


class Metric(SQLModel, table=True):
    __tablename__ = "metrics"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    org: Optional[str] = Field(default=None, index=True, max_length=256)
    project: Optional[str] = Field(default=None, index=True, max_length=256)
    repo: Optional[str] = Field(default=None, index=True, max_length=256)

    run_id: UUID = Field(foreign_key="runs.id", index=True)
    scope: Optional[str] = Field(default=None, index=True, max_length=256)
    metric_key: str = Field(index=True, max_length=256)

    metric_value: Optional[Decimal] = None
    metric_json: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    score: Optional[Decimal] = None
    status: Optional[str] = Field(default=None, max_length=64)
    calculation_version: Optional[str] = Field(default=None, max_length=64)

    observed_at: datetime = Field(index=True)


_ENGINE_CACHE: Dict[str, Any] = {}
try:
    import threading as _threading
    _ENGINE_CACHE_LOCK = _threading.Lock()
except Exception:
    _ENGINE_CACHE_LOCK = None  # type: ignore


def init_engine_and_tables(database_url: str, allow_create_all: bool = False):
    """
    Initialize and cache a SQLAlchemy engine. Optionally create tables for local dev only.

    Note: In production/CI, use Alembic migrations to create schema with proper JSONB,
    hypertables, indexes, and constraints.
    """
    from devops_metrics.config.access import validate_database_url

    validate_database_url(database_url)
    engine = _ENGINE_CACHE.get(database_url)
    if not engine:
        if _ENGINE_CACHE_LOCK is not None:
            with _ENGINE_CACHE_LOCK:
                engine = _ENGINE_CACHE.get(database_url)
                if not engine:
                    engine = create_engine(database_url, pool_pre_ping=True, future=True)
                    _ENGINE_CACHE[database_url] = engine
        else:
            engine = create_engine(database_url, pool_pre_ping=True, future=True)
            _ENGINE_CACHE[database_url] = engine

    if allow_create_all:
        # Guard create_all for first-time local dev. This creates plain tables only.
        from sqlalchemy import inspect

        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        needed = {"runs", "raw_metrics", "metrics"}
        if not needed.issubset(existing_tables):
            SQLModel.metadata.create_all(engine)

    return engine
