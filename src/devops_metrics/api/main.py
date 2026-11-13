from __future__ import annotations

from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Engine
from pathlib import Path
from uuid import UUID, uuid4
import asyncio
import zipfile
import tempfile
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from devops_metrics.config.settings import Settings
from devops_metrics.api.models import CollectionConfig
from devops_metrics.runner import execute_collection_run
from devops_metrics.persistence.models import init_engine_and_tables
from devops_metrics.config.exemptions import ExemptionManager
from devops_metrics.config.access import checked_path
from devops_metrics import __version__ as TOOL_VERSION

app = FastAPI(title="DORA Maturity Reporting", version=TOOL_VERSION)
logger = logging.getLogger(__name__)

# Ephemeral run cache for no-DB mode
RUNS_CACHE: Dict[str, Dict[str, Any]] = {}
# Active task tracking for cancellation
TASKS_CACHE: Dict[str, asyncio.Task] = {}

# Locks for thread-safe cache access
_runs_cache_lock = asyncio.Lock()
_tasks_cache_lock = asyncio.Lock()

_settings: Settings | None = None
_engine: Engine | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_engine(settings: Settings = Depends(get_settings)) -> Engine:
    global _engine
    if _engine is None:
        db_url = settings.database_url or os.getenv("DATABASE_URL")
        if not db_url:
            raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
        _engine = init_engine_and_tables(db_url, allow_create_all=False)
    return _engine


# Optional API key auth

def verify_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> bool:
    if not getattr(settings, "api_key", None):
        return True
    api_key_val = settings.api_key.get_secret_value() if settings.api_key else None
    if x_api_key != api_key_val:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True


# Static mount for artifacts and UI auth helpers

def require_api_key_non_local(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> bool:
    """Require API key in non-local environments.

    - In non-local environments (not in {local, ci}), an API key MUST be configured
      and provided via X-API-Key.
    - In local/ci, API key is not required (disabled for ease of development).
    """
    env = (settings.run_env or os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local").lower()

    # In local/ci environments, skip API key requirement entirely
    if env in {"local", "ci"}:
        return True

    # Non-local environments: require API key
    api_key_val = settings.api_key.get_secret_value() if settings.api_key else None
    if not api_key_val:
        raise HTTPException(status_code=403, detail="API key required in non-local environments; set ADO_API_KEY")
    if x_api_key != api_key_val:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True

# Static mount for artifacts and UI
EXPORTS_DIR = checked_path("exports").resolve()
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(EXPORTS_DIR)), name="files")

UI_DIR = Path(__file__).parent / "ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


