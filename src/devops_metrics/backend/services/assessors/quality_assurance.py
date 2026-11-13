"""Quality assurance metrics assessor."""
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, cast, Optional, Set

from ....types.assessment_types import (
    MetricThresholds,
    AssessmentResult,
    BuildTestData
)
from ..scoring.scoring_service import ScoringService
from ....types.data_types import RepositoryData
from ....utils.logging import get_logger
from ....utils.validation import load_thresholds
from ....utils.time import parse_ts
from devops_metrics.config.exemptions import ExemptionManager


class QualityAssuranceAssessor:
    """Assessor for quality assurance metrics."""

    def __init__(self, scoring_service: ScoringService, exemption_manager: Optional[ExemptionManager] = None) -> None:
        """Initialize the assessor.
        
        Args:
            scoring_service: Service for calculating scores
        """
        self.scoring = scoring_service
        self.exemption_manager = exemption_manager
        self.required_metrics = {
            "code_coverage": 0.33,
            "test_pass_rate": 0.33,
            "build_success_rate": 0.34
        }
        # Load centralized thresholds once
        self._thresholds_cfg = load_thresholds()
        # Track repositories we've already processed to avoid duplicate explanations
        self.processed_repos: Set[str] = set()

    def assess(
        self,
        repo_data: RepositoryData,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData],
        project_name: str,
        repo_name: Optional[str] = None,
    ) -> AssessmentResult:
        """Assess quality assurance metrics.
        
        Args:
            repo_data: Repository data
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            Assessment result with scores and explanations
        """
        if not builds or not build_data:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": ["No build or coverage data available"],
                "present": False
            }

        scores: List[Tuple[float, float]] = []
        total_weight = 0.0
        missing_metrics: List[Tuple[str, float]] = []
        explanation: List[str] = []
        metrics: Dict[str, Any] = {}
        present = False
        
        # Get repository ID to track processed repositories
        repo_id = repo_data.get('id', '') if repo_data else ''

        # Exemptions filtering and weight redistribution
        ex_set: Set[str] = set()
        if self.exemption_manager:
            for name in self.required_metrics.keys():
                try:
                    if self.exemption_manager.is_metric_exempt(project_name, name, repo_name):
                        ex_set.add(name)
                except Exception as e:
                    get_logger(__name__).warning(f"Error checking exemption for {name}: {e}")
        # Add explanations for exempt metrics
        if ex_set:
            for m in sorted(ex_set):
                reason = None
                try:
                    reason = self.exemption_manager.get_exemption_reason(project_name, m) if self.exemption_manager else None
                except Exception as e:
                    get_logger(__name__).warning(f"Error getting exemption reason for {m}: {e}")
                    reason = None
                explanation.append(f"{m} is exempt: {reason or 'policy exemption'}")
        active_required = {k: v for k, v in self.required_metrics.items() if k not in ex_set}
        if not active_required:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": explanation + ["All metrics in quality_assurance are exempt"],
                "present": False,
            }
        total_w = sum(active_required.values())
        weight_map = {k: (v / total_w if total_w > 0 else 0.0) for k, v in active_required.items()}

        # Calculate code coverage score (33%)
        # Note: Only real code coverage data is considered. We no longer
        # use test pass rate as a proxy for code coverage.
        if 'code_coverage' in weight_map:
            coverage_values = self._calculate_coverage(builds, build_data)
            if coverage_values:
                coverage = sum(value for value, _ in coverage_values) / len(coverage_values)
                metrics['code_coverage'] = coverage
                present = True  # coverage observed even if 0.0
                levels = self._levels_for('code_coverage')
                coverage_score = self.scoring.calculate_metric_score(
                    coverage_values,
                    levels
                )
                weight = weight_map['code_coverage']
                scores.append((coverage_score, weight))
                total_weight += weight
                
                level = self.scoring.determine_maturity_level(coverage_score)
                explanation.append(
                    f"Code Coverage ({coverage:.1f}%) meets {self.scoring.get_maturity_label(level)} "
                    f"(Level {level}) threshold"
                )
            else:
                missing_metrics.append(("code_coverage", weight_map['code_coverage']))
                explanation.append("Code Coverage metric missing")

        # Calculate test pass rate (33%)
        if 'test_pass_rate' in weight_map:
            test_pass_rate = self._calculate_test_pass_rate(builds, build_data, [])  # Empty list to avoid duplicate explanations
            if test_pass_rate is not None:
                metrics['test_pass_rate'] = test_pass_rate
                present = True  # observed even if 0.0
                levels = self._levels_for('test_pass_rate')
                test_score = self.scoring.calculate_metric_score(
                    [(test_pass_rate, datetime.now(timezone.utc))],
                    levels
                )
                weight = weight_map['test_pass_rate']
                scores.append((test_score, weight))
                total_weight += weight
                
                level = self.scoring.determine_maturity_level(test_score)
                explanation.append(
                    f"Test Pass Rate ({test_pass_rate:.1f}%) meets {self.scoring.get_maturity_label(level)} "
                    f"(Level {level}) threshold"
                )
            else:
                missing_metrics.append(("test_pass_rate", weight_map['test_pass_rate']))
                explanation.append("Test Pass Rate metric missing")

        # Calculate build success rate (34%)
        if 'build_success_rate' in weight_map:
            build_results = self._calculate_build_success_rate(builds, [])  # Empty list to avoid duplicate explanations
            if build_results is not None:
                success_rate = build_results
                metrics['build_success_rate'] = success_rate
                present = True  # observed even if 0.0
                levels = self._levels_for('build_success_rate')
                build_score = self.scoring.calculate_metric_score(
                    [(success_rate, datetime.now(timezone.utc))],
                    levels
                )
                weight = weight_map['build_success_rate']
                scores.append((build_score, weight))
                total_weight += weight
                
                level = self.scoring.determine_maturity_level(build_score)
                explanation.append(
                    f"Build Success Rate ({success_rate:.1f}%) meets {self.scoring.get_maturity_label(level)} "
                    f"(Level {level}) threshold"
                )
            else:
                missing_metrics.append(("build_success_rate", weight_map['build_success_rate']))
                explanation.append("Build Success Rate metric missing")

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
            "explanation": explanation,
            "present": present
        }

    def _calculate_coverage(
        self,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData]
    ) -> List[Tuple[float, datetime]]:
        """Calculate code coverage values with timestamps.
        
        Args:
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            List of (coverage_percentage, timestamp) tuples
        """
        logger = get_logger(__name__)
        
        logger.info(f"Calculating coverage for {len(builds)} builds")
        logger.info(f"Build data has {len(build_data)} keys")
        if build_data:
            sample_keys = list(build_data.keys())[:5]
            logger.info(f"Sample build_data keys: {sample_keys} (type: {type(sample_keys[0]).__name__ if sample_keys else 'unknown'})")
        
        coverage_values: List[Tuple[float, datetime]] = []
        
        for build in builds:
            build_dict = build
            build_id = build_dict.get('id', '')
            build_id_str = str(build_id)
            repo_id = build_dict.get('repository', {}).get('id', '')
            repo_name = build_dict.get('repository', {}).get('name', '')
            
            logger.info(f"Processing build {build_id} (str: {build_id_str}) for repository {repo_name} ({repo_id})")
            
            if not build_id_str:
                logger.warning(f"Empty build ID for repository {repo_name}")
                continue
                
            if build_id_str not in build_data:
                logger.warning(f"Build {build_id_str} not found in build_data")
                continue
                
            data = build_data[build_id_str]
            
            # Only use actual code coverage data
            coverage_dict = cast(Dict[str, Any], data.get('coverage', {}))
            if not coverage_dict:
                logger.warning(f"No coverage data found for build {build_id}")
                continue
                
            # Handle empty coverage data
            if 'value' not in coverage_dict or not coverage_dict['value']:
                logger.warning(f"Empty coverage value for build {build_id}")
                continue
                
            value_list = cast(List[Dict[str, Any]], coverage_dict['value'])
            logger.info(f"Found {len(value_list)} coverage value items for build {build_id}")
            
            # If we have coverage data but no modules, we cannot compute coverage
            if not value_list or all('modules' not in item or not item['modules'] for item in value_list):
                logger.warning(f"No modules found in coverage data for build {build_id}")
                continue
            
            for item in value_list:
                if 'modules' not in item:
                    logger.warning(f"No modules found in coverage item for build {build_id}")
                    continue
                    
                modules_list = cast(List[Dict[str, Any]], item['modules'])
                logger.info(f"Found {len(modules_list)} modules in coverage data for build {build_id}")
                
                if not modules_list:
                    logger.warning(f"Empty modules list for build {build_id}")
                    continue
                
                total_covered = 0
                total_not_covered = 0
                
                for module in modules_list:
                    try:
                        # Try to get coverage directly if it exists
                        if 'coverage' in module:
                            coverage_value = float(module['coverage'])
                            if coverage_value > 0:
                                finish_time = str(build_dict.get('finishTime', ''))
                                if finish_time:
                                    try:
                                        timestamp = parse_ts(finish_time)
                                        coverage_values.append((coverage_value, timestamp))
                                        logger.info(f"Added coverage value {coverage_value} from direct 'coverage' field")
                                    except (ValueError, TypeError) as e:
                                        logger.error(f"Error parsing timestamp {finish_time}: {str(e)}")
                        # Otherwise calculate from statistics
                        elif 'statistics' in module:
                            stats = module['statistics']
                            covered = stats.get('linesCovered', 0)
                            not_covered = stats.get('linesNotCovered', 0)
                            
                            # Accumulate totals for overall calculation
                            total_covered += covered
                            total_not_covered += not_covered
                        else:
                            logger.warning(f"Module has neither 'coverage' nor 'statistics' fields: {module.get('name', 'unknown')}")
                    except (ValueError, TypeError, KeyError) as e:
                        logger.error(f"Error processing module coverage: {str(e)}")
                        continue
                
                # Calculate overall coverage for this build if we accumulated statistics
                if total_covered > 0 or total_not_covered > 0:
                    total = total_covered + total_not_covered
                    if total > 0:
                        coverage_value = (total_covered / total) * 100
                        finish_time = str(build_dict.get('finishTime', ''))
                        if finish_time:
                            try:
                                timestamp = parse_ts(finish_time)
                                coverage_values.append((coverage_value, timestamp))
                                logger.info(f"Added overall coverage value {coverage_value} calculated from accumulated statistics")
                            except (ValueError, TypeError) as e:
                                logger.error(f"Error parsing timestamp {finish_time}: {str(e)}")
        
        logger.info(f"Calculated {len(coverage_values)} coverage values")
        return coverage_values

    def _calculate_test_pass_rate(
        self,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData],
        explanation: List[str]
    ) -> Optional[float]:
        """Calculate test pass rate.
        
        Args:
            builds: List of builds
            build_data: Build data
            explanation: List to append explanations to
            
        Returns:
            Test pass rate or None if not available
        """
        for build_id, data in build_data.items():
            tests_section: Dict[str, Any] = data.get("tests", {}) or {}
            results_list: List[Dict[str, Any]] = cast(List[Dict[str, Any]], tests_section.get("results", []) or [])
            for test_result in results_list:
                total = int(test_result.get("total", 0))
                failed = int(test_result.get("failed", 0))
                if total > 0:
                    pass_rate: float = ((total - failed) / total) * 100.0
                    return pass_rate
        explanation.append("Test Pass Rate metric missing")
        return None

    def _calculate_build_success_rate(
        self,
        builds: List[Dict[str, Any]],
        explanation: List[str]
    ) -> Optional[float]:
        """Calculate build success rate.
        
        Args:
            builds: List of builds
            explanation: List to append explanations to
            
        Returns:
            Success rate or None if not available
        """
        if not builds:
            explanation.append("Build Success Rate metric missing")
            return None
            
        success_count = sum(1 for build in builds if build.get("result", "").lower() == "succeeded")
        success_rate = (success_count / len(builds)) * 100.0
        
        return success_rate

    def _levels_for(self, metric_name: str) -> Dict[str, float]:
        """Get levels mapping for a metric from centralized thresholds."""
        metric_cfg = self._thresholds_cfg.metrics.get(metric_name)
        if not metric_cfg:
            # Fallback to a safe default to avoid runtime errors
            return {"level1": 0.0, "level2": 25.0, "level3": 50.0, "level4": 75.0, "level5": 100.0}
        lv = metric_cfg.levels
        return {
            "level1": lv.level1,
            "level2": lv.level2,
            "level3": lv.level3,
            "level4": lv.level4,
            "level5": lv.level5,
        }
