"""Collaboration metrics assessor."""

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, cast, Optional, TypedDict, Literal

from ....types.assessment_types import (
    AssessmentResult,
    BuildTestData
)
from ..scoring.scoring_service import ScoringService
from ....types.data_types import RepositoryData
from ....utils.logging import get_logger
from devops_metrics.config.exemptions import ExemptionManager

logger = get_logger(__name__)

# Type aliases for level keys
LevelKey = Literal["level1", "level2", "level3", "level4", "level5"]


class LevelThresholds(TypedDict):
    """Level thresholds for metrics."""
    level1: float
    level2: float
    level3: float
    level4: float
    level5: float


def get_level_key(level: int) -> LevelKey:
    """Convert level number to level key.
    
    Args:
        level: Level number (1-5)
        
    Returns:
        Level key (e.g., "level1")
    """
    keys: Dict[int, LevelKey] = {
        1: "level1",
        2: "level2",
        3: "level3",
        4: "level4",
        5: "level5"
    }
    return keys[level]


def get_level_threshold(thresholds: LevelThresholds, level: int) -> float:
    """Get threshold value for a specific level.
    
    Args:
        thresholds: Level thresholds
        level: Level number (1-5)
        
    Returns:
        Threshold value for the level
    """
    key = get_level_key(level)
    return thresholds[key]