@app.on_event("startup")
async def _check_and_maybe_run_migrations():
    """On startup, ensure DB is reachable and schema is up-to-date.

    - In local/dev, if AUTO_RUN_MIGRATIONS=true, run pending migrations.
    - In prod, if pending migrations exist, fail startup with clear error.
    - If DATABASE_URL is not configured, skip.
    """
    try:
        settings = get_settings()
        db_url = settings.database_url or os.getenv("DATABASE_URL")
        if not db_url:
            return
        run_env = (settings.run_env or os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local").lower()
        auto_flag = (os.getenv("AUTO_RUN_MIGRATIONS") or "false").lower() in {"1", "true", "yes"}
        timeout = int(os.getenv("MIGRATION_TIMEOUT") or "30")

        # Import migrate helpers lazily to avoid hard dependency during non-DB runs
        try:
            from scripts.migrate import check_database_connectivity, get_pending_migrations, run_migrations
        except Exception:
            logger.warning("Migration helpers unavailable; skipping migration checks")
            return

        # Wait for DB reachability
        if not check_database_connectivity(db_url, timeout=timeout):
            logger.error("Database unreachable during startup migration check")
            if run_env == "prod":
                raise RuntimeError("Database unreachable and RUN_ENV=prod")
            return

        pending = get_pending_migrations(db_url)
        if not pending:
            logger.info("Database migrations up-to-date")
            return

        if run_env in {"local", "dev"} and auto_flag:
            logger.info("Pending migrations detected; AUTO_RUN_MIGRATIONS=true -> applying")
            ok = run_migrations(db_url)
            if not ok:
                logger.error("Automatic migrations failed during startup")
                if run_env == "prod":
                    raise RuntimeError("Migrations failed during startup")
            return

        if run_env == "prod":
            msg = "Database schema is not up-to-date. Please run migrations (alembic upgrade head or devops-metrics db-migrate)."
            logger.error(msg)
            raise RuntimeError(msg)

        logger.warning("Pending migrations detected; continuing without auto-run (set AUTO_RUN_MIGRATIONS=true to enable)")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Startup migration check failed: {e}")


@app.on_event("startup")
async def _cleanup_orphaned_runs():
    """On startup, reconcile any 'running' or 'queued' runs from a previous session.

    Previous logic marked all such runs as 'failed'. That can misclassify near-complete
    runs where metrics were already persisted but the final status update did not occur
    due to a restart. Here we:
      - If metrics exist for the run_id, recover it as 'succeeded'.
      - Otherwise, mark it as 'failed' with an orphaned error message.
    """
    try:
        settings = get_settings()
        db_url = settings.database_url or os.getenv("DATABASE_URL")
        if not db_url:
            return

        engine = init_engine_and_tables(db_url, allow_create_all=False)
        with engine.begin() as conn:
            # Find runs that appear orphaned from a prior process
            # Only consider runs without finished_at (haven't completed yet)
            rows = conn.execute(
                text(
                    "SELECT id, started_at FROM runs "
                    "WHERE status IN ('running', 'queued') AND finished_at IS NULL"
                )
            ).fetchall()

            if not rows:
                logger.info("No orphaned runs found on startup")
                return

            # Grace period: skip runs that started very recently (might still be initializing)
            grace_period_seconds = int(os.getenv("ORPHAN_CLEANUP_GRACE_SECONDS", "60"))
            now = datetime.now(timezone.utc)

            to_fail: list[str] = []
            to_recover: list[str] = []

            for r in rows:
                rid = r[0]
                started_at = r[1]

                # Skip runs that started within grace period
                if started_at and isinstance(started_at, datetime):
                    # Ensure timezone-aware comparison
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    age_seconds = (now - started_at).total_seconds()
                    if age_seconds < grace_period_seconds:
                        logger.debug(
                            f"Skipping run {rid} (started {age_seconds:.0f}s ago, "
                            f"within {grace_period_seconds}s grace period)"
                        )
                        continue
                # If any metrics exist for this run, treat it as completed and recover as succeeded
                mcount = conn.execute(
                    text("SELECT COUNT(1) FROM metrics WHERE run_id = :id"), {"id": rid}
                ).scalar() or 0
                if int(mcount) > 0:
                    to_recover.append(rid)
                else:
                    to_fail.append(rid)

            def _find_artifacts_dir_for_run(run_id: str, ts_value):
                """Best-effort guess of artifacts_dir by scanning exports for closest directory near ts_value."""
                try:
                    ts = ts_value
                    if not isinstance(ts, datetime):
                        return None
                    candidates = []
                    # One-level deep directories under EXPORTS_DIR
                    for d in sorted(EXPORTS_DIR.glob("*")):
                        try:
                            if not d.is_dir():
                                continue
                            # Look for any expected artifact file names in the directory
                            has_artifacts = False
                            for pat in ("metrics*.json", "metrics*.xlsx", "*_segments.json", "*_segments.xlsx"):
                                if list(d.glob(pat)):
                                    has_artifacts = True
                                    break
                            if not has_artifacts:
                                continue
                            mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                            diff = abs((mtime - ts).total_seconds())
                            candidates.append((diff, d))
                        except Exception as e:
                            logger.debug(f"Error checking directory {d} for artifacts: {e}")
                            continue
                    # Choose the closest within a reasonable threshold (e.g., 2 hours)
                    if not candidates:
                        return None
                    candidates.sort(key=lambda x: x[0])
                    best_diff, best_path = candidates[0]
                    if best_diff <= 2 * 3600:
                        return str(best_path.resolve())
                except Exception as e:
                    logger.debug(f"Artifact discovery failed for run {run_id}: {e}")
                    return None
                return None

            if to_recover:
                for rid in to_recover:
                    # Attempt to fill in artifacts_dir if missing
                    try:
                        row = conn.execute(
                            text(
                                "SELECT artifacts_dir, COALESCE(started_at, executed_at) FROM runs WHERE id=:id"
                            ),
                            {"id": rid},
                        ).fetchone()
                        current_art_dir, ts_val = (row[0], row[1]) if row else (None, None)
                        new_art_dir = None
                        if not current_art_dir:
                            new_art_dir = _find_artifacts_dir_for_run(rid, ts_val)
                        params = {"id": rid, "ts": datetime.now(timezone.utc)}
                        if new_art_dir:
                            conn.execute(
                                text(
                                    "UPDATE runs SET status='succeeded', finished_at = COALESCE(finished_at, :ts), artifacts_dir = :ad WHERE id = :id"
                                ),
                                {**params, "ad": new_art_dir},
                            )
                        else:
                            conn.execute(
                                text(
                                    "UPDATE runs SET status='succeeded', finished_at = COALESCE(finished_at, :ts) WHERE id = :id"
                                ),
                                params,
                            )
                    except Exception as e:
                        # As a fallback, ensure status is succeeded and finished_at set
                        logger.debug(f"Error during recovery of run {rid}, using fallback: {e}")
                        conn.execute(
                            text(
                                "UPDATE runs SET status='succeeded', finished_at = COALESCE(finished_at, :ts) WHERE id = :id"
                            ),
                            {"id": rid, "ts": datetime.now(timezone.utc)},
                        )
                logger.warning(
                    f"Recovered {len(to_recover)} run(s) as 'succeeded' based on persisted metrics: {to_recover}"
                )

            if to_fail:
                for rid in to_fail:
                    conn.execute(
                        text(
                            "UPDATE runs SET status='failed', finished_at = COALESCE(finished_at, :ts), "
                            "error = 'Task orphaned due to API restart/reload' WHERE id = :id"
                        ),
                        {"id": rid, "ts": datetime.now(timezone.utc)},
                    )
                logger.warning(
                    f"Marked {len(to_fail)} orphaned run(s) from previous session as failed: {to_fail}"
                )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to cleanup orphaned runs on startup: {e}")


@app.get("/")
async def root():
    if UI_DIR.exists():
        return RedirectResponse(url="/ui/")
    return {"message": "DevOps Metrics API"}


@app.get("/healthz")
async def healthz(settings: Settings = Depends(get_settings)):
    db_url = settings.database_url or os.getenv("DATABASE_URL")

    # Count active tasks from TASKS_CACHE (in-memory async tasks)
    active_tasks = len(TASKS_CACHE)

    # Count active runs - prefer database if available, fallback to cache
    active_runs = 0
    queued_runs = 0

    if not db_url:
        # No database - count from cache only
        async with _runs_cache_lock:
            for run_data in RUNS_CACHE.values():
                status = run_data.get("status", "")
                if status == "running":
                    active_runs += 1
                elif status == "queued":
                    queued_runs += 1
        return JSONResponse(status_code=200, content={
            "status": "ok",
            "database": "not_configured",
            "active_runs": active_runs,
            "queued_runs": queued_runs,
            "active_tasks": active_tasks
        })

    try:
        engine = init_engine_and_tables(db_url, allow_create_all=False)
        with engine.connect() as conn:
            # Test connection
            conn.execute(text("SELECT 1"))

            # Count active/queued runs from database (source of truth)
            result = conn.execute(text(
                "SELECT status, COUNT(*) as count FROM runs "
                "WHERE status IN ('running', 'queued') "
                "GROUP BY status"
            ))
            for row in result:
                if row[0] == "running":
                    active_runs = row[1]
                elif row[0] == "queued":
                    queued_runs = row[1]

        return {
            "status": "ok",
            "database": "connected",
            "active_runs": active_runs,
            "queued_runs": queued_runs,
            "active_tasks": active_tasks
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Health check failed: {e}")
        # On DB error, fallback to cache counts
        async with _runs_cache_lock:
            for run_data in RUNS_CACHE.values():
                status = run_data.get("status", "")
                if status == "running":
                    active_runs += 1
                elif status == "queued":
                    queued_runs += 1
        return JSONResponse(status_code=503, content={
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "active_runs": active_runs,
            "queued_runs": queued_runs,
            "active_tasks": active_tasks
        })


@app.post("/v1/runs", status_code=202)
async def trigger_run(
    config: CollectionConfig,
    settings: Settings = Depends(get_settings),
    _auth_ok: bool = Depends(require_api_key_non_local),
):
    run_id = str(uuid4())

    # In-memory run record for no-DB mode
    async with _runs_cache_lock:
        RUNS_CACHE[run_id] = {
            "id": run_id,
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "run_label": config.run_label,
            "error": None,
            "artifacts_dir": None,
        }

    # Pre-insert queued row if DB configured
    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if db_url and config.write_db:
        try:
            engine = init_engine_and_tables(db_url, allow_create_all=False)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO runs (id, status, executed_at, run_label, environment, tool_version)
                        VALUES (:id, 'queued', :executed_at, :run_label, :env, :version)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": run_id,
                        "executed_at": datetime.now(timezone.utc),
                        "run_label": config.run_label,
                        "env": (settings.run_env or os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "local"),
                        "version": TOOL_VERSION,
                    },
                )
        except Exception as ex:  # noqa: BLE001
            logger.warning(f"Failed to insert queued run: {ex}")

    async def _run():
        try:
            # Update in-memory status for no-DB mode
            try:
                async with _runs_cache_lock:
                    RUNS_CACHE[run_id]["status"] = "running"
                    RUNS_CACHE[run_id]["started_at"] = datetime.now(timezone.utc)
            except Exception:
                pass
            res = await execute_collection_run(config, settings, run_id)
            try:
                async with _runs_cache_lock:
                    RUNS_CACHE[run_id].update(
                        status=res.get("status"),
                        started_at=res.get("started_at"),
                        finished_at=res.get("finished_at"),
                        error=res.get("error"),
                        artifacts_dir=res.get("artifacts_dir"),
                    )
            except Exception:
                pass
        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            # Update status to cancelled (already set by cancel endpoint, but belt-and-suspenders)
            try:
                async with _runs_cache_lock:
                    RUNS_CACHE[run_id].update(status="cancelled", finished_at=datetime.now(timezone.utc))
            except Exception:
                pass
            # Re-raise to properly cancel the task
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Run {run_id} error: {e}", exc_info=True)
            # Reflect failure in in-memory cache as well
            try:
                async with _runs_cache_lock:
                    RUNS_CACHE[run_id].update(status="failed", finished_at=datetime.now(timezone.utc), error=str(e))
            except Exception:
                pass
            # Best-effort DB status update on background failure
            db2 = settings.database_url or os.getenv("DATABASE_URL")
            if db2 and config.write_db:
                try:
                    engine = init_engine_and_tables(db2, allow_create_all=False)
                    with engine.begin() as conn:
                        conn.execute(
                            text(
                                "UPDATE runs SET status='failed', finished_at=:ts, error=:err WHERE id=:id"
                            ),
                            {"id": run_id, "ts": datetime.now(timezone.utc), "err": str(e)},
                        )
                except Exception:
                    pass

    # Store task for cancellation capability
    task = asyncio.create_task(_run())
    TASKS_CACHE[run_id] = task

    # Clean up task from cache when done
    def _cleanup(task_ref):
        TASKS_CACHE.pop(run_id, None)
    task.add_done_callback(_cleanup)

    return {"id": run_id, "message": "Accepted"}


