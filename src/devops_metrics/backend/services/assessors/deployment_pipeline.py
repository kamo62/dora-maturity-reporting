"""Deployment pipeline assessor."""
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, cast, Optional, TypedDict, Literal

from ....types.assessment_types import (
    AssessmentResult,
    BuildTestData,
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


class DeploymentPipelineAssessor:
    """Assessor for deployment pipeline metrics."""

    def __init__(self, scoring_service: ScoringService, exemption_manager: Optional[ExemptionManager] = None) -> None:
        """Initialize the assessor.
        
        Args:
            scoring_service: Service for calculating scores
        """
        self.scoring = scoring_service
        self.exemption_manager = exemption_manager
        self.required_metrics = {
            "deployment_frequency": 0.4,
            "build_time": 0.3,
            "pipeline_stability": 0.3
        }
        self.thresholds: Dict[str, LevelThresholds] = {
            "deployment_frequency": {
                "level1": 1.0,    # Monthly
                "level2": 4.0,    # Weekly
                "level3": 15.0,   # Few times per week
                "level4": 30.0,   # Daily
                "level5": 60.0    # Multiple times per day
            },
            "build_time": {
                "level1": 120.0,  # 2 hours
                "level2": 60.0,   # 1 hour
                "level3": 30.0,   # 30 minutes
                "level4": 15.0,   # 15 minutes
                "level5": 5.0     # 5 minutes
            },
            "pipeline_stability": {
                "level1": 40.0,   # 40% success rate
                "level2": 60.0,   # 60% success rate
                "level3": 75.0,   # 75% success rate
                "level4": 85.0,   # 85% success rate
                "level5": 95.0    # 95% success rate
            }
        }

    def assess(
        self,
        repo_data: RepositoryData,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData],
        project_name: str,
        repo_name: Optional[str] = None,
    ) -> AssessmentResult:
        """Assess deployment pipeline metrics.
        
        Args:
            repo_data: Repository data
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            Assessment result with scores and explanations
        """
        builds = cast(List[Dict[str, Any]], builds)
        if not builds:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": ["No build data available"]
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
                if self.exemption_manager.is_metric_exempt(project_name, 'deployment_frequency', repo_name):
                    ex_set.add('deployment_frequency')
                if self.exemption_manager.is_metric_exempt(project_name, 'build_time', repo_name):
                    ex_set.add('build_time')
                if self.exemption_manager.is_metric_exempt(project_name, 'pipeline_stability', repo_name):
                    ex_set.add('pipeline_stability')
        except Exception as e:
            logger.exception(f"Failed to check exemptions for {project_name}/{repo_name}: {e}")
        if ex_set:
            for m in sorted(ex_set):
                reason = None
                try:
                    reason = self.exemption_manager.get_exemption_reason(project_name, m) if self.exemption_manager else None
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
                "explanation": explanation + ["All metrics in deployment_pipeline are exempt"],
            }
        total_w = sum(active_required.values())
        weight_map = {k: (v / total_w if total_w > 0 else 0.0) for k, v in active_required.items()}

        # Calculate deployment frequency (40%)
        if 'deployment_frequency' in weight_map:
            deployment_freq = self._calculate_deployment_frequency(builds)
            if deployment_freq is not None:
                metrics['deployment_frequency'] = deployment_freq
                freq_score = self.scoring.calculate_metric_score(
                    [(deployment_freq, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['deployment_frequency'])
                )
                weight = weight_map['deployment_frequency']
                scores.append((freq_score, weight))
                total_weight += weight
                
                level = self._determine_level(deployment_freq, self.thresholds['deployment_frequency'])
                explanation.append(f"Deployment Frequency ({deployment_freq:.1f} per month) meets Level {level} threshold")
            else:
                missing_metrics.append(("deployment_frequency", weight_map['deployment_frequency']))
                explanation.append("Deployment Frequency metric missing")

        # Calculate build time (30%)
        if 'build_time' in weight_map:
            build_times = self._calculate_build_times(builds)
            if build_times:
                avg_build_time = sum(time for time, _ in build_times) / len(build_times)
                metrics['build_time'] = avg_build_time
                # Lower build times are better (reverse)
                time_score = self.scoring.calculate_metric_score(
                    build_times,
                    cast(Dict[str, float], self.thresholds['build_time']),
                    reverse=True
                )
                weight = weight_map['build_time']
                scores.append((time_score, weight))
                total_weight += weight
                
                level = self._determine_level(
                    avg_build_time,
                    self.thresholds['build_time'],
                    reverse=True
                )
                explanation.append(f"Build Time ({avg_build_time:.1f} minutes) meets Level {level} threshold")
            else:
                missing_metrics.append(("build_time", weight_map['build_time']))
                explanation.append("Build Time metric missing")

        # Calculate pipeline stability (30%)
        if 'pipeline_stability' in weight_map:
            stability_data = self._calculate_pipeline_stability(builds)
            if stability_data:
                stability = sum(rate for rate, _ in stability_data) / len(stability_data)
                metrics['pipeline_stability'] = stability
                stability_score = self.scoring.calculate_metric_score(
                    stability_data,
                    cast(Dict[str, float], self.thresholds['pipeline_stability'])
                )
                weight = weight_map['pipeline_stability']
                scores.append((stability_score, weight))
                total_weight += weight
                
                level = self._determine_level(stability, self.thresholds['pipeline_stability'])
                explanation.append(f"Pipeline Stability ({stability:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("pipeline_stability", weight_map['pipeline_stability']))
                explanation.append("Pipeline Stability metric missing")

        # Calculate final score
        if scores:
            base_score = sum(score * weight for score, weight in scores) / total_weight if total_weight > 0 else 0.0
            final_score = self.scoring.apply_missing_metrics_penalty(base_score, missing_metrics, explanation, ex_set)
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
                level_key = get_level_key(level)
                if value <= thresholds[level_key]:
                    return level
        else:
            for level in range(5, 0, -1):
                level_key = get_level_key(level)
                if value >= thresholds[level_key]:
                    return level
        return 1

    def _calculate_deployment_frequency(
        self,
        builds: List[Dict[str, Any]]
    ) -> Optional[float]:
        """Calculate deployment frequency per month.
        
        Args:
            builds: List of builds
            
        Returns:
            Deployments per month or None if no data
        """
        successful_deployments = []
        
        for build in builds:
            build_dict = cast(Dict[str, Any], build)
            if build_dict.get('result', '').lower() != 'succeeded':
                continue
                
            try:
                finish_time = str(build_dict.get('finishTime', ''))
                timestamp = datetime.fromisoformat(finish_time)
                successful_deployments.append(timestamp)
            except (ValueError, TypeError):
                continue
        
        if not successful_deployments:
            return None
            
        # Calculate deployments per month
        now = datetime.now(timezone.utc)
        oldest_deployment = min(successful_deployments)
        months = (now - oldest_deployment).days / 30.0  # Approximate months
        if months < 1:
            months = 1.0
            
        return len(successful_deployments) / months

    def _calculate_build_times(
        self,
        builds: List[Dict[str, Any]]
    ) -> List[Tuple[float, datetime]]:
        """Calculate build times in minutes with timestamps.
        
        Args:
            builds: List of builds
            
        Returns:
            List of (minutes, timestamp) tuples
        """
        build_times: List[Tuple[float, datetime]] = []
        
        for build in builds:
            build_dict = cast(Dict[str, Any], build)
            try:
                start_time = datetime.fromisoformat(str(build_dict.get('startTime', '')))
                finish_time = datetime.fromisoformat(str(build_dict.get('finishTime', '')))
                duration = (finish_time - start_time).total_seconds() / 60.0  # Convert to minutes
                if duration > 0:
                    build_times.append((duration, finish_time))
            except (ValueError, TypeError, KeyError):
                continue
        
        return build_times

    def _calculate_pipeline_stability(
        self,
        builds: List[Dict[str, Any]]
    ) -> List[Tuple[float, datetime]]:
        """Calculate pipeline stability (success rate) with timestamps.
        
        Args:
            builds: List of builds
            
        Returns:
            List of (success_rate, timestamp) tuples
        """
        success_values: List[Tuple[float, datetime]] = []
        
        for build in builds:
            build_dict = cast(Dict[str, Any], build)
            if 'result' not in build_dict or 'finishTime' not in build_dict:
                continue
                
            try:
                finish_time = str(build_dict['finishTime'])
                timestamp = datetime.fromisoformat(finish_time)
                result = str(build_dict['result']).lower()
                success = 100.0 if result == 'succeeded' else 0.0
                success_values.append((success, timestamp))
            except (ValueError, KeyError):
                continue
        
        return success_values
