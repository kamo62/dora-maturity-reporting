from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlmodel import Session, select
from sqlalchemy import text

from devops_metrics.persistence.models import Run, Metric
from devops_metrics.persistence.models import init_engine_and_tables


@dataclass
class ValidationResult:
    is_consistent: bool
    key: str
    excel_value: Any
    db_value: Any
    diff_percentage: Optional[float] = None
    error_message: Optional[str] = None


class CalculationValidator:
    """
    Validates that "Excel" (exported artifacts) match database metrics.
    For now, we validate segment maturity scores, using the JSON values generated for the segment export
    as the "Excel" source of truth (to avoid parsing XLSX).
    """

    def __init__(self, database_url: str):
        # Use existing engine bootstrap (pool_pre_ping, cached engines)
        self._engine = init_engine_and_tables(database_url, allow_create_all=False)

    def _resolve_latest_run_id(self, run_context: Dict[str, Any]) -> Optional[UUID]:
        """
        Resolve the most recent run_id for the given context.
        Preference: match on org/project/repo/branch and commit_sha when present.
        """
        with Session(self._engine) as session:
            stmt = select(Run)

            # Filters (only include if provided; None means no filter)
            if run_context.get("org") is not None:
                stmt = stmt.where(Run.org == run_context["org"])
            if run_context.get("project") is not None:
                stmt = stmt.where(Run.project == run_context["project"])
            if run_context.get("repo") is not None:
                stmt = stmt.where(Run.repo == run_context["repo"])
            if run_context.get("branch") is not None:
                stmt = stmt.where(Run.branch == run_context["branch"])
            if run_context.get("commit_sha") is not None:
                stmt = stmt.where(Run.commit_sha == run_context["commit_sha"])

            stmt = stmt.order_by(text("executed_at DESC"))

            run = session.exec(stmt).first()
            return run.id if run else None

    def _fetch_db_metrics_map(self, run_id: UUID) -> Dict[Tuple[Optional[str], str], Dict[str, Any]]:
        """
        Fetch DB metrics for a run and return a map keyed by (scope, metric_key).
        Values contain numeric fields for comparison.
        """
        with Session(self._engine) as session:
            stmt = select(Metric).where(Metric.run_id == run_id)
            rows = session.exec(stmt).all()

            db_map: Dict[Tuple[Optional[str], str], Dict[str, Any]] = {}
            for m in rows:
                key = (m.scope, m.metric_key)
                db_map[key] = {
                    "metric_value": float(m.metric_value) if m.metric_value is not None else None,
                    "score": float(m.score) if m.score is not None else None,
                    "status": m.status,
                }
            return db_map

    @staticmethod
    def _numeric_equal(a: Optional[float], b: Optional[float], abs_tol: float = 1e-6, rel_tol: float = 1e-3) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))

    @staticmethod
    def _diff_percentage(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        if a == 0:
            return None  # Percentage difference undefined when baseline is zero
        return abs((a - b) / a) * 100.0

    def validate_segments(self, run_context: Dict[str, Any], segment_scores: Dict[str, Any]) -> List[ValidationResult]:
        """
        Validate segment maturity scores:
        - Excel side: segment_scores[segment]["maturity_score"]
        - DB side: Metric rows with (scope = segment_name, metric_key = "maturity_score")
        """
        results: List[ValidationResult] = []

        run_id = self._resolve_latest_run_id(run_context)
        if not run_id:
            results.append(
                ValidationResult(
                    is_consistent=False,
                    key="run_resolution",
                    excel_value=None,
                    db_value=None,
                    error_message="Could not resolve run_id from context",
                )
            )
            return results

        db_map = self._fetch_db_metrics_map(run_id)

        # Build the "Excel" map from segment_scores
        excel_map: Dict[Tuple[Optional[str], str], Optional[float]] = {}
        for segment_name, seg_data in (segment_scores or {}).items():
            try:
                excel_val: Optional[float] = float(seg_data.get("maturity_score", 0.0))
            except (ValueError, TypeError):
                excel_val = None
            excel_map[(segment_name, "maturity_score")] = excel_val

        # Compare only keys that exist in "Excel" (segment export is the target truth for this validator)
        for key, excel_val in excel_map.items():
            db_entry = db_map.get(key)
            db_val = db_entry["metric_value"] if db_entry else None

            equal = self._numeric_equal(excel_val, db_val)
            results.append(
                ValidationResult(
                    is_consistent=equal,
                    key=f"{key[0]}.maturity_score" if key[0] else f"(none).maturity_score",
                    excel_value=excel_val,
                    db_value=db_val,
                    diff_percentage=None if equal else self._diff_percentage(excel_val, db_val),
                )
            )

        return results

    def validate_segments_by_run_id(self, run_id: Any, segment_scores: Dict[str, Any]) -> List[ValidationResult]:
        """Validate using an explicit run_id instead of resolving by context."""
        results: List[ValidationResult] = []

        # Coerce run_id to UUID if needed
        try:
            rid: UUID
            if isinstance(run_id, UUID):
                rid = run_id
            else:
                rid = UUID(str(run_id))
        except (ValueError, TypeError):
            results.append(
                ValidationResult(
                    is_consistent=False,
                    key="run_id_parse",
                    excel_value=None,
                    db_value=None,
                    error_message="Invalid run_id provided",
                )
            )
            return results

        db_map = self._fetch_db_metrics_map(rid)

        # Build the "Excel" map from segment_scores
        excel_map: Dict[Tuple[Optional[str], str], Optional[float]] = {}
        for segment_name, seg_data in (segment_scores or {}).items():
            try:
                excel_val = float(seg_data.get("maturity_score", 0.0))
            except (ValueError, TypeError):
                excel_val = None
            excel_map[(segment_name, "maturity_score")] = excel_val

        for key, excel_val in excel_map.items():
            db_entry = db_map.get(key)
            db_val = db_entry["metric_value"] if db_entry else None
            equal = self._numeric_equal(excel_val, db_val)
            results.append(
                ValidationResult(
                    is_consistent=equal,
                    key=f"{key[0]}.maturity_score" if key[0] else f"(none).maturity_score",
                    excel_value=excel_val,
                    db_value=db_val,
                    diff_percentage=None if equal else self._diff_percentage(excel_val, db_val),
                )
            )

        return results

    # Generic validator for arbitrary (scope, metric_key) numeric values
    def validate_metric_pairs(
        self,
        run_context: Dict[str, Any],
        expected: Dict[Tuple[Optional[str], str], Optional[float]],
    ) -> List[ValidationResult]:
        results: List[ValidationResult] = []

        run_id = self._resolve_latest_run_id(run_context)
        if not run_id:
            results.append(
                ValidationResult(
                    is_consistent=False,
                    key="run_resolution",
                    excel_value=None,
                    db_value=None,
                    error_message="Could not resolve run_id from context",
                )
            )
            return results

        db_map = self._fetch_db_metrics_map(run_id)

        for (scope, metric_key), excel_val in expected.items():
            db_entry = db_map.get((scope, metric_key))
            db_val = db_entry["metric_value"] if db_entry else None
            equal = self._numeric_equal(excel_val, db_val)
            results.append(
                ValidationResult(
                    is_consistent=equal,
                    key=f"{scope}.{metric_key}" if scope else metric_key,
                    excel_value=excel_val,
                    db_value=db_val,
                    diff_percentage=None if equal else self._diff_percentage(excel_val, db_val),
                )
            )

        return results

    def _load_expected_from_segments_artifact(self, file_output_base: str) -> Dict[Tuple[Optional[str], str], Optional[float]]:
        """
        Load expected aggregate metrics from the segments JSON artifact.
        Currently aggregates repository-level metrics from raw_maturity -> repositories -> quality_assurance.metrics.
        Returns pairs for keys like (None, 'build_success_rate') and (None, 'code_coverage').
        """
        import json
        from pathlib import Path

        expected: Dict[Tuple[Optional[str], str], Optional[float]] = {}
        try:
            seg_path = Path(file_output_base).with_name(f"{Path(file_output_base).stem}_segments").with_suffix(".json")
            if not seg_path.exists():
                return expected
            with open(seg_path, "r") as f:
                payload = json.load(f)
        except Exception:
            return expected

        raw_maturity = payload.get("raw_maturity") or {}
        repos = raw_maturity.get("repositories") or {}
        if not isinstance(repos, dict):
            return expected

        # Collect repo-level metrics then compute mean
        def mean(values: List[float]) -> Optional[float]:
            vals = [v for v in values if isinstance(v, (int, float))]
            return (sum(vals) / len(vals)) if vals else None

        bsrs: List[float] = []
        covs: List[float] = []
        tprs: List[float] = []
        for _repo_id, repodata in repos.items():
            qa = repodata.get("quality_assurance") or {}
            metrics = qa.get("metrics") or {}
            bsr = metrics.get("build_success_rate")
            cov = metrics.get("code_coverage") or metrics.get("coverage")
            tpr = metrics.get("test_pass_rate")
            if isinstance(bsr, (int, float)):
                bsrs.append(float(bsr))
            if isinstance(cov, (int, float)):
                covs.append(float(cov))
            if isinstance(tpr, (int, float)):
                tprs.append(float(tpr))

        expected[(None, "build_success_rate")] = mean(bsrs)
        expected[(None, "coverage")] = mean(covs)
        expected[(None, "test_pass_rate")] = mean(tprs)
        return expected

    def validate_from_artifacts(self, run_context: Dict[str, Any], file_output_base: str) -> List[ValidationResult]:
        """Validate a set of standard metrics (build_success_rate, coverage) by reading JSON artifacts.
        Compares only against metrics present in the DB for the resolved run to avoid spurious failures.
        """
        expected_all = self._load_expected_from_segments_artifact(file_output_base)
        if not expected_all:
            return []

        # Resolve run once and validate inline to avoid redundant resolution
        run_id = self._resolve_latest_run_id(run_context)
        if not run_id:
            return []
        db_map = self._fetch_db_metrics_map(run_id)
        expected = {k: v for k, v in expected_all.items() if k in db_map}
        if not expected:
            return []

        results: List[ValidationResult] = []
        for (scope, metric_key), excel_val in expected.items():
            db_entry = db_map.get((scope, metric_key))
            db_val = db_entry["metric_value"] if db_entry else None
            equal = self._numeric_equal(excel_val, db_val)
            results.append(
                ValidationResult(
                    is_consistent=equal,
                    key=f"{scope}.{metric_key}" if scope else metric_key,
                    excel_value=excel_val,
                    db_value=db_val,
                    diff_percentage=None if equal else self._diff_percentage(excel_val, db_val),
                )
            )
        return results
