from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def aggregate_common_metrics_from_repositories(repositories: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Aggregate common QA metrics across repositories.
    Expects a dict of repositories where each repo has:
      repo["quality_assurance"]["metrics"]
    Extracts: build_success_rate, code_coverage (as coverage), test_pass_rate
    Returns a dict keyed by metric_key -> mean value
    """
    bsr_vals: List[float] = []
    cov_vals: List[float] = []
    tpr_vals: List[float] = []

    for _rid, rdata in (repositories or {}).items():
        qa = (rdata or {}).get("quality_assurance") or {}
        m = qa.get("metrics") or {}
        bsr = m.get("build_success_rate")
        cov = m.get("code_coverage")
        tpr = m.get("test_pass_rate")
        if isinstance(bsr, (int, float)):
            bsr_vals.append(float(bsr))
        if isinstance(cov, (int, float)):
            cov_vals.append(float(cov))
        if isinstance(tpr, (int, float)):
            tpr_vals.append(float(tpr))

    return {
        "build_success_rate": _mean(bsr_vals),
        "coverage": _mean(cov_vals),
        "test_pass_rate": _mean(tpr_vals),
    }


def aggregate_common_metrics_from_maturity(maturity_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Aggregate metrics from a maturity_data structure that contains repositories map.
    """
    repos = maturity_data.get("repositories", {}) if isinstance(maturity_data, dict) else {}
    if not isinstance(repos, dict):
        return {}
    return aggregate_common_metrics_from_repositories(repos)


def aggregate_common_metrics_from_raw_maturity_container(container: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """
    Find a raw_maturity payload within a container JSON and aggregate metrics.
    The container may directly contain "raw_maturity" or nest it within another object.
    """
    raw_maturity = None
    if isinstance(container, dict):
        if "raw_maturity" in container and isinstance(container["raw_maturity"], dict):
            raw_maturity = container["raw_maturity"]
        else:
            for _, v in container.items():
                if isinstance(v, dict) and "raw_maturity" in v and isinstance(v["raw_maturity"], dict):
                    raw_maturity = v["raw_maturity"]
                    break

    if not raw_maturity or not isinstance(raw_maturity.get("repositories"), dict):
        return {}

    return aggregate_common_metrics_from_repositories(raw_maturity["repositories"]) 

