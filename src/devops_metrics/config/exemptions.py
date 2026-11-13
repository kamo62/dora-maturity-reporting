from __future__ import annotations

from datetime import date

from pathlib import Path
from typing import Dict, List, Optional, Set
import logging
import fnmatch

import yaml
from pydantic import BaseModel, Field, validator


class ExemptionRule(BaseModel):
    level: str  # "metric", "dimension", "mixed", "repository"
    exempt_metrics: List[str] = Field(default_factory=list)
    exempt_dimensions: List[str] = Field(default_factory=list)
    repositories: List[Dict] = Field(default_factory=list)
    reason: str
    owner: str
    effective_date: date
    expiration_date: Optional[date] = None

    @validator("level")
    def _validate_level(cls, v: str) -> str:
        valid = {"metric", "dimension", "mixed", "repository"}
        if v not in valid:
            raise ValueError(f"level must be one of {valid}")
        return v


class PatternExemption(BaseModel):
    pattern: str
    exempt_metrics: List[str] = Field(default_factory=list)
    exempt_dimensions: List[str] = Field(default_factory=list)
    reason: str
    owner: str
    expiration_date: Optional[date] = None


class ExemptionConfig(BaseModel):
    version: str
    exemptions: Dict[str, ExemptionRule] = Field(default_factory=dict)
    pattern_exemptions: List[PatternExemption] = Field(default_factory=list)


def _is_rule_active(rule: BaseModel, check_date: Optional[date]) -> bool:
    """Return True if the rule is active at check_date (or today)."""
    cd = check_date or date.today()
    eff = getattr(rule, "effective_date", None)
    exp = getattr(rule, "expiration_date", None)
    if eff and eff > cd:
        return False
    if exp and cd > exp:
        return False
    return True


class ExemptionManager:
    """Loads and evaluates project exemptions from YAML.

    Foundation implementation (MVP): safe defaults when file missing/invalid.
    """

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.config: Optional[ExemptionConfig] = None
        self._metric_cache: Dict[tuple, bool] = {}
        self._dimension_cache: Dict[tuple, bool] = {}
        self._load_config()

    def _load_config(self) -> None:
        try:
            if not self.config_path.exists():
                self.config = ExemptionConfig(version="1.0", exemptions={}, pattern_exemptions=[])
                return
            data = yaml.safe_load(self.config_path.read_text()) or {}
            self.config = ExemptionConfig(**data)
        except Exception as e:
            # Fail-safe: no exemptions, but log error
            logging.error(f"Failed to load exemption config from {self.config_path}: {e}")
            self.config = ExemptionConfig(version="1.0", exemptions={}, pattern_exemptions=[])

    def invalidate_cache(self) -> None:
        self._metric_cache.clear()
        self._dimension_cache.clear()

    def is_metric_exempt(
        self,
        project_name: str,
        metric_name: str,
        repo_name: Optional[str] = None,
        check_date: Optional[date] = None,
    ) -> bool:
        key = ((project_name or "").lower(), metric_name, repo_name or "", (check_date or date.today()).isoformat())
        if key in self._metric_cache:
            return self._metric_cache[key]
        cfg = self.config or ExemptionConfig(version="1.0")
        proj_key = (project_name or "").lower()
        result = False
        # Direct project rule
        for name, rule in (cfg.exemptions or {}).items():
            if name.lower() == proj_key:
                if not _is_rule_active(rule, check_date):
                    continue
                if rule.exempt_metrics and metric_name in rule.exempt_metrics:
                    result = True
                    break
                if repo_name and rule.level in ("repository", "mixed"):
                    for r in rule.repositories or []:
                        if r.get("name") == repo_name and metric_name in (r.get("exempt_metrics") or []):
                            result = True
                            break
        if not result:
            # Pattern rules
            for pat in (cfg.pattern_exemptions or []):
                if _is_rule_active(pat, check_date) and fnmatch.fnmatch(project_name or "", pat.pattern):
                    if metric_name in (pat.exempt_metrics or []):
                        result = True
                        break
        self._metric_cache[key] = result
        return result

    def is_dimension_exempt(
        self,
        project_name: str,
        dimension_name: str,
        check_date: Optional[date] = None,
    ) -> bool:
        key = ((project_name or "").lower(), dimension_name, (check_date or date.today()).isoformat())
        if key in self._dimension_cache:
            return self._dimension_cache[key]
        cfg = self.config or ExemptionConfig(version="1.0")
        proj_key = (project_name or "").lower()
        result = False
        for name, rule in (cfg.exemptions or {}).items():
            if name.lower() == proj_key:
                if not _is_rule_active(rule, check_date):
                    continue
                if rule.exempt_dimensions and dimension_name in rule.exempt_dimensions:
                    result = True
                    break
        if not result:
            for pat in (cfg.pattern_exemptions or []):
                if _is_rule_active(pat, check_date) and fnmatch.fnmatch(project_name or "", pat.pattern):
                    if dimension_name in (pat.exempt_dimensions or []):
                        result = True
                        break
        self._dimension_cache[key] = result
        return result

    def get_exempt_metrics_for_project(self, project_name: str, repo_name: Optional[str] = None) -> Set[str]:
        cfg = self.config or ExemptionConfig(version="1.0")
        out: Set[str] = set()
        proj_key = (project_name or "").lower()
        for name, rule in (cfg.exemptions or {}).items():
            if name.lower() == proj_key:
                if _is_rule_active(rule, None):
                    out.update(rule.exempt_metrics or [])
                if repo_name and rule.level in ("repository", "mixed"):
                    for r in rule.repositories or []:
                        if r.get("name") == repo_name and _is_rule_active(rule, None):
                            out.update((r.get("exempt_metrics") or []))
        return out

    def get_exempt_dimensions_for_project(self, project_name: str) -> Set[str]:
        cfg = self.config or ExemptionConfig(version="1.0")
        out: Set[str] = set()
        proj_key = (project_name or "").lower()
        for name, rule in (cfg.exemptions or {}).items():
            if name.lower() == proj_key:
                if _is_rule_active(rule, None):
                    out.update(rule.exempt_dimensions or [])
        for pat in (cfg.pattern_exemptions or []):
            if _is_rule_active(pat, None) and fnmatch.fnmatch(project_name or "", pat.pattern):
                out.update(pat.exempt_dimensions or [])
        return out

    def get_exemption_reason(self, project_name: str, metric_or_dimension: str) -> Optional[str]:
        cfg = self.config or ExemptionConfig(version="1.0")
        proj_key = (project_name or "").lower()
        for name, rule in (cfg.exemptions or {}).items():
            if name.lower() == proj_key:
                if _is_rule_active(rule, None) and (metric_or_dimension in (rule.exempt_metrics or []) or metric_or_dimension in (rule.exempt_dimensions or [])):
                    return rule.reason
        # Pattern support
        for pat in (cfg.pattern_exemptions or []):
            if _is_rule_active(pat, None) and fnmatch.fnmatch(project_name or "", pat.pattern):
                if metric_or_dimension in (pat.exempt_metrics or []) or metric_or_dimension in (pat.exempt_dimensions or []):
                    return pat.reason
        return None