@app.get("/v1/runs/{id}")
async def get_run_status(id: UUID, settings: Settings = Depends(get_settings)):
    run_id_str = str(id)
    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if db_url:
        try:
            engine = init_engine_and_tables(db_url, allow_create_all=False)
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id::text, status, started_at, finished_at, error, artifacts_dir FROM runs WHERE id=:id"
                    ),
                    {"id": run_id_str},
                ).fetchone()
            if row is not None:
                started, finished = row[2], row[3]
                return {
                    "id": row[0],
                    "status": row[1],
                    "started_at": started,
                    "finished_at": finished,
                    "duration_seconds": (finished - started).total_seconds() if finished and started else None,
                    "error": row[4],
                    "artifacts_dir": row[5],
                }
        except Exception:
            pass
    # Fallback to in-memory cache
    r = RUNS_CACHE.get(run_id_str)
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    s = r.get("started_at")
    f = r.get("finished_at")
    dur = (f - s).total_seconds() if f and s else None
    return {
        "id": r.get("id"),
        "status": r.get("status"),
        "started_at": s,
        "finished_at": f,
        "duration_seconds": dur,
        "error": r.get("error"),
        "artifacts_dir": r.get("artifacts_dir"),
    }


async def _cancel_run_impl(id: UUID, settings: Settings):
    """Shared implementation for cancelling a run."""
    run_id_str = str(id)

    # Check if task exists
    task = TASKS_CACHE.get(run_id_str)
    if not task:
        # Check if run exists in cache or DB
        if run_id_str in RUNS_CACHE:
            status = RUNS_CACHE[run_id_str].get("status")
            if status in ["succeeded", "failed", "cancelled"]:
                raise HTTPException(status_code=400, detail=f"Run already {status}, cannot cancel")
            raise HTTPException(status_code=404, detail="Run not actively running (task not found)")
        raise HTTPException(status_code=404, detail="Run not found")

    # Cancel the task
    task.cancel()

    # Update status in cache
    async with _runs_cache_lock:
        if run_id_str in RUNS_CACHE:
            RUNS_CACHE[run_id_str]["status"] = "cancelled"
            RUNS_CACHE[run_id_str]["finished_at"] = datetime.now(timezone.utc)

    # Update status in DB if configured
    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if db_url:
        try:
            engine = init_engine_and_tables(db_url, allow_create_all=False)
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE runs SET status='cancelled', finished_at=:ts WHERE id=:id"),
                    {"id": run_id_str, "ts": datetime.now(timezone.utc)},
                )
        except Exception as ex:  # noqa: BLE001
            logger.warning(f"Failed to update cancelled run in DB: {ex}")

    return {"id": run_id_str, "status": "cancelled", "message": "Run cancelled successfully"}


