"""Service for calculating maturity scores and levels."""
from datetime import datetime, timezone
import math
from typing import Dict, List, Tuple, Any

from ..types.assessment_types import (
    MetricThresholds,
    AssessmentResult
)


class ScoringService:
    """Service for calculating maturity scores and determining levels."""

    def __init__(self) -> None:
        """Initialize the scoring service with maturity levels."""
        self.maturity_levels = {
            1: "Initial",      # 0-20%  - Inconsistent practices
            2: "Managed",      # 21-40% - Basic management foundation
            3: "Standardized", # 41-60% - Consistent processes
            4: "Predictable",  # 61-80% - Reliable results
            5: "Optimizing"    # 81-100% - Continuous improvement
        }

    def calculate_weighted_average(
        self,
        values: List[Tuple[float, datetime]]
    ) -> float:
        """Calculate time-weighted average of values.
        
        Args:
            values: List of (value, timestamp) tuples
            
        Returns:
            Time-weighted average value
        """
        if not values:
            return 0.0

        now = datetime.now(timezone.utc)
        weighted_values: List[float] = []
        total_weight = 0.0

        for value, timestamp in values:
            days_ago = (now - timestamp).days
            weight = math.exp(-0.1 * days_ago)  # Weight decreases exponentially with age
            weighted_values.append(value * weight)
            total_weight += weight

        return sum(weighted_values) / total_weight if total_weight > 0 else 0.0

    def calculate_level_score(
        self,
        value: float,
        thresholds: MetricThresholds,
        reverse: bool = False
    ) -> float:
        """Calculate score for a single value based on thresholds.
        
        Args:
            value: Value to calculate score for
            thresholds: Level thresholds for the metric
            reverse: If True, lower values are better
            
        Returns:
            Score between 0.0 and 1.0
        """
        if reverse:
            if value > float(thresholds["level1"]):
                return 0.1
            max_threshold = float(thresholds["level1"])
            score = (max_threshold - value) / max_threshold
            return max(0.1, min(1.0, score))
        
        if value < float(thresholds["level1"]):
            return 0.1
        if value >= float(thresholds["level5"]):
            return 1.0

        # Determine level and calculate score
        level = next(
            (l for l in range(5, 1, -1) if value >= float(thresholds[f"level{l}"])),
            1
        )
        
        lower_threshold = float(thresholds[f"level{level}"])
        upper_threshold = float(thresholds[f"level{level + 1}"] if level < 5 else thresholds["level5"])
        level_progress = (value - lower_threshold) / (upper_threshold - lower_threshold)
        
        base_score = (level - 1) * 0.2
        level_contribution = 0.2 * level_progress
        return max(0.1, min(1.0, base_score + level_contribution))

    def apply_missing_metrics_penalty(
        self,
        base_score: float,
        missing_metrics: List[Tuple[str, float]],
        explanation: List[str]
    ) -> float:
        """Apply penalties for missing metrics.
        
        Args:
            base_score: Initial score before penalties
            missing_metrics: List of (metric_name, weight) tuples
            explanation: List to append penalty explanations to
            
        Returns:
            Final score after applying penalties
        """
        total_penalty = sum(weight for _, weight in missing_metrics)
        if total_penalty > 0:
            for metric, weight in missing_metrics:
                explanation.append(f"Missing {metric}: -{weight*100:.0f}% penalty applied")
        
        return round(max(0.1, base_score * (1 - total_penalty)), 6)

    def calculate_metric_score(
        self,
        values: List[Tuple[float, datetime]],
        thresholds: MetricThresholds,
        reverse: bool = False
    ) -> float:
        """Calculate a normalized score based on thresholds with time-weighted linear scaling.
        
        Args:
            values: List of (value, timestamp) tuples
            thresholds: Level thresholds for the metric
            reverse: If True, lower values are better
            
        Returns:
            Score between 0.0 and 1.0
        """
        if not values:
            return 0.0

        # Validate and convert values
        processed_values: List[Tuple[float, datetime]] = []
        for value, timestamp in values:
            try:
                float_value = float(value)
                if float_value > 0:
                    processed_values.append((float_value, timestamp))
            except (TypeError, ValueError):
                continue

        if not processed_values:
            return 0.0

        # Calculate scores for each value
        scores = [
            (self.calculate_level_score(value, thresholds, reverse), timestamp)
            for value, timestamp in processed_values
        ]

        # Calculate time-weighted average
        return round(self.calculate_weighted_average(scores), 6)

    def determine_maturity_level(self, score: float) -> int:
        """Determine maturity level based on score.
        
        Args:
            score: Score between 0.0 and 1.0
            
        Returns:
            Maturity level (1-5)
        """
        if score >= 0.81:
            return 5  # Optimizing
        elif score >= 0.61:
            return 4  # Predictable
        elif score >= 0.41:
            return 3  # Standardized
        elif score >= 0.21:
            return 2  # Managed
        else:
            return 1  # Initial

    def get_maturity_label(self, level: int) -> str:
        """Get human-readable label for a maturity level.
        
        Args:
            level: Maturity level (1-5)
            
        Returns:
            Human-readable label for the level
        """
        return self.maturity_levels.get(level, "Unknown")

    def create_assessment_result(
        self,
        score: float,
        metrics: Dict[str, Any],
        explanation: List[str]
    ) -> AssessmentResult:
        """Create an assessment result with the given values.
        
        Args:
            score: Score between 0.0 and 1.0
            metrics: Dictionary of metric values
            explanation: List of explanation strings
            
        Returns:
            AssessmentResult with the provided values
        """
        level = self.determine_maturity_level(score)
        return {
            "score": score,
            "level": level,
            "metrics": metrics,
            "explanation": explanation
        }