class CollaborationMetricsAssessor:
    """Assessor for collaboration metrics."""

    def __init__(self, scoring_service: ScoringService, exemption_manager: Optional[ExemptionManager] = None) -> None:
        """Initialize the assessor.
        
        Args:
            scoring_service: Service for calculating scores
        """
        self.scoring = scoring_service
        self.exemption_manager = exemption_manager
        self.required_metrics = {
            "merge_rate": 0.5,
            "review_coverage": 0.5
        }
        self.thresholds: Dict[str, LevelThresholds] = {
            "merge_rate": {
                "level1": 50.0,   # 50% merge rate
                "level2": 65.0,   # 65% merge rate
                "level3": 75.0,   # 75% merge rate
                "level4": 85.0,   # 85% merge rate
                "level5": 95.0    # 95% merge rate
            },
            "review_coverage": {
                "level1": 40.0,   # 40% PRs reviewed
                "level2": 60.0,   # 60% PRs reviewed
                "level3": 75.0,   # 75% PRs reviewed
                "level4": 90.0,   # 90% PRs reviewed
                "level5": 98.0    # 98% PRs reviewed
            }
        }

    def assess(
        self,
        repo_data: RepositoryData,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData],
        project_name: Optional[str] = None,
        repo_name: Optional[str] = None,
    ) -> AssessmentResult:
        """Assess collaboration metrics.
        
        Args:
            repo_data: Repository data
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            Assessment result with scores and explanations
        """
        pull_requests = cast(List[Dict[str, Any]], repo_data.get('pull_requests', []))
        if not pull_requests:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": ["No pull request data available"]
            }

        scores: List[Tuple[float, float]] = []
        total_weight = 0.0
        missing_metrics: List[Tuple[str, float]] = []
        explanation: List[str] = []
        metrics: Dict[str, Any] = {}

        # Exemptions filtering and weight redistribution
        ex_set: set[str] = set()
        try:
            if getattr(self, 'exemption_manager', None) and project_name:
                if self.exemption_manager.is_metric_exempt(project_name, 'merge_rate', repo_name):
                    ex_set.add('merge_rate')
                if self.exemption_manager.is_metric_exempt(project_name, 'review_coverage', repo_name):
                    ex_set.add('review_coverage')
        except Exception as e:
            logger.warning(f"Failed to check exemptions: {e}", exc_info=True)
        if ex_set:
            for m in sorted(ex_set):
                reason = None
                try:
                    if self.exemption_manager and project_name:
                        reason = self.exemption_manager.get_exemption_reason(project_name, m)
                    else:
                        reason = None
                except Exception as e:
                    logger.warning(f"Failed to get exemption reason for {project_name}, metric={m}: {e}")
                    reason = None
                explanation.append(f"{m} is exempt: {reason or 'policy exemption'}")
        active_required = {k: v for k, v in self.required_metrics.items() if k not in ex_set}
        if not active_required:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": explanation + ["All metrics in collaboration_metrics are exempt"],
            }
        total_w = sum(active_required.values())
        weight_map = {k: (v / total_w if total_w > 0 else 0.0) for k, v in active_required.items()}

        # Calculate merge rate (50%)
        if 'merge_rate' in weight_map:
            merge_rate = self._calculate_merge_rate(pull_requests)
            if merge_rate is not None:
                metrics['merge_rate'] = merge_rate
                merge_score = self.scoring.calculate_metric_score(
                    [(merge_rate, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['merge_rate'])
                )
                weight = weight_map['merge_rate']
                scores.append((merge_score, weight))
                total_weight += weight
                
                level = self._determine_level(merge_rate, self.thresholds['merge_rate'])
                explanation.append(f"Merge Rate ({merge_rate:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("merge_rate", weight_map['merge_rate']))
                explanation.append("Merge Rate metric missing")

        # Calculate review coverage (50%)
        if 'review_coverage' in weight_map:
            review_coverage = self._calculate_review_coverage(pull_requests)
            if review_coverage is not None:
                metrics['review_coverage'] = review_coverage
                coverage_score = self.scoring.calculate_metric_score(
                    [(review_coverage, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['review_coverage'])
                )
                weight = weight_map['review_coverage']
                scores.append((coverage_score, weight))
                total_weight += weight
                
                level = self._determine_level(review_coverage, self.thresholds['review_coverage'])
                explanation.append(f"Review Coverage ({review_coverage:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("review_coverage", weight_map['review_coverage']))
                explanation.append("Review Coverage metric missing")

        # Calculate final score
        if scores:
            base_score = sum(score * weight for score, weight in scores) / total_weight if total_weight > 0 else 0.0
            final_score = self.scoring.apply_missing_metrics_penalty(base_score, missing_metrics, explanation, set(ex_set))
        else:
            final_score = 0.0
            explanation.append("No valid metrics available")

        return {
            "score": final_score,
            "level": self.scoring.determine_maturity_level(final_score),
            "metrics": metrics,
            "explanation": explanation
        }

    def _determine_level(
        self,
        value: float,
        thresholds: LevelThresholds,
        reverse: bool = False
    ) -> int:
        """Determine the level based on value and thresholds.
        
        Args:
            value: Metric value
            thresholds: Level thresholds
            reverse: If True, lower values are better
            
        Returns:
            Level (1-5)
        """
        if reverse:
            for level in range(5, 0, -1):
                if value <= get_level_threshold(thresholds, level):
                    return level
        else:
            for level in range(5, 0, -1):
                if value >= get_level_threshold(thresholds, level):
                    return level
        return 1

    def _calculate_merge_rate(
        self,
        pull_requests: List[Dict[str, Any]]
    ) -> Optional[float]:
        """Calculate PR merge rate percentage.
        
        Args:
            pull_requests: List of pull requests
            
        Returns:
            Merge rate percentage or None if no data
        """
        if not pull_requests:
            return None
            
        total_prs = len(pull_requests)
        merged_prs = sum(
            1 for pr in pull_requests
            if pr.get('status', '').lower() == 'completed'
        )
        
        if total_prs > 0:
            return (merged_prs / total_prs) * 100.0
            
        return None

    def _calculate_review_coverage(
        self,
        pull_requests: List[Dict[str, Any]]
    ) -> Optional[float]:
        """Calculate PR review coverage percentage.
        
        Args:
            pull_requests: List of pull requests
            
        Returns:
            Review coverage percentage or None if no data
        """
        if not pull_requests:
            return None
            
        total_prs = len(pull_requests)
        reviewed_prs = sum(
            1 for pr in pull_requests
            if len(pr.get('reviewers', [])) > 0
        )
        
        if total_prs > 0:
            return (reviewed_prs / total_prs) * 100.0
            
        return None