@app.delete("/v1/runs/{id}")
async def cancel_run(id: UUID, settings: Settings = Depends(get_settings)):
    """Cancel a running job (DELETE method for backward compatibility)."""
    return await _cancel_run_impl(id, settings)


@app.post("/v1/runs/{id}/cancel")
async def cancel_run_post(id: UUID, settings: Settings = Depends(get_settings)):
    """Cancel a running job (POST method, matches UI bulk-cancel pattern)."""
    return await _cancel_run_impl(id, settings)


@app.delete("/v1/runs/{id}/data")
async def delete_run_data(id: UUID, settings: Settings = Depends(get_settings)):
    """Delete a run and all its associated data from the database (metrics, raw_metrics)."""
    run_id_str = str(id)

    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        engine = init_engine_and_tables(db_url, allow_create_all=False)
        with engine.begin() as conn:
            # Check if run exists
            result = conn.execute(
                text("SELECT id, run_label FROM runs WHERE id = :id"),
                {"id": run_id_str}
            )
            run_row = result.fetchone()
            if not run_row:
                raise HTTPException(status_code=404, detail="Run not found in database")

            # Delete metrics (CASCADE will handle this, but explicit for clarity)
            metrics_deleted = conn.execute(
                text("DELETE FROM metrics WHERE run_id = :id"),
                {"id": run_id_str}
            ).rowcount

            # Delete raw_metrics
            raw_metrics_deleted = conn.execute(
                text("DELETE FROM raw_metrics WHERE run_id = :id"),
                {"id": run_id_str}
            ).rowcount

            # Delete the run itself
            conn.execute(
                text("DELETE FROM runs WHERE id = :id"),
                {"id": run_id_str}
            )

        # Also remove from in-memory cache if present
        async with _runs_cache_lock:
            RUNS_CACHE.pop(run_id_str, None)
        TASKS_CACHE.pop(run_id_str, None)

        return {
            "id": run_id_str,
            "message": "Run data deleted successfully",
            "deleted": {
                "metrics": metrics_deleted,
                "raw_metrics": raw_metrics_deleted,
                "run": 1
            }
        }
    except HTTPException:
        raise
    except Exception as ex:
        logger.error(f"Failed to delete run {run_id_str}: {ex}")
        raise HTTPException(status_code=500, detail=f"Failed to delete run: {str(ex)}")


