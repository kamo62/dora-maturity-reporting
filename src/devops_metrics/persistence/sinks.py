from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Protocol

from sqlmodel import Session, select

from .models import Run, RawMetric, Metric


AssessmentResults = Dict[str, Any]


class StorageSink(Protocol):
    def persist(self, results: AssessmentResults, run_context: Dict[str, Any]) -> None:  # pragma: no cover - protocol
        ...


@dataclass
class CompositeSink(StorageSink):
    sinks: List[StorageSink]
    # Propagate DB sink run_id to callers for validation/reporting
    last_run_id: Any = None

    def persist(self, results: AssessmentResults, run_context: Dict[str, Any]) -> None:
        self.last_run_id = None
        for sink in self.sinks:
            sink.persist(results, run_context)
            # If a sink exposes last_run_id, propagate it
            lrid = getattr(sink, "last_run_id", None)
            if lrid is not None:
                self.last_run_id = lrid


class FileSink(StorageSink):
    """Write file artifacts (JSON/Excel) based on collected results.

    Expects the following keys in `results`:
      - raw_ado_data: Dict
      - file_output_base: str (base path without extension)
      - maturity_data: Optional[Dict]
      - project_scores: Optional[Dict]
      - segment_scores: Optional[Dict]
    """

    def persist(self, results: AssessmentResults, run_context: Dict[str, Any]) -> None:
        from pathlib import Path
        import json
        import pandas as pd  # type: ignore

        base = results.get("file_output_base")
        raw = results.get("raw_ado_data") or {}
        maturity = results.get("maturity_data")
        project_scores = results.get("project_scores") or {}
        segment_scores = results.get("segment_scores") or {}

        if not base:
            # Default to timestamped path if not provided
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"exports/{ts}/metrics"

        base_path = Path(base)
        base_path.parent.mkdir(parents=True, exist_ok=True)

        # 1) Raw JSON + Excel
        if raw:
            try:
                from devops_metrics.backend.services.raw_data_exporter import RawDataExporter
                exporter = RawDataExporter(None)
                exporter.export_data(raw, str(base_path.with_suffix(".xlsx")))
            except Exception:
                # Fallback minimal JSON write if exporter failed
                with open(base_path.with_suffix(".json"), "w") as f:
                    json.dump(raw, f, indent=2)

        # 2) Maturity assessment (Excel + JSON)
        if maturity:
            try:
                from devops_metrics.backend.services.maturity_assessment_exporter import (
                    MaturityAssessmentExporter,
                )
                maturity_output = base_path.with_name(f"{base_path.stem}_maturity_assessment").with_suffix(
                    ".xlsx"
                )
                exporter = MaturityAssessmentExporter(None)
                exporter.export_data(maturity, str(maturity_output))
            except Exception:
                # Fallback JSON only
                with open(
                    base_path.with_name(f"{base_path.stem}_maturity_assessment").with_suffix(".json"),
                    "w",
                ) as f:
                    json.dump(maturity, f, indent=2)

        # 3) Segment/export outputs
        if segment_scores or project_scores:
            # JSON payload mirrors CLI structure
            seg_json = {
                "project_scores": project_scores,
                "segment_scores": segment_scores,
                "raw_maturity": maturity,
            }
            seg_json_path = base_path.with_name(f"{base_path.stem}_segments").with_suffix(".json")
            with open(seg_json_path, "w") as f:
                json.dump(seg_json, f, indent=2)

            # Excel: simple two-sheet writer for projects/segments
            try:
                seg_xlsx = base_path.with_name(f"{base_path.stem}_segments").with_suffix(".xlsx")
                proj_rows = []
                for pid, pdata in (project_scores or {}).items():
                    repos = pdata.get("repositories", [])
                    proj_rows.append(
                        {
                            "Project Name": pdata.get("name", pid),
                            "Maturity Score": pdata.get("maturity_score", 0.0),
                            "Number of Repositories": pdata.get("repository_count", 0),
                            "Repositories": "\n".join(f"• {r}" for r in repos),
                        }
                    )
                seg_rows = []
                for seg_name, sdata in (segment_scores or {}).items():
                    seg_rows.append(
                        {
                            "Security Group Name": seg_name,
                            "Maturity Score": sdata.get("maturity_score", 0.0),
                            "Maturity Level": sdata.get("maturity_level"),
                            "Maturity Label": sdata.get("maturity_label"),
                            "Project Count": sdata.get("project_count", 0),
                            "Projects": "\n".join(f"• {p}" for p in sdata.get("projects", [])),
                        }
                    )

                with pd.ExcelWriter(seg_xlsx) as writer:
                    if proj_rows:
                        pd.DataFrame.from_records(proj_rows).to_excel(
                            writer, sheet_name="Project Maturity", index=False
                        )
                    if seg_rows:
                        pd.DataFrame.from_records(seg_rows).to_excel(
                            writer, sheet_name="Segment Maturity", index=False
                        )
            except Exception:
                # If Excel creation fails, we already have JSON
                pass


