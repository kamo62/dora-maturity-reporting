from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import yaml  # type: ignore[import-untyped]

from ..types.validation_types import ValidationIssue, ValidationReport


@dataclass(frozen=True)
class ThresholdLevels:
    level1: float
    level2: float
    level3: float
    level4: float
    level5: float


@dataclass(frozen=True)
class MetricThresholdsConfig:
    name: str
    reverse: bool
    levels: ThresholdLevels


@dataclass(frozen=True)
class ThresholdsConfig:
    version: str
    metrics: Dict[str, MetricThresholdsConfig]


_DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"


def _as_levels(d: Dict[str, Any], path: List[str], issues: List[ValidationIssue]) -> Optional[ThresholdLevels]:
    required = ["level1", "level2", "level3", "level4", "level5"]
    for k in required:
        if k not in d:
            issues.append({"code": "missing_level", "message": f"Missing {k}", "path": [*path, k]})
            return None
        if not isinstance(d[k], (int, float)):
            issues.append({"code": "type_error", "message": f"{k} must be number", "path": [*path, k]})
            return None
    return ThresholdLevels(
        level1=float(d["level1"]),
        level2=float(d["level2"]),
        level3=float(d["level3"]),
        level4=float(d["level4"]),
        level5=float(d["level5"]),
    )


def validate_thresholds_dict(raw: Dict[str, Any]) -> ValidationReport:
    issues: List[ValidationIssue] = []

    if "version" not in raw or not isinstance(raw["version"], str):
        issues.append({"code": "missing_version", "message": "version must be a string", "path": ["version"]})

    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        issues.append({"code": "missing_metrics", "message": "metrics must be a dict", "path": ["metrics"]})
        return {"ok": False, "issues": issues}

    for metric_key, metric_cfg in metrics.items():
        mpath = ["metrics", metric_key]
        if not isinstance(metric_cfg, dict):
            issues.append({"code": "type_error", "message": "metric config must be a dict", "path": mpath})
            continue

        reverse = metric_cfg.get("reverse")
        if not isinstance(reverse, bool):
            issues.append({"code": "type_error", "message": "reverse must be bool", "path": [*mpath, "reverse"]})

        levels_dict = metric_cfg.get("levels")
        if not isinstance(levels_dict, dict):
            issues.append({"code": "type_error", "message": "levels must be dict", "path": [*mpath, "levels"]})
            continue

        levels = _as_levels(levels_dict, [*mpath, "levels"], issues)
        if levels is None:
            # issues already populated
            continue

        # Monotonicity and bounds: assuming percentage-like metrics 0-100 for now
        if reverse:
            if not (levels.level1 > levels.level2 > levels.level3 > levels.level4 > levels.level5):
                issues.append(
                    {"code": "monotonicity", "message": "reverse levels must be strictly decreasing", "path": [*mpath, "levels"]}
                )
        else:
            if not (levels.level1 < levels.level2 < levels.level3 < levels.level4 < levels.level5):
                issues.append(
                    {"code": "monotonicity", "message": "levels must be strictly increasing", "path": [*mpath, "levels"]}
                )
        # Bounds check [0,100]
        for k, v in (
            ("level1", levels.level1),
            ("level2", levels.level2),
            ("level3", levels.level3),
            ("level4", levels.level4),
            ("level5", levels.level5),
        ):
            if not (0.0 <= v <= 100.0):
                issues.append({"code": "bounds", "message": f"{k} must be within [0,100]", "path": [*mpath, "levels", k]})

    return {"ok": len(issues) == 0, "issues": issues}


def load_thresholds(path: Optional[Path] = None, validate: bool = True) -> ThresholdsConfig:
    """
    Load thresholds YAML into a structured ThresholdsConfig.
    Raises ValueError if validation fails when validate=True.
    """
    thresholds_path = path or _DEFAULT_THRESHOLDS_PATH
    with thresholds_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if validate:
        report = validate_thresholds_dict(raw)
        if not report["ok"]:
            msgs = "; ".join(f"{i['code']}@{'/'.join(i['path'])}: {i['message']}" for i in report["issues"])
            raise ValueError(f"Invalid thresholds configuration: {msgs}")

    version = str(raw.get("version", "0"))
    metrics_raw = raw.get("metrics", {})
    metrics: Dict[str, MetricThresholdsConfig] = {}

    for key, cfg in metrics_raw.items():
        levels_dict = cfg.get("levels", {})
        levels = ThresholdLevels(
            level1=float(levels_dict.get("level1", 0.0)),
            level2=float(levels_dict.get("level2", 0.0)),
            level3=float(levels_dict.get("level3", 0.0)),
            level4=float(levels_dict.get("level4", 0.0)),
            level5=float(levels_dict.get("level5", 0.0)),
        )
        metrics[key] = MetricThresholdsConfig(
            name=str(cfg.get("name", key)),
            reverse=bool(cfg.get("reverse", False)),
            levels=levels,
        )

    return ThresholdsConfig(version=version, metrics=metrics)