@app.post("/v1/database/clear")
async def clear_database(settings: Settings = Depends(get_settings)):
    """Clear all data from the database (runs, metrics, raw_metrics).
    Only allowed in local/dev environments for safety."""

    # Safety check - only allow in local/dev
    run_env = (settings.run_env or os.getenv("ADO_RUN_ENV") or os.getenv("RUN_ENV") or "production").lower()
    if run_env not in ('local', 'development', 'dev'):
        raise HTTPException(
            status_code=403,
            detail=f"Database clear is not allowed in '{run_env}' environment. Only local/dev/development permitted."
        )

    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        engine = init_engine_and_tables(db_url, allow_create_all=False)
        with engine.begin() as conn:
            # Count before clearing
            runs_count = conn.execute(text("SELECT COUNT(*) FROM runs")).scalar()
            metrics_count = conn.execute(text("SELECT COUNT(*) FROM metrics")).scalar()
            raw_metrics_count = conn.execute(text("SELECT COUNT(*) FROM raw_metrics")).scalar()

            # Clear all tables
            conn.execute(text("TRUNCATE TABLE metrics, raw_metrics, runs RESTART IDENTITY CASCADE"))

        # Clear in-memory caches
        async with _runs_cache_lock:
            RUNS_CACHE.clear()
        TASKS_CACHE.clear()

        return {
            "message": "Database cleared successfully",
            "environment": run_env,
            "deleted": {
                "runs": runs_count,
                "metrics": metrics_count,
                "raw_metrics": raw_metrics_count
            }
        }
    except HTTPException:
        raise
    except Exception as ex:
        logger.error(f"Failed to clear database: {ex}")
        raise HTTPException(status_code=500, detail=f"Failed to clear database: {str(ex)}")


