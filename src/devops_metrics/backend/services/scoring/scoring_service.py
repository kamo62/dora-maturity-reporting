"""Scoring service implementation."""
from typing import Dict, Any, List, Tuple, Optional, Mapping, Sequence, Set
from datetime import datetime, timezone, timedelta
from ....utils.time import parse_ts


class ScoringService:
    """Service for calculating maturity scores.

    This service uses a time-weighted scoring approach, balancing current month data with historical data.
    The historical data window is extended to 180 days, allowing for a more comprehensive view of past performance.
    The time-weighting configuration determines how these two data sets are balanced, with the current month data and historical data each contributing 50% to the overall score.
    The time-weighted scoring approach is designed to provide a balanced view of both recent and past performance, 
    enabling a more accurate assessment of maturity levels. The current month data provides insight into recent trends 
    and performance, while the historical data offers a broader perspective on past achievements and challenges.
    By combining these two data sets, the service can provide a more comprehensive and nuanced view of maturity levels.
    """

    def __init__(
        self, current_month_weight: float = 0.8, historical_weight: float = 0.2
    ) -> None:
        """Initialize the scoring service.

        Args:
            current_month_weight: Weight for the current month's data.
            historical_weight: Weight for historical data.
        """
        self.maturity_levels = {
            1: "Initial",      # 0-20%  - Inconsistent practices
            2: "Managed",      # 21-40% - Basic management foundation
            3: "Standardized", # 41-60% - Consistent processes
            4: "Predictable",  # 61-80% - Reliable results
            5: "Optimizing"    # 81-100% - Continuous improvement
        }
        # Time-weighting configuration
        self.current_month_weight = current_month_weight
        self.historical_weight = historical_weight

    def determine_maturity_level(self, score: float) -> int:
        """Determine maturity level based on score.
        
        Args:
            score: Score value (0-1)
            
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
        """Get human-readable label for maturity level.
        
        Args:
            level: Maturity level (1-5)
            
        Returns:
            Label for the level
        """
        return self.maturity_levels.get(level, "Unknown")

    def apply_missing_metrics_penalty(
        self, 
        base_score: float, 
        missing_metrics: Sequence[Tuple[str, float]],
        explanations: Optional[List[str]] = None,
        exempt_metrics: Optional[Set[str]] = None,
    ) -> float:
        """Apply penalty for missing metrics.
        
        Args:
            base_score: Base score to apply penalty to
            missing_metrics: List of tuples with metric name and weight
            explanations: Optional list to add explanations about missing metrics
            
        Returns:
            Adjusted score after applying penalty
        """
        if not missing_metrics:
            return base_score

        # Filter out exempt metrics from penalty
        filtered = list(missing_metrics)
        if exempt_metrics:
            filtered = [(name, weight) for name, weight in missing_metrics if name not in exempt_metrics]
        if not filtered:
            return base_score

        total_penalty = sum(weight for _, weight in filtered)
        # Cap the total penalty at 25%
        total_penalty = min(total_penalty, 0.25)
        # Apply penalty
        adjusted_score = base_score * (1 - total_penalty)
        # Add explanation if provided
        if explanations is not None:
            penalty_percent = total_penalty * 100
            explanations.append(f"Applied {penalty_percent:.1f}% penalty for missing metrics (non-exempt)")
        # Enforce a minimum floor and clamp to [0.1, 1.0]
        return max(0.1, min(1.0, adjusted_score))

    def calculate_metric_score(
        self,
        values: List[Tuple[float, Any]],
        thresholds: Mapping[str, float],
        reverse: bool = False
    ) -> float:
        """Calculate a normalized score (0-1) based on thresholds with time weighting.
        
        Args:
            values: List of (value, timestamp) tuples
            thresholds: Level thresholds
            reverse: If True, lower values are better
            
        Returns:
            Normalized score (0-1)
        """
        if not values or not thresholds:
            return 0.0

        # Apply time-weighted scoring (50% current month, 50% historical)
        current_month_start = self._get_current_month_start()
        
        # Split data into current month and historical
        current_month_values = []
        historical_values = []
        
        for value, timestamp in values:
            try:
                ts = parse_ts(timestamp)  # Normalize to aware UTC datetime
            except (ValueError, TypeError):
                # Skip invalid timestamps
                continue

            if ts >= current_month_start:
                current_month_values.append(value)
            else:
                historical_values.append(value)
        
        # Calculate average for each time period
        current_month_avg = self._calculate_average(current_month_values) if current_month_values else None
        historical_avg = self._calculate_average(historical_values) if historical_values else None
        
        # Apply time weighting
        if current_month_avg is not None and historical_avg is not None:
            # We have both current and historical data
            weighted_value = (current_month_avg * self.current_month_weight + 
                             historical_avg * self.historical_weight)
        elif current_month_avg is not None:
            # Only current month data
            weighted_value = current_month_avg
        elif historical_avg is not None:
            # Only historical data
            weighted_value = historical_avg
        else:
            # No valid data
            return 0.0
            
        # Calculate score based on thresholds
        if reverse:
            max_threshold = float(thresholds["level1"])
            if weighted_value > max_threshold:
                return 0.1
            score = (max_threshold - weighted_value) / max_threshold
            return max(0.1, min(1.0, score))
        
        if weighted_value < float(thresholds["level1"]):
            return 0.1
        if weighted_value >= float(thresholds["level5"]):
            return 1.0

        # Determine level and calculate score
        level = next((l for l in range(5, 1, -1) 
                     if weighted_value >= float(thresholds[f"level{l}"])), 1)
        
        lower_threshold = float(thresholds[f"level{level}"])
        upper_threshold = float(thresholds[f"level{level + 1}"]) if level < 5 else float(thresholds["level5"])
        level_progress = (weighted_value - lower_threshold) / (upper_threshold - lower_threshold)
        
        base_score = (level - 1) * 0.2
        level_contribution = 0.2 * level_progress
        return max(0.1, min(1.0, base_score + level_contribution))
        
    def _get_current_month_start(self) -> datetime:
        """Get the start of the current month.
        
        Returns:
            Datetime object representing the start of the current month
        """
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        
    def _calculate_average(self, values: List[float]) -> float:
        """Calculate the average of a list of values.
        
        Args:
            values: List of numeric values
            
        Returns:
            Average value
        """
        if not values:
            return 0.0
        return sum(values) / len(values)

    def calculate_weighted_average(
        self, 
        scores: Dict[str, float], 
        weights: Dict[str, float]
    ) -> float:
        """Calculate weighted average of scores.
        
        Args:
            scores: Dictionary of scores
            weights: Dictionary of weights
            
        Returns:
            Weighted average score
        """
        if not scores or not weights:
            return 0.0
            
        total_weight = 0.0
        weighted_sum = 0.0
        
        for key, score in scores.items():
            if key in weights:
                weight = weights[key]
                weighted_sum += score * weight
                total_weight += weight
                
        if total_weight == 0:
            return 0.0
            
        return weighted_sum / total_weight
