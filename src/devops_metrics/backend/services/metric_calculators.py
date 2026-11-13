"""Metric Calculators Module

This module provides specialized classes for calculating various metrics used in the
maturity assessment. Each calculator handles a specific type of metric (builds, PRs, etc.)
and includes proper timestamp handling for time-based analysis.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple, Optional
from ...utils.logging import get_logger

logger = get_logger(__name__)


class BuildMetrics:
    """Calculator for build-related metrics"""

    @staticmethod
    def calculate_success_rate(builds: List[Dict[str, Any]]) -> List[Tuple[float, datetime]]:
        """Calculate build success rate with timestamps

        Args:
            builds: List of build dictionaries

        Returns:
            List of (success_rate, timestamp) tuples
        """
        success_rates = []

        for build in builds:
            finish_time = build.get("finishTime")
            if finish_time:
                try:
                    if isinstance(finish_time, str):
                        finish_time = datetime.fromisoformat(
                            finish_time.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                    success_rate = 100.0 if build.get("result") == "succeeded" else 0.0
                    success_rates.append((success_rate, finish_time))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(f"Could not parse finish time for build {build.get('id')}")
                    continue

        return success_rates

    @staticmethod
    def calculate_pipeline_metrics(builds: List[Dict[str, Any]]) -> Optional[float]:
        """Calculate pipeline metrics score based on build configuration and steps

        Args:
            builds: List of build dictionaries

        Returns:
            Pipeline metrics score (0-10) or None if no valid builds
        """
        if not builds:
            return None

        # Calculate score based on pipeline configuration and steps
        total_score: float = 0.0
        valid_builds = 0

        for build in builds:
            build_score: float = 0.0
            definition = build.get("definition", {})
            steps = definition.get("steps", [])

            # Score based on pipeline features
            if definition.get("continuous_integration", False):
                build_score += 2  # CI enabled
            if definition.get("continuous_delivery", False):
                build_score += 2  # CD enabled
            if definition.get("yaml_based", False):
                build_score += 1  # YAML-based pipeline

            # Score based on pipeline steps
            if any(step.get("type") == "build" for step in steps):
                build_score += 1  # Has build step
            if any(step.get("type") == "test" for step in steps):
                build_score += 1  # Has test step
            if any(step.get("type") == "deploy" for step in steps):
                build_score += 1  # Has deploy step
            if any(step.get("type") == "quality" for step in steps):
                build_score += 1  # Has quality gate
            if any(step.get("type") == "security" for step in steps):
                build_score += 0.5  # Has security scan

            if build_score > 0:
                total_score += build_score
                valid_builds += 1

        return round(total_score / valid_builds, 2) if valid_builds > 0 else None

    @staticmethod
    def calculate_deployment_frequency(
        builds: List[Dict[str, Any]], window_days: int = 7
    ) -> List[Tuple[float, datetime]]:
        """Calculate deployment frequency with timestamps

        Args:
            builds: List of build dictionaries
            window_days: Number of days to use for frequency calculation window

        Returns:
            List of (frequency, timestamp) tuples where frequency is deployments per day
        """
        frequencies = []

        for build in builds:
            finish_time = build.get("finishTime")
            if finish_time:
                try:
                    if isinstance(finish_time, str):
                        finish_time = datetime.fromisoformat(
                            finish_time.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)

                    # Count builds in surrounding window
                    window_start = finish_time - timedelta(days=window_days / 2)
                    window_end = finish_time + timedelta(days=window_days / 2)

                    builds_in_window = sum(
                        1
                        for b in builds
                        if b.get("finishTime")
                        and window_start
                        <= datetime.fromisoformat(
                            b["finishTime"].replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                        <= window_end
                    )

                    frequency = builds_in_window / window_days  # builds per day
                    frequencies.append((frequency, finish_time))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(f"Could not parse finish time for build {build.get('id')}")
                    continue

        return frequencies


class PRMetrics:
    """Calculator for pull request-related metrics"""

    @staticmethod
    def calculate_review_process_score(pull_requests: List[Dict[str, Any]]) -> Optional[float]:
        """Calculate code review process score based on PR configuration and practices

        Args:
            pull_requests: List of PR dictionaries

        Returns:
            Review process score (1-5) or None if no valid PRs
        """
        if not pull_requests:
            return None

        total_score: float = 0.0
        valid_prs = 0

        for pr in pull_requests:
            pr_score: float = 0.0

            # Basic PR configuration
            if pr.get("auto_complete", False):
                pr_score += 0.5  # Auto-complete enabled
            if pr.get("draft", False):
                pr_score += 0.5  # Uses draft PRs

            # Review requirements
            policies = pr.get("policies", {})
            if policies.get("required_reviewers", 0) >= 2:
                pr_score += 1  # Requires multiple reviewers
            if policies.get("comment_requirements", False):
                pr_score += 0.5  # Requires comments
            if policies.get("work_item_linking", False):
                pr_score += 0.5  # Requires work item links

            # Review activity
            reviews = pr.get("reviews", [])
            if len(reviews) >= 2:
                pr_score += 1  # Has multiple reviews
            if any(review.get("status") == "rejected" for review in reviews):
                pr_score += 0.5  # Shows review rigor
            if any(review.get("comments", []) for review in reviews):
                pr_score += 0.5  # Has review comments

            if pr_score > 0:
                total_score += pr_score
                valid_prs += 1

        return round(total_score / valid_prs, 2) if valid_prs > 0 else None

    @staticmethod
    def calculate_size_metrics(pull_requests: List[Dict[str, Any]]) -> List[Tuple[float, datetime]]:
        """Calculate PR size metrics with timestamps

        Args:
            pull_requests: List of PR dictionaries

        Returns:
            List of (size, completion_timestamp) tuples where size is total changes
        """
        size_metrics = []

        for pr in pull_requests:
            completed = pr.get("closedDate")
            if completed:
                try:
                    if isinstance(completed, str):
                        completed = datetime.fromisoformat(
                            completed.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)

                    additions = pr.get("additions", 0)
                    deletions = pr.get("deletions", 0)
                    if additions + deletions > 0:  # Only count non-empty PRs
                        size_metrics.append((additions + deletions, completed))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(
                        f"Could not parse completion date for PR {pr.get('pullRequestId')}"
                    )
                    continue

        return size_metrics

    @staticmethod
    def calculate_time_to_first_review(
        pull_requests: List[Dict[str, Any]],
    ) -> List[Tuple[float, datetime]]:
        """Calculate time to first review with timestamps

        Args:
            pull_requests: List of PR dictionaries

        Returns:
            List of (hours_to_first_review, creation_timestamp) tuples
        """
        first_review_times = []

        for pr in pull_requests:
            created = pr.get("creationDate")
            reviews = pr.get("reviews", [])

            if created and reviews:
                try:
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(
                            timezone.utc
                        )

                    # Find the earliest review
                    first_review = None
                    for review in reviews:
                        review_date = review.get("date")
                        if review_date:
                            try:
                                if isinstance(review_date, str):
                                    review_date = datetime.fromisoformat(
                                        review_date.replace("Z", "+00:00")
                                    ).astimezone(timezone.utc)
                                if not first_review or review_date < first_review:
                                    first_review = review_date
                            except (ValueError, TypeError, AttributeError):
                                continue

                    if first_review:
                        time_to_review = (first_review - created).total_seconds() / 3600  # hours
                        if time_to_review > 0:  # Only count valid reviews
                            first_review_times.append((time_to_review, created))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(f"Could not parse dates for PR {pr.get('pullRequestId')}")
                    continue

        return first_review_times

    @staticmethod
    def calculate_review_depth(pull_requests: List[Dict[str, Any]]) -> List[Tuple[float, datetime]]:
        """Calculate PR review depth metrics with timestamps

        Args:
            pull_requests: List of PR dictionaries

        Returns:
            List of (metric, completion_timestamp) tuples where metric combines
            number of reviewers and comments
        """
        depth_metrics = []

        for pr in pull_requests:
            completed = pr.get("closedDate")
            if completed:
                try:
                    if isinstance(completed, str):
                        completed = datetime.fromisoformat(
                            completed.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)

                    reviewers = len(pr.get("reviewers", []))
                    comments = len(pr.get("comments", []))

                    # Combine metrics with weights
                    depth = (reviewers * 0.6) + (comments * 0.4)  # Weighted combination
                    depth_metrics.append((depth, completed))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(
                        f"Could not parse completion date for PR {pr.get('pullRequestId')}"
                    )
                    continue

        return depth_metrics


class CoverageMetrics:
    """Calculator for code coverage metrics"""

    @staticmethod
    def calculate_coverage(
        builds: List[Dict[str, Any]], build_data: Dict[str, Any]
    ) -> List[Tuple[float, datetime]]:
        """Calculate code coverage metrics with timestamps

        Args:
            builds: List of build dictionaries
            build_data: Dictionary of build coverage data

        Returns:
            List of (coverage_percentage, timestamp) tuples
        """
        coverage_values = []

        for build in builds:
            build_id = build.get("id")
            finish_time = build.get("finishTime")

            if build_id and build_id in build_data and finish_time:
                try:
                    if isinstance(finish_time, str):
                        finish_time = datetime.fromisoformat(
                            finish_time.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)

                    coverage_data = build_data[build_id].get("coverage", {}).get("value", [])
                    for module in coverage_data:
                        for mod in module.get("modules", []):
                            stats = mod.get("statistics", {})
                            covered = stats.get("linesCovered", 0)
                            total = covered + stats.get("linesNotCovered", 0)
                            if total > 0:
                                coverage_values.append(((covered / total) * 100, finish_time))
                except (ValueError, TypeError, AttributeError):
                    logger.warning(f"Could not parse finish time for build {build_id}")
                    continue

        return coverage_values