@app.get("/v1/runs")
async def list_runs(limit: int = 20, status: str | None = None, settings: Settings = Depends(get_settings)):
    def _serialize_cache_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if status:
            items = [r for r in items if (r.get("status") or "").lower() == status.lower()]
        def _ts(v):
            return v or datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda r: _ts(r.get("finished_at") or r.get("started_at")), reverse=True)
        items = items[:max(0, int(limit))]
        out = []
        for r in items:
            s = r.get("started_at")
            f = r.get("finished_at")
            dur = (f - s).total_seconds() if f and s else None
            out.append({
                "id": r.get("id"),
                "status": r.get("status"),
                "started_at": s,
                "finished_at": f,
                "duration_seconds": dur,
                "run_label": r.get("run_label"),
            })
        return out

    db_url = settings.database_url or os.getenv("DATABASE_URL")
    cache_items = list(RUNS_CACHE.values())
    if not db_url:
        return _serialize_cache_items(cache_items)

    query = """
        SELECT id::text, status, started_at, finished_at, run_label
        FROM runs
    """
    params: dict[str, object] = {"limit": limit}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY COALESCE(finished_at, started_at) DESC LIMIT :limit"

    try:
        engine = init_engine_and_tables(db_url, allow_create_all=False)
        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
        if not rows and cache_items:
            return _serialize_cache_items(cache_items)
        out = []
        for r in rows:
            started, finished = r[2], r[3]
            out.append(
                {
                    "id": r[0],
                    "status": r[1],
                    "started_at": started,
                    "finished_at": finished,
                    "duration_seconds": (finished - started).total_seconds() if finished and started else None,
                    "run_label": r[4],
                }
            )
        return out
    except Exception:
        return _serialize_cache_items(cache_items)


@app.get("/v1/runs/{id}/artifacts")
async def list_artifacts(id: UUID, settings: Settings = Depends(get_settings)):
    run_id_str = str(id)
    art_dir_val: Optional[str] = None
    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if db_url:
        try:
            engine = init_engine_and_tables(db_url, allow_create_all=False)
            with engine.connect() as conn:
                row = conn.execute(text("SELECT artifacts_dir FROM runs WHERE id=:id"), {"id": run_id_str}).fetchone()
            if row and row[0]:
                art_dir_val = row[0]
        except Exception:
            pass
    if art_dir_val is None:
        art_dir_val = (RUNS_CACHE.get(run_id_str) or {}).get("artifacts_dir")
    if not art_dir_val:
        raise HTTPException(status_code=404, detail="Artifacts not found")

    art_dir = Path(art_dir_val).resolve()

    try:
        art_dir.relative_to(EXPORTS_DIR)
    except ValueError:
        logger.warning(f"Artifacts directory {art_dir} is outside EXPORTS_DIR {EXPORTS_DIR}")
        raise HTTPException(status_code=403, detail="Invalid artifacts directory")

    if not art_dir.exists():
        raise HTTPException(status_code=404, detail="Artifacts directory missing")

    files = []
    for p in art_dir.glob("**/*"):
        try:
            if p.is_symlink() or not p.is_file():
                continue
            resolved = p.resolve()
            resolved.relative_to(EXPORTS_DIR)
            rel = resolved.relative_to(EXPORTS_DIR)
            files.append({"name": str(rel), "size_bytes": p.stat().st_size, "url": f"/files/{rel}"})
        except Exception:  # noqa: BLE001
            continue
    return {"id": str(id), "artifacts_dir": str(art_dir), "files": files}


@app.get("/v1/runs/{id}/artifacts.zip")
async def download_artifacts_zip(id: UUID, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)):
    run_id_str = str(id)
    art_dir_val: Optional[str] = None
    db_url = settings.database_url or os.getenv("DATABASE_URL")
    if db_url:
        try:
            engine = init_engine_and_tables(db_url, allow_create_all=False)
            with engine.connect() as conn:
                row = conn.execute(text("SELECT artifacts_dir FROM runs WHERE id=:id"), {"id": run_id_str}).fetchone()
            if row and row[0]:
                art_dir_val = row[0]
        except Exception:
            pass
    if art_dir_val is None:
        art_dir_val = (RUNS_CACHE.get(run_id_str) or {}).get("artifacts_dir")
    if not art_dir_val:
        raise HTTPException(status_code=404, detail="Artifacts not found")

    art_dir = Path(art_dir_val).resolve()

    try:
        art_dir.relative_to(EXPORTS_DIR)
    except ValueError:
        logger.warning(f"Artifacts directory {art_dir} is outside EXPORTS_DIR {EXPORTS_DIR}")
        raise HTTPException(status_code=403, detail="Invalid artifacts directory")

    if not art_dir.exists():
        raise HTTPException(status_code=404, detail="Artifacts directory missing")

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in art_dir.glob("**/*"):
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    resolved = p.resolve()
                    resolved.relative_to(EXPORTS_DIR)
                    zf.write(resolved, arcname=resolved.relative_to(art_dir))
                except Exception:  # noqa: BLE001
                    continue

    except Exception as e:  # noqa: BLE001
        zip_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to create artifacts zip: {e}")

    # Defer cleanup until after response completes
    background_tasks.add_task(lambda: zip_path.unlink(missing_ok=True))
    return FileResponse(str(zip_path), media_type="application/zip", filename=f"{id}_artifacts.zip")