class PostgresSink(StorageSink):
    def __init__(self, engine):
        self._engine = engine
        self.last_run_id = None

    def _serialize_value(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=timezone.utc)
            return obj.astimezone(timezone.utc).isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return str(obj)
        return obj

    def _sanitize_raw_data(self, raw_data: Any) -> Dict[str, Any]:
        import json
        pii_keys = {
            "email",
            "username",
            "displayname",
            "principalname",
            "token",
            "password",
            "secret",
            "authorization",
            "key",
        }

        def sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                cleaned: Dict[str, Any] = {}
                for k, v in obj.items():
                    if isinstance(k, str) and k.lower() in pii_keys:
                        cleaned[k] = "[REDACTED]"
                    else:
                        cleaned[k] = sanitize(v)
                return cleaned
            if isinstance(obj, list):
                return [sanitize(i) for i in obj]
            return self._serialize_value(obj)

        try:
            cleaned = sanitize(raw_data)
            json.dumps(cleaned)  # ensure serializable
            return cleaned  # type: ignore[return-value]
        except (TypeError, ValueError) as e:
            return {"error": f"Failed to sanitize data: {e}", "raw_string": str(raw_data)}

    def persist(self, results: AssessmentResults, run_context: Dict[str, Any]) -> None:
        """
        Persist a run with:
        - Run row with audit context and executed_at timestamp
        - RawMetric rows for raw_ado_data (sanitized)
        - Metric rows for calculated metrics if provided via results['calculated_metrics']
        """
        from decimal import Decimal, InvalidOperation
        from uuid import uuid4
        from sqlalchemy import text

        now = datetime.now(timezone.utc)
        # Ensure executed_at is timezone-aware
        executed_at_raw = run_context.get("executed_at")
        if isinstance(executed_at_raw, datetime):
            if executed_at_raw.tzinfo is None:
                # Naive datetime - assume UTC
                executed_at = executed_at_raw.replace(tzinfo=timezone.utc)
            else:
                # Aware datetime - convert to UTC
                executed_at = executed_at_raw.astimezone(timezone.utc)
        else:
            executed_at = now
        with Session(self._engine) as session:
            # Attempt application-side deduplication of runs
            existing_run = None
            org_val = run_context.get("org")
            project_val = run_context.get("project")
            repo_val = run_context.get("repo")
            branch_val = run_context.get("branch")
            commit_val = run_context.get("commit_sha")
            run_label_val = run_context.get("run_label")
            run_id_val = run_context.get("run_id")

            # If run_id is provided, try to fetch that specific run first
            if run_id_val:
                try:
                    from uuid import UUID
                    run_uuid = UUID(run_id_val) if isinstance(run_id_val, str) else run_id_val
                    existing_run = session.exec(select(Run).where(Run.id == run_uuid)).first()
                except Exception:
                    # If run_id lookup fails, fall back to deduplication logic
                    existing_run = None

            # Fall back to deduplication logic if run not found by ID
            if not existing_run and commit_val:
                stmt = (
                    select(Run)
                    .where(
                        Run.org == org_val,
                        Run.project == project_val,
                        Run.repo == repo_val,
                        Run.branch == branch_val,
                        Run.commit_sha == commit_val,
                    )
                    .order_by(Run.executed_at.desc())
                )
                existing_run = session.exec(stmt).first()
            elif not existing_run:
                # For runs without commit_sha, use same-day + run_label matching
                start_of_day = executed_at.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = start_of_day + timedelta(days=1)
                stmt = (
                    select(Run)
                    .where(
                        Run.org == org_val,
                        Run.project == project_val,
                        Run.repo == repo_val,
                        Run.branch == branch_val,
                        Run.commit_sha.is_(None),
                        Run.run_label == run_label_val,
                        Run.executed_at >= start_of_day,
                        Run.executed_at < end_of_day,
                    )
                    .order_by(Run.executed_at.desc())
                )
                existing_run = session.exec(stmt).first()

            if existing_run:
                run = existing_run
            else:
                # Create run (prefer atomic upsert for PostgreSQL when commit_sha present)
                dialect = session.get_bind().dialect.name
                if dialect == "postgresql" and commit_val:
                    # Use ON CONFLICT on (org,project,repo,branch,commit_sha)
                    # Use provided run_id if available, otherwise generate new UUID
                    new_id = str(run_id_val) if run_id_val else str(uuid4())
                    stmt = text(
                        """
                        INSERT INTO runs (
                            id, org, project, repo, branch, commit_sha,
                            executed_at, environment, run_label,
                            tool_version, thresholds_version, config_hash, calculation_version
                        ) VALUES (
                            :id, :org, :project, :repo, :branch, :commit_sha,
                            :executed_at, :environment, :run_label,
                            :tool_version, :thresholds_version, :config_hash, :calculation_version
                        )
                        ON CONFLICT (org, project, repo, branch, commit_sha)
                        DO UPDATE SET
                            executed_at = EXCLUDED.executed_at,
                            environment = EXCLUDED.environment,
                            run_label = EXCLUDED.run_label,
                            tool_version = EXCLUDED.tool_version,
                            thresholds_version = EXCLUDED.thresholds_version,
                            config_hash = EXCLUDED.config_hash,
                            calculation_version = EXCLUDED.calculation_version
                        RETURNING id
                        """
                    )
                    params = {
                        "id": new_id,
                        "org": org_val,
                        "project": project_val,
                        "repo": repo_val,
                        "branch": branch_val,
                        "commit_sha": commit_val,
                        "executed_at": executed_at,
                        "environment": run_context.get("environment", "local"),
                        "run_label": run_label_val,
                        "tool_version": run_context.get("tool_version"),
                        "thresholds_version": run_context.get("thresholds_version"),
                        "config_hash": run_context.get("config_hash"),
                        "calculation_version": run_context.get("calculation_version"),
                    }
                    res = session.exec(stmt, params)
                    result_row = res.first()
                    rid = result_row[0] if result_row and len(result_row) > 0 else new_id
                    # Rehydrate a Run instance minimal for downstream use
                    run = Run(
                        id=rid,
                        org=org_val,
                        project=project_val,
                        repo=repo_val,
                        branch=branch_val,
                        commit_sha=commit_val,
                        executed_at=executed_at,
                    )
                else:
                    # Fallback ORM path (and for commitless case)
                    # Use provided run_id if available
                    run_kwargs = {
                        "org": org_val,
                        "project": project_val,
                        "repo": repo_val,
                        "branch": branch_val,
                        "commit_sha": commit_val,
                        "executed_at": executed_at,
                        "environment": run_context.get("environment", "local"),
                        "run_label": run_label_val,
                        "tool_version": run_context.get("tool_version"),
                        "thresholds_version": run_context.get("thresholds_version"),
                        "config_hash": run_context.get("config_hash"),
                        "calculation_version": run_context.get("calculation_version"),
                    }
                    if run_id_val:
                        from uuid import UUID
                        run_kwargs["id"] = UUID(run_id_val) if isinstance(run_id_val, str) else run_id_val
                    run = Run(**run_kwargs)
                    session.add(run)
                    session.flush()  # get run.id

            # Store raw data if present
            raw_data_dict = results.get("raw_ado_data") or {}
            if raw_data_dict:
                sanitized = self._sanitize_raw_data(raw_data_dict)
                raw_metric = RawMetric(
                    run_id=run.id,
                    source_system="ado",
                    source_key="raw_export",
                    raw_data=sanitized,
                    collected_at=now,
                    org=run.org,
                    project=run.project,
                    repo=run.repo,
                )
                session.add(raw_metric)

            # Store calculated metrics if provided
            calc_metrics = results.get("calculated_metrics") or []
            if isinstance(calc_metrics, list) and calc_metrics:
                metric_rows: List[Metric] = []
                for m in calc_metrics:
                    scope = m.get("scope")
                    metric_key = m.get("metric_key") or m.get("key")
                    if not metric_key:
                        continue  # skip malformed entries
                    # Preserve valid 0.0 values; only fall back if None
                    value_float = m.get("value_float") if m.get("value_float") is not None else m.get("metric_value")
                    # Convert floats to Decimal to preserve precision in NUMERIC columns
                    metric_value = None
                    if value_float is not None:
                        try:
                            metric_value = Decimal(str(value_float))
                        except (InvalidOperation, ValueError, TypeError):
                            metric_value = None
                    metric_json = m.get("value_json") or m.get("metric_json")
                    score_val = m.get("score")
                    score = None
                    if score_val is not None:
                        try:
                            score = Decimal(str(score_val))
                        except (InvalidOperation, ValueError, TypeError):
                            score = None
                    status = m.get("status")
                    # Default observed_at to run.executed_at for consistency
                    observed_at = m.get("observed_at") if isinstance(m.get("observed_at"), datetime) else executed_at

                    metric_rows.append(
                        Metric(
                            run_id=run.id,
                            scope=scope,
                            metric_key=metric_key,
                            metric_value=metric_value,
                            metric_json=metric_json,
                            score=score,
                            status=status,
                            calculation_version=run_context.get("calculation_version"),
                            observed_at=observed_at,
                            # denormalized context for query perf
                            org=run.org,
                            project=run.project,
                            repo=run.repo,
                        )
                    )
                if metric_rows:
                    dialect = session.get_bind().dialect.name
                    if dialect == "postgresql":
                        # Upsert metrics using ON CONFLICT
                        insert_stmt = text(
                            """
                            INSERT INTO metrics (
                                id, org, project, repo, run_id, scope, metric_key,
                                metric_value, metric_json, score, status,
                                calculation_version, observed_at
                            ) VALUES (
                                :id, :org, :project, :repo, :run_id, :scope, :metric_key,
                                :metric_value, :metric_json, :score, :status,
                                :calculation_version, :observed_at
                            )
                            ON CONFLICT ON CONSTRAINT uq_metric_per_run
                            DO UPDATE SET
                                metric_value = EXCLUDED.metric_value,
                                metric_json = EXCLUDED.metric_json,
                                score = EXCLUDED.score,
                                status = EXCLUDED.status,
                                calculation_version = EXCLUDED.calculation_version,
                                observed_at = EXCLUDED.observed_at
                            """
                        )
                        # Batch all parameter dictionaries to avoid N+1 queries
                        batch_params = [
                            {
                                "id": str(uuid4()),
                                "org": r.org,
                                "project": r.project,
                                "repo": r.repo,
                                "run_id": str(r.run_id),
                                "scope": r.scope,
                                "metric_key": r.metric_key,
                                "metric_value": r.metric_value,
                                "metric_json": r.metric_json,
                                "score": r.score,
                                "status": r.status,
                                "calculation_version": r.calculation_version,
                                "observed_at": r.observed_at,
                            }
                            for r in metric_rows
                        ]
                        # Execute as batch using connection
                        if batch_params:
                            session.connection().execute(insert_stmt, batch_params)
                    else:
                        session.add_all(metric_rows)

            session.commit()
            # Track last run id for downstream validation/reporting
            self.last_run_id = str(run.id)
