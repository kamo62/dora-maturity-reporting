"""
Shared runner for metrics collection.

Used by both CLI and API to ensure identical orchestration logic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, cast
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
import logging
import os

from devops_metrics.config.settings import Settings
from devops_metrics.config.access import checked_path, validate_org, validate_target
from devops_metrics.api.models import CollectionConfig
from devops_metrics.backend.api.ado_client import AzureDevOpsClient
from devops_metrics.backend.services.factory import MetricsServiceFactory
from devops_metrics.backend.services.raw_data_collector import RawDataCollector
from devops_metrics.backend.services.raw_maturity_assessment import RawMaturityAssessmentService
from devops_metrics.backend.services.segment_maturity_assessment import SegmentMaturityAssessmentService
from devops_metrics.persistence.models import init_engine_and_tables
from devops_metrics.persistence.sinks import CompositeSink, FileSink, PostgresSink
from devops_metrics.validation.consistency import CalculationValidator
from devops_metrics import __version__ as TOOL_VERSION
from devops_metrics.types import RawData

logger = logging.getLogger(__name__)


class RunResult(dict):
    """Result of a metrics collection run"""


async def execute_collection_run(
    config: CollectionConfig,
    settings: Settings,
    run_id: Optional[str] = None,
) -> RunResult:
    """
    Execute a metrics collection run (normal collection mode).
    Backfill path is deferred to MVP+1.
    """
    # Ensure run_id
    if run_id is None:
        run_id = str(uuid4())

    result: RunResult = RunResult(
        id=run_id,
        queued_at=datetime.now(timezone.utc),
        started_at=None,
        finished_at=None,
        status="queued",
        error=None,
        artifacts_dir=None,
        outputs={},
    )

    # Resolve org
    effective_org = validate_org(config.org or settings.azure_devops_org)
    if config.project:
        validate_target(config.project)
    settings = settings.model_copy(update={"azure_devops_org": effective_org})

    # Output path
    if config.output_path:
        output = checked_path(config.output_path)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project_suffix = f"_{config.project}" if config.project else ""
        output_dir = Path(f"exports/{timestamp}")
        output = output_dir / f"metrics{project_suffix}"
    checked_path(output).parent.mkdir(parents=True, exist_ok=True)

    # Database engine if needed
    engine = None
    if config.write_db or config.backfill_from:
        db_url = settings.database_url or os.getenv("DATABASE_URL")
        if not db_url:
            if config.db_strict:
                result.update(
                    status="failed",
                    error="DATABASE_URL must be set for --write-db",
                    finished_at=datetime.now(timezone.utc),
                )
                return result
            else:
                logger.warning("DATABASE_URL not set; DB writes disabled")
                config.write_db = False
        else:
            try:
                # Always require Alembic-managed schema; do not auto-create tables
                engine = init_engine_and_tables(db_url, allow_create_all=False)
            except Exception as ex:
                if config.db_strict:
                    result.update(status="failed", error=f"Failed to init DB: {ex}", finished_at=datetime.now(timezone.utc))
                    return result
                else:
                    logger.warning(f"DB init failed: {ex}; continuing without DB")
                    config.write_db = False

    # Flip to running
    result["status"] = "running"
    result["started_at"] = datetime.now(timezone.utc)
    # Pre-populate artifacts_dir early so it is available even if final status update is missed
    try:
        result["artifacts_dir"] = str(output.parent)
    except Exception:
        result["artifacts_dir"] = None

    # Store status in DB if engine available
    if engine and config.write_db:
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO runs (
                            id, status, executed_at, started_at, org, project, repo, branch, commit_sha,
                            run_label, environment, tool_version, artifacts_dir
                        )
                        VALUES (
                            :id, 'running', :executed_at, :started_at, :org, :project, :repo, :branch, :commit_sha,
                            :run_label, :env, :version, :artifacts_dir
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            status='running',
                            started_at=:started_at,
                            artifacts_dir=COALESCE(runs.artifacts_dir, EXCLUDED.artifacts_dir)
                        """
                    ),
                    {
                        "id": run_id,
                        "executed_at": result.get("queued_at") or result.get("started_at"),
                        "started_at": result["started_at"],
                        "org": effective_org,
                        "project": config.project,
                        "repo": config.repo,
                        "branch": config.branch,
                        "commit_sha": config.commit_sha,
                        "run_label": config.run_label,
                        "env": settings.run_env,
                        "version": TOOL_VERSION,
                        "artifacts_dir": result.get("artifacts_dir"),
                    },
                )
        except Exception as ex:
            logger.warning(f"Failed to insert run record: {ex}")

    # From this point on, ensure DB engine is disposed on exit
    # Abort backfill in MVP
    if config.backfill_from:
        result.update(status="failed", error="Backfill not implemented in runner (MVP+1)")
        result["finished_at"] = datetime.now(timezone.utc)
        return result

    # Normal collection
    ado_client = AzureDevOpsClient(settings)
    try:
        # Services
        effective_minimal = config.minimal or config.fast
        enable_delta = not config.full
        skip_stale = not config.full
        force_year_window = config.db_reset

        raw_collector = cast(
            RawDataCollector,
            MetricsServiceFactory.create_service(
                "raw",
                ado_client,
                minimal=effective_minimal,
                enable_delta=enable_delta,
                skip_stale_repos=skip_stale,
                force_year_window=force_year_window,
            ),
        )

        # Load exemptions if configured
        exemption_manager = None
        try:
            from devops_metrics.config.exemptions import ExemptionManager  # type: ignore
            ef = getattr(settings, 'exemptions_file', None)
            if ef:
                cfg_path = Path(str(ef))
                if cfg_path.exists():
                    exemption_manager = ExemptionManager(cfg_path)
        except Exception as ex:
            logger.warning(f"Failed to initialize ExemptionManager: {ex}")

        # Collect raw data
        if config.all_projects:
            raw_data_result = await raw_collector.collect_all_projects_raw_data()
        else:
            raw_data_result = await raw_collector.collect_all_raw_data(config.project)
        raw_data_dict: Dict[str, Any] = dict(raw_data_result)
        results_container: Dict[str, Any] = {"raw_ado_data": raw_data_dict}

        # Maturity assessment
        if config.include_maturity:
            maturity_service = cast(
                RawMaturityAssessmentService,
                MetricsServiceFactory.create_service(
                    "raw_maturity",
                    ado_client,
                    current_month_weight=settings.current_month_weight,
                    historical_weight=settings.historical_weight,
                    exemption_manager=exemption_manager,
                ),
            )
            maturity_data = maturity_service.assess_raw_data(cast(RawData, raw_data_dict))
            results_container["maturity_data"] = maturity_data

            # Derive calculated metrics (best-effort)
            try:
                repos = maturity_data.get("repositories", {}) if isinstance(maturity_data, dict) else {}
                if isinstance(repos, dict) and repos:
                    bsr_vals: List[float] = []
                    cov_vals: List[float] = []
                    for _rid, rdata in repos.items():
                        qa = (rdata or {}).get("quality_assurance") or {}
                        m = qa.get("metrics") or {}
                        bsr = m.get("build_success_rate")
                        cov = m.get("code_coverage")
                        if isinstance(bsr, (int, float)):
                            bsr_vals.append(float(bsr))
                        if isinstance(cov, (int, float)):
                            cov_vals.append(float(cov))
                    def _mean(xs: List[float]) -> Optional[float]:
                        return sum(xs) / len(xs) if xs else None
                    calc_list: List[Dict[str, Any]] = results_container.get("calculated_metrics") or []
                    agg_bsr = _mean(bsr_vals)
                    agg_cov = _mean(cov_vals)
                    if agg_bsr is not None:
                        calc_list.append({"scope": None, "metric_key": "build_success_rate", "value_float": agg_bsr})
                    if agg_cov is not None:
                        calc_list.append({"scope": None, "metric_key": "coverage", "value_float": agg_cov})
                    if calc_list:
                        results_container["calculated_metrics"] = calc_list
            except Exception:
                pass

        # Segment assessment
        if config.include_segments:
            if "maturity_data" not in results_container:
                logger.error("Segment assessment requires maturity assessment; enable include_maturity")
                result.update(status="failed", error="Segments require maturity data", finished_at=datetime.now(timezone.utc))
                return result
            segment_service = cast(
                SegmentMaturityAssessmentService,
                MetricsServiceFactory.create_service("segment_maturity", ado_client),
            )
            group_entitlements = await segment_service.get_group_entitlements()
            project_scores = segment_service.calculate_project_maturity(cast(Dict, results_container["maturity_data"]))
            segment_scores = segment_service.calculate_segment_maturity(project_scores, group_entitlements)

            results_container["project_scores"] = project_scores
            results_container["segment_scores"] = segment_scores

            # Map to calculated_metrics for DB
            calculated_metrics: List[Dict[str, Any]] = []
            now_utc = datetime.now(timezone.utc)
            for seg_name, seg_data in segment_scores.items():
                try:
                    maturity_score = float(seg_data.get("maturity_score", 0.0))
                except Exception:
                    logger.warning(f"Failed to convert maturity_score for segment {seg_name}; using None")
                    maturity_score = None  # type: ignore[assignment]
                calculated_metrics.append(
                    {
                        "scope": seg_name,
                        "metric_key": "maturity_score",
                        "value_float": maturity_score,
                        "status": None,
                        "observed_at": now_utc,
                    }
                )
            existing_calc = results_container.get("calculated_metrics", []) or []
            results_container["calculated_metrics"] = existing_calc + calculated_metrics

        # Persistence
        run_context = {
            "run_id": run_id,  # Pass run_id so PostgresSink can update existing run instead of creating duplicate
            "org": effective_org,
            "project": config.project,
            "repo": config.repo,
            "branch": config.branch,
            "commit_sha": config.commit_sha,
            "run_label": config.run_label,
            "environment": settings.run_env,
            "tool_version": TOOL_VERSION,
            "calculation_version": None,
        }

        if config.write_db and engine:
            # Destructive reset - only in local/dev environments
            if config.db_reset:
                run_env = getattr(settings, 'run_env', 'production').lower()
                if run_env not in ('local', 'development', 'dev'):
                    error_msg = f"DB reset (TRUNCATE CASCADE) is not allowed in environment '{run_env}'. Only local/dev/development environments are permitted."
                    logger.error(error_msg)
                    result.update(status="failed", error=error_msg, finished_at=datetime.now(timezone.utc))
                    return result
                logger.warning(f"TRUNCATE CASCADE requested in {run_env} environment - proceeding with destructive reset")
                from sqlalchemy import text
                with engine.begin() as conn:
                    conn.execute(text("TRUNCATE TABLE metrics, raw_metrics, runs RESTART IDENTITY CASCADE;"))

            # Build composite sink
            sinks: List[Any] = []
            if config.write_files:
                sinks.append(FileSink())
            sinks.append(PostgresSink(engine))
            sink = CompositeSink(sinks)
        elif config.write_files:
            sink = FileSink()
        else:
            sink = None

        if sink:
            payload = {
                "raw_ado_data": results_container.get("raw_ado_data"),
                "file_output_base": str(output),
                "maturity_data": results_container.get("maturity_data"),
                "project_scores": results_container.get("project_scores"),
                "segment_scores": results_container.get("segment_scores"),
                "calculated_metrics": results_container.get("calculated_metrics", []),
            }
            sink.persist(payload, run_context)

        # Optional validation
        if config.validate_calculations:
            dbu = settings.database_url or os.getenv("DATABASE_URL")
            if dbu:
                validator = CalculationValidator(dbu)
                seg_scores = results_container.get("segment_scores", {})
                vr = validator.validate_segments(run_context, seg_scores)
                inconsistencies = [r for r in vr if not r.is_consistent]
                if inconsistencies:
                    result.update(status="failed", error=f"Validation failed: {len(inconsistencies)} inconsistencies")
                    result["finished_at"] = datetime.now(timezone.utc)
                    return result
            else:
                logger.warning("Skipping validation: DATABASE_URL not set")

        # Success
        result.update(
            status="succeeded",
            finished_at=datetime.now(timezone.utc),
            artifacts_dir=str(output.parent),
            outputs={
                "raw_json": str(output.with_suffix(".json")),
                "raw_excel": str(output.with_suffix(".xlsx")),
                "maturity_assessment": str(output.with_name(f"{output.stem}_maturity_assessment").with_suffix(".json"))
                if config.include_maturity
                else None,
                "segments": str(output.with_name(f"{output.stem}_segments").with_suffix(".json"))
                if config.include_segments
                else None,
            },
        )

        # Update DB
        if engine and config.write_db:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE runs
                        SET status='succeeded', finished_at=:finished_at, artifacts_dir=:artifacts_dir
                        WHERE id=:id
                        """
                    ),
                    {
                        "id": result["id"],
                        "finished_at": result["finished_at"],
                        "artifacts_dir": result["artifacts_dir"],
                    },
                )

        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"Run {run_id} failed: {e}", exc_info=True)
        result.update(status="failed", error=str(e), finished_at=datetime.now(timezone.utc))
        if engine and config.write_db:
            try:
                from sqlalchemy import text
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE runs
                            SET status='failed', finished_at=:finished_at, error=:error
                            WHERE id=:id
                            """
                        ),
                        {"id": run_id, "finished_at": result["finished_at"], "error": result["error"]},
                    )
            except Exception:
                pass
        return result
    finally:
        # Close ADO client with timeout
        try:
            import asyncio
            await asyncio.wait_for(ado_client.close(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("ADO client close() timed out after 30s")
        except Exception as e:
            logger.error(f"Error closing ADO client: {e}")

        # Dispose engine if it was created
        if engine is not None:
            try:
                engine.dispose()
            except Exception as e:
                logger.error(f"Error disposing engine: {e}")