@app.get("/v1/runs/{id}/validate")
async def validate_run(id: UUID, engine: Engine = Depends(get_engine), settings: Settings = Depends(get_settings)):
    from devops_metrics.validation.consistency import CalculationValidator

    with engine.connect() as conn:
        row = conn.execute(text("SELECT id::text, artifacts_dir FROM runs WHERE id=:id"), {"id": str(id)}).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")

    run_id, artifacts_dir = row[0], row[1]
    dbu = settings.database_url or os.getenv("DATABASE_URL")
    if not dbu:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    validator = CalculationValidator(dbu)
    try:
        results = validator.validate_segments_by_run_id(run_id, {})
        errors = [r for r in results if not r.is_consistent]
        return {
            "run_id": run_id,
            "status": "ok" if not errors else "failed",
            "issues": [
                {"key": r.key, "excel": r.excel_value, "db": r.db_value, "diff": r.diff_percentage} for r in errors
            ],
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Validation error: {e}")


@app.get("/v1/leaderboard")
async def leaderboard(engine: Engine = Depends(get_engine)):
    # Latest succeeded run
    with engine.connect() as conn:
        run_row = conn.execute(
            text(
                "SELECT id::text FROM runs WHERE status='succeeded' ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1"
            )
        ).fetchone()
    if not run_row:
        raise HTTPException(status_code=404, detail="No successful runs found")
    run_id = run_row[0]
    # Top segments by maturity_score
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT scope, metric_value
                FROM metrics
                WHERE run_id = :run_id AND metric_key = 'maturity_score' AND scope IS NOT NULL
                ORDER BY metric_value DESC NULLS LAST
                LIMIT 20
                """
            ),
            {"run_id": run_id},
        ).fetchall()
    items = [
        {"name": r[0], "score": float(r[1]) if r[1] is not None else None} for r in rows
    ]
    return {"source": "db", "run_id": run_id, "items": items}


@app.get("/v1/leaderboard/files")
async def leaderboard_files():
    # Find latest segments JSON: exports/*/metrics_segments.json
    latest = None
    for p in sorted(EXPORTS_DIR.glob("*/metrics_segments.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        latest = p
        break
    if not latest:
        raise HTTPException(status_code=404, detail="No segments artifact found")
    import json
    data = json.loads(latest.read_text())
    seg = (data or {}).get("segment_scores") or {}
    items = []
    for name, s in seg.items():
        try:
            score = float(s.get("maturity_score", 0.0))
        except Exception:
            score = 0.0
        items.append({"name": name, "score": score})
    items.sort(key=lambda x: x["score"], reverse=True)
    return {"source": "files", "artifact": str(latest.relative_to(EXPORTS_DIR)), "items": items[:20]}


@app.get("/v1/leaderboard/files/projects")
async def leaderboard_files_projects():
    latest = None
    for p in sorted(EXPORTS_DIR.glob("*/metrics_segments.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        latest = p
        break
    if not latest:
        raise HTTPException(status_code=404, detail="No segments artifact found")
    import json
    data = json.loads(latest.read_text())
    proj = (data or {}).get("project_scores") or {}
    items: List[Dict[str, Any]] = []
    for _pid, v in proj.items():
        try:
            nm = v.get("name") or _pid
            sc = float(v.get("maturity_score", 0.0))
            items.append({"name": nm, "score": sc})
        except Exception:
            continue
    items.sort(key=lambda x: x["score"], reverse=True)
    return {"source": "files", "artifact": str(latest.relative_to(EXPORTS_DIR)), "items": items[:20]}


# --------- Projects & Exemptions (Settings) ---------

def _exemptions_path(settings: Settings) -> Path:
    p = settings.exemptions_file or Path("src/devops_metrics/config/exemptions.yaml")
    return Path(p)


@app.get("/v1/projects")
async def list_projects(settings: Settings = Depends(get_settings)):
    # Prefer artifacts for speed; fall back to DB
    items: List[str] = []
    latest = None
    for p in sorted(EXPORTS_DIR.glob("*/metrics_segments.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        latest = p
        break
    if latest and latest.exists():
        try:
            import json
            data = json.loads(latest.read_text())
            project_scores = (data or {}).get("project_scores") or {}
            items = sorted({(v.get("name") or k) for k, v in project_scores.items() if isinstance(v, dict)})
        except Exception:
            items = []
    if not items:
        db_url = settings.database_url or os.getenv("DATABASE_URL")
        if db_url:
            try:
                engine = init_engine_and_tables(db_url, allow_create_all=False)
                with engine.connect() as conn:
                    rows = conn.execute(text("SELECT DISTINCT project FROM runs WHERE project IS NOT NULL ORDER BY project"))
                    items = [r[0] for r in rows if r and r[0]]
            except Exception:
                items = []
    return {"items": items}


@app.get("/v1/exemptions")
async def get_exemptions(settings: Settings = Depends(get_settings)):
    path = _exemptions_path(settings)
    mgr = ExemptionManager(path)
    cfg = mgr.config
    if not cfg:
        return {"version": "1.0", "exemptions": {}, "pattern_exemptions": []}
    # Convert pydantic models to plain dicts
    ex = {k: v.model_dump() for k, v in (cfg.exemptions or {}).items()}
    pats = [p.model_dump() for p in (cfg.pattern_exemptions or [])]
    return {"version": cfg.version, "exemptions": ex, "pattern_exemptions": pats}


@app.get("/v1/exemptions/{project}")
async def get_project_exemption(project: str, settings: Settings = Depends(get_settings)):
    path = _exemptions_path(settings)
    mgr = ExemptionManager(path)
    cfg = mgr.config
    if not cfg:
        return {"project": project, "rule": None}
    for name, rule in (cfg.exemptions or {}).items():
        if name.lower() == (project or "").lower():
            return {"project": name, "rule": rule.model_dump()}
    return {"project": project, "rule": None}


@app.get("/v1/exemptions/meta")
async def get_exemptions_meta():
    metrics = [
        # Quality Assurance
        "code_coverage", "test_pass_rate", "build_success_rate",
        # Deployment Pipeline
        "deployment_frequency", "build_time", "pipeline_stability",
        # Security & Compliance
        "policy_compliance", "security_scanning", "branch_protection",
        # Collaboration Metrics
        "merge_rate", "review_coverage",
    ]
    dimensions = [
        "quality_assurance", "deployment_pipeline", "security_compliance", "collaboration_metrics"
    ]
    return {"metrics": metrics, "dimensions": dimensions}


@app.put("/v1/exemptions/{project}")
async def put_project_exemption(
    project: str,
    body: Dict[str, Any],
    settings: Settings = Depends(get_settings),
):
    path = _exemptions_path(settings)
    # Load existing file (if any)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            data = {}
    else:
        data = {}

    cfg_version = str(data.get("version") or "1.0")
    ex: Dict[str, Any] = data.get("exemptions") or {}
    # Build rule
    exempt_metrics = body.get("exempt_metrics") or []
    exempt_dimensions = body.get("exempt_dimensions") or []
    level = body.get("level") or ("mixed" if exempt_metrics and exempt_dimensions else ("metric" if exempt_metrics else ("dimension" if exempt_dimensions else "metric")))
    rule = {
        "level": level,
        "exempt_metrics": list(exempt_metrics),
        "exempt_dimensions": list(exempt_dimensions),
        "repositories": body.get("repositories") or [],
        "reason": body.get("reason") or "",
        "owner": body.get("owner") or "",
        "effective_date": (body.get("effective_date") or datetime.now(timezone.utc).date()).isoformat(),
        "expiration_date": body.get("expiration_date"),
    }
    # Store by project name (preserve casing as provided)
    ex[project] = rule
    data["version"] = cfg_version
    data["exemptions"] = ex
    # Preserve existing pattern_exemptions
    if "pattern_exemptions" not in data:
        data["pattern_exemptions"] = []

    # Write back
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

    return {"project": project, "rule": rule}


@app.delete("/v1/exemptions/{project}")
async def delete_project_exemption(
    project: str,
    settings: Settings = Depends(get_settings),
    _auth_ok: bool = Depends(require_api_key_non_local),
):
    """Delete an exemption for a specific project."""
    path = _exemptions_path(settings)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Exemptions file not found")

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read exemptions file: {e}")

    ex: Dict[str, Any] = data.get("exemptions") or {}

    # Find and remove the project (case-insensitive match)
    project_key = None
    for key in ex.keys():
        if key.lower() == project.lower():
            project_key = key
            break

    if project_key is None:
        raise HTTPException(status_code=404, detail=f"Exemption for project '{project}' not found")

    # Remove the exemption
    del ex[project_key]
    data["exemptions"] = ex

    # Write back
    try:
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write exemptions file: {e}")

    return {"message": f"Exemption for project '{project_key}' deleted successfully", "project": project_key}
