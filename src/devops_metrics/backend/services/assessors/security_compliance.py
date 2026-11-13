"""Security compliance assessor."""
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, cast, Optional, TypedDict, Literal

from ....types.assessment_types import (
    AssessmentResult,
    BuildTestData,
    PolicyData
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


class SecurityComplianceAssessor:
    """Assessor for security and compliance metrics."""

    def __init__(self, scoring_service: ScoringService, exemption_manager: Optional[ExemptionManager] = None) -> None:
        """Initialize the assessor.
        
        Args:
            scoring_service: Service for calculating scores
        """
        self.scoring = scoring_service
        self.exemption_manager = exemption_manager
        self.required_metrics = {
            "policy_compliance": 0.4,
            "security_scanning": 0.3,
            "branch_protection": 0.3
        }
        self.thresholds: Dict[str, LevelThresholds] = {
            "policy_compliance": {
                "level1": 40.0,   # 40% policies enabled
                "level2": 60.0,   # 60% policies enabled
                "level3": 75.0,   # 75% policies enabled
                "level4": 85.0,   # 85% policies enabled
                "level5": 95.0    # 95% policies enabled
            },
            "security_scanning": {
                "level1": 20.0,   # Basic scanning
                "level2": 40.0,   # Regular scanning
                "level3": 60.0,   # Comprehensive scanning
                "level4": 80.0,   # Advanced scanning
                "level5": 90.0    # Full coverage
            },
            "branch_protection": {
                "level1": 30.0,   # Basic protection
                "level2": 50.0,   # Standard protection
                "level3": 70.0,   # Enhanced protection
                "level4": 85.0,   # Strong protection
                "level5": 95.0    # Maximum protection
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
        """Assess security and compliance metrics.
        
        Args:
            repo_data: Repository data
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            Assessment result with scores and explanations
        """
        policies = repo_data.get('policies')
        policies = cast(Dict[str, Any], repo_data.get('policies'))
        if not policies:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": ["No policy data available"]
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
                if self.exemption_manager.is_metric_exempt(project_name, 'policy_compliance', repo_name):
                    ex_set.add('policy_compliance')
                if self.exemption_manager.is_metric_exempt(project_name, 'security_scanning', repo_name):
                    ex_set.add('security_scanning')
                if self.exemption_manager.is_metric_exempt(project_name, 'branch_protection', repo_name):
                    ex_set.add('branch_protection')
        except Exception as e:
            logger.warning(f"Error checking metric exemptions: {e}", exc_info=True)
        if ex_set:
            for m in sorted(ex_set):
                reason = None
                try:
                    reason = self.exemption_manager.get_exemption_reason(project_name or "", m) if getattr(self, 'exemption_manager', None) else None
                except Exception as e:
                    logger.warning(f"Error retrieving exemption reason for {m}: {e}")
                    reason = None
                explanation.append(f"{m} is exempt: {reason or 'policy exemption'}")
        active_required = {k: v for k, v in self.required_metrics.items() if k not in ex_set}
        if not active_required:
            return {
                "score": 0.0,
                "level": self.scoring.determine_maturity_level(0.0),
                "metrics": {},
                "explanation": explanation + ["All metrics in security_compliance are exempt"],
            }
        total_w = sum(active_required.values())
        weight_map = {k: (v / total_w if total_w > 0 else 0.0) for k, v in active_required.items()}

        # Calculate policy compliance (40%)
        if 'policy_compliance' in weight_map:
            policy_compliance = self._calculate_policy_compliance(repo_data)
            if policy_compliance is not None:
                metrics['policy_compliance'] = policy_compliance
                compliance_score = self.scoring.calculate_metric_score(
                    [(policy_compliance, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['policy_compliance'])
                )
                weight = weight_map['policy_compliance']
                scores.append((compliance_score, weight))
                total_weight += weight
                
                level = self._determine_level(policy_compliance, self.thresholds['policy_compliance'])
                explanation.append(f"Policy Compliance ({policy_compliance:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("policy_compliance", weight_map['policy_compliance']))
                explanation.append("Policy Compliance metric missing")

        # Calculate security scanning coverage (30%)
        if 'security_scanning' in weight_map:
            scanning_coverage = self._calculate_security_scanning(repo_data, builds, build_data)
            if scanning_coverage is not None:
                metrics['security_scanning'] = scanning_coverage
                scanning_score = self.scoring.calculate_metric_score(
                    [(scanning_coverage, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['security_scanning'])
                )
                weight = weight_map['security_scanning']
                scores.append((scanning_score, weight))
                total_weight += weight
                
                level = self._determine_level(scanning_coverage, self.thresholds['security_scanning'])
                explanation.append(f"Security Scanning ({scanning_coverage:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("security_scanning", weight_map['security_scanning']))
                explanation.append("Security Scanning metric missing")

        # Calculate branch protection (30%)
        if 'branch_protection' in weight_map:
            protection_score = self._calculate_branch_protection(repo_data)
            if protection_score is not None:
                metrics['branch_protection'] = protection_score
                protection_metric_score = self.scoring.calculate_metric_score(
                    [(protection_score, datetime.now(timezone.utc))],
                    cast(Dict[str, float], self.thresholds['branch_protection'])
                )
                weight = weight_map['branch_protection']
                scores.append((protection_metric_score, weight))
                total_weight += weight
                
                level = self._determine_level(protection_score, self.thresholds['branch_protection'])
                explanation.append(f"Branch Protection ({protection_score:.1f}%) meets Level {level} threshold")
            else:
                missing_metrics.append(("branch_protection", weight_map['branch_protection']))
                explanation.append("Branch Protection metric missing")

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

    def _calculate_policy_compliance(
        self,
        repo_data: RepositoryData
    ) -> Optional[float]:
        """Calculate policy compliance percentage.
        
        Args:
            repo_data: Repository data
            
        Returns:
            Policy compliance percentage or None if no data
        """
        policies = cast(PolicyData, repo_data.get('policies', {}))
        if not policies or 'statistics' not in policies:
            return None
        
        stats = policies['statistics']
        total_policies = stats.get('total_policies', 0)
        enabled_policies = stats.get('enabled_policies', 0)
        
        if total_policies > 0:
            return (enabled_policies / total_policies) * 100.0
            
        return None

    def _calculate_security_scanning(
        self,
        repo_data: RepositoryData,
        builds: List[Dict[str, Any]],
        build_data: Dict[str, BuildTestData]
    ) -> Optional[float]:
        """Calculate security scanning coverage.
        
        Args:
            repo_data: Repository data
            builds: List of builds
            build_data: Build test and coverage data
            
        Returns:
            Security scanning coverage percentage or None if no build data
        """
        # Require builds to evaluate scanning coverage. If there are no builds,
        # treat the metric as missing so the missing-metric penalty applies.
        if not builds:
            return None
            
        # Count builds with security scanning
        builds_with_scanning = 0
        
        for build in builds:
            if self._has_security_scanning(build, build_data):
                builds_with_scanning += 1
                
        # Calculate percentage of builds with security scanning
        coverage = (builds_with_scanning / len(builds)) * 100.0
        return coverage

    def _has_security_scanning(
        self,
        build: Dict[str, Any],
        build_data: Dict[str, BuildTestData]
    ) -> bool:
        """Check if build includes security scanning.
        
        Args:
            build: Build data
            build_data: Build test and coverage data
            
        Returns:
            True if build includes security scanning
        """
        # Require presence of security scan artifacts (e.g., SARIF files)
        # This is populated by the raw data collector via get_security_scan_results
        build_id = str(build.get('id', ''))
        if build_id and build_id in build_data:
            # The build_data contains security_scans field
            build_info = build_data[build_id]
            if isinstance(build_info, dict) and 'security_scans' in build_info:
                security_scans = build_info.get('security_scans', [])
                if security_scans:
                    return True
        return False

    def _calculate_branch_protection(
        self,
        repo_data: RepositoryData
    ) -> Optional[float]:
        """Calculate branch protection score.
        
        Args:
            repo_data: Repository data
            
        Returns:
            Branch protection score or None if no data
        """
        # First check for branch policies from the policies data (production format)
        policies = cast(PolicyData, repo_data.get('policies', {}))
        if policies:
            branch_policies = policies.get('branch_policies', [])
            if branch_policies:
                return self._calculate_branch_protection_from_policies(branch_policies)
        
        # Fallback to check for branches data (test format)
        branches = repo_data.get('branches', [])
        if branches:
            return self._calculate_branch_protection_from_branches(branches)
            
        # No branch protection data available
        return 0.0
    
    def _calculate_branch_protection_from_branches(
        self,
        branches: List[Dict[str, Any]]
    ) -> float:
        """Calculate branch protection from branches data (test format).
        
        Args:
            branches: List of branch data
            
        Returns:
            Branch protection score
        """
        if not branches:
            return 0.0
            
        total_score = 0.0
        protected_count = 0
        
        for branch in branches:
            if not isinstance(branch, dict):
                continue
                
            if branch.get('protected', False):
                protected_count += 1
                branch_score = 30.0  # Base score for being protected
                
                # Check protection rules
                rules = branch.get('protection_rules', {})
                if isinstance(rules, dict):
                    if rules.get('required_reviews', 0) > 0:
                        branch_score += 30.0  # Required reviews
                        if rules.get('required_reviews', 0) >= 2:
                            branch_score += 10.0  # Bonus for multiple reviewers
                    if rules.get('required_status_checks'):
                        branch_score += 30.0  # Required status checks
                    if rules.get('enforce_admins'):
                        branch_score += 10.0  # Enforce for admins
                    if rules.get('linear_history'):
                        branch_score += 10.0  # Linear history
                        
                total_score += min(branch_score, 100.0)
        
        if protected_count > 0:
            return total_score / len(branches)
            
        return 0.0
    
    def _calculate_branch_protection_from_policies(
        self,
        branch_policies: List[Dict[str, Any]]
    ) -> float:
        """Calculate branch protection from branch policies (production format).
        
        Args:
            branch_policies: List of branch policy data
            
        Returns:
            Branch protection score
        """
        if not branch_policies:
            return 0.0
            
        # Group policies by branch name to handle multiple entries per branch
        # (e.g., one for repo-level policies, one for project-level policies)
        branch_policy_map: Dict[str, List[Dict[str, Any]]] = {}
        
        for branch_policy_entry in branch_policies:
            if not isinstance(branch_policy_entry, dict):
                continue
                
            branch_name = branch_policy_entry.get('branch_name', '')
            if branch_name not in branch_policy_map:
                branch_policy_map[branch_name] = []
            branch_policy_map[branch_name].append(branch_policy_entry)
        
        # Calculate protection score for each unique branch
        total_score = 0.0
        branch_count = 0
        
        for branch_name, policy_entries in branch_policy_map.items():
            branch_count += 1
            branch_score = 0.0
            
            # Aggregate all policies for this branch across all entries
            all_policies = []
            is_default = False
            
            for entry in policy_entries:
                is_default = is_default or entry.get('is_default_branch', False)
                policies_list = entry.get('policies', [])
                all_policies.extend(policies_list)
            
            # Analyze the aggregated policies for this branch
            has_review_policy = False
            has_build_policy = False
            has_work_item_policy = False
            has_comment_resolution = False
            blocking_review_count = 0
            
            for policy in all_policies:
                if not isinstance(policy, dict):
                    continue
                    
                # Check if policy is enabled
                if not policy.get('isEnabled', False):
                    continue
                    
                # Get policy type
                policy_type = policy.get('type', {})
                if isinstance(policy_type, dict):
                    type_name = policy_type.get('displayName', '').lower()
                    
                    # Check for different policy types
                    if 'review' in type_name or 'approval' in type_name or 'minimum number of reviewers' in type_name:
                        has_review_policy = True
                        # Check if it's blocking
                        if policy.get('isBlocking', False):
                            blocking_review_count += 1
                    elif 'build' in type_name or 'status' in type_name:
                        has_build_policy = True
                    elif 'work item' in type_name:
                        has_work_item_policy = True
                    elif 'comment' in type_name:
                        has_comment_resolution = True
            
            # Calculate score based on policies present
            if has_review_policy:
                branch_score += 30.0  # Required reviews
                # Add bonus for blocking review policies (up to 10 points)
                branch_score += min(blocking_review_count * 5.0, 10.0)
            if has_build_policy:
                branch_score += 30.0  # Required status checks
            if has_work_item_policy:
                branch_score += 20.0  # Work item linking
            if has_comment_resolution:
                branch_score += 10.0  # Comment resolution
                
            # Bonus for default branch protection
            if is_default and branch_score > 0:
                branch_score += 10.0
                
            total_score += min(branch_score, 100.0)
        
        if branch_count > 0:
            return total_score / branch_count
            
        return 0.0
