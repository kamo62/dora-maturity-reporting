"""Raw Maturity Assessment Service"""
from typing import Dict, Any, List, Tuple, Optional, cast

from ...utils.logging import get_logger
from ...types.assessment_types import (
    AssessmentResult,
    ProjectAssessment,
    DimensionName,
    RepositoryAssessment,
    OverallResult,
    ScoreWeight,
    ScoreExplanation,
    QUALITY_ASSURANCE,
    DEPLOYMENT_PIPELINE,
    SECURITY_COMPLIANCE,
    COLLABORATION_METRICS,
    DIMENSION_KEYS
)
from ...types.data_types import (
    RawData, 
    validate_project_info
)
from .base_service import MetricsService
from .assessors import (
    QualityAssuranceAssessor,
    DeploymentPipelineAssessor,
    SecurityComplianceAssessor,
    CollaborationMetricsAssessor,
    Assessor
)
from .scoring.scoring_service import ScoringService
from devops_metrics.config.exemptions import ExemptionManager

logger = get_logger(__name__)


class RawMaturityAssessmentService(MetricsService):
    """Service for assessing project maturity based on raw Azure DevOps data."""

    def __init__(
        self, 
        ado_client: Any = None,
        current_month_weight: float = 0.9, # Default from Settings
        historical_weight: float = 0.1, # Default from Settings
        exemption_manager: Optional[ExemptionManager] = None,
    ) -> None:
        """Initialize the service with assessors.
        
        Args:
            ado_client: Azure DevOps client instance
            current_month_weight: Weight for current month data in scoring.
            historical_weight: Weight for historical data in scoring.
        """
        super().__init__(ado_client)
        
        # Initialize scoring service with weights from settings
        self.scoring = ScoringService(
            current_month_weight=current_month_weight,
            historical_weight=historical_weight
        )

        # Initialize assessors with optional exemption manager
        self.exemption_manager = exemption_manager
        self.assessors: Dict[DimensionName, Assessor] = {
            QUALITY_ASSURANCE: QualityAssuranceAssessor(self.scoring, exemption_manager),
            DEPLOYMENT_PIPELINE: DeploymentPipelineAssessor(self.scoring, exemption_manager),
            SECURITY_COMPLIANCE: SecurityComplianceAssessor(self.scoring, exemption_manager),
            COLLABORATION_METRICS: CollaborationMetricsAssessor(self.scoring, exemption_manager)
        }
        
        # Dimension weights
        self.weights: Dict[DimensionName, float] = {
            QUALITY_ASSURANCE: 0.30,      # Increased for core quality metrics
            DEPLOYMENT_PIPELINE: 0.30,     # Maintained for delivery speed
            SECURITY_COMPLIANCE: 0.25,     # Increased for security importance
            COLLABORATION_METRICS: 0.15    # Maintained for team interaction
        }

    def _check_data_presence(self, raw_data: RawData) -> None:
        """Check for presence of data and log warnings.
        
        Args:
            raw_data: Raw data to check
        """
        if not raw_data.get("repositories"):
            logger.warning("No repositories found in raw data")
        if not raw_data.get("repository_data"):
            logger.warning("No repository_data found in raw data")
        if not raw_data.get("builds"):
            logger.warning("No builds found in raw data")
        if not raw_data.get("build_data"):
            logger.warning("No build_data found in raw data")

    def _validate_raw_data(self, raw_data: RawData) -> List[str]:
        """Validate the structure of input raw data.
        
        Args:
            raw_data: Raw data to validate
            
        Returns:
            List of validation errors, empty if valid
        """
        errors = []
        
        try:
            # Validate required fields
            if not isinstance(raw_data.get("repositories"), list):
                errors.append("'repositories' must be a list")
            if not isinstance(raw_data.get("repository_data"), dict):
                errors.append("'repository_data' must be a dictionary")
            
            # Only check data presence if no type errors
            if not errors:
                self._check_data_presence(raw_data)
            
            # Validate project info if present
            if project := raw_data.get("project"):
                project_errors = validate_project_info(project)
                if project_errors:
                    errors.extend(f"Project info error: {err}" for err in project_errors)

        except Exception as e:
            errors.append(f"Error validating raw data: {str(e)}")
        
        return errors

    def assess_raw_data(self, raw_data: RawData) -> ProjectAssessment:
        """Assess raw data and calculate maturity scores.
        
        Args:
            raw_data: Raw data to assess
            
        Returns:
            Project assessment results
        """
        validation_errors = self._validate_raw_data(raw_data)
        if validation_errors:
            for error in validation_errors:
                logger.error(error)
            return self._create_empty_assessment()

        try:
            # Extract project name, provide default
            project_name = raw_data.get("project", {}).get("name", "Unknown")

            # Determine active (non-exempt) dimensions and redistribute weights
            if self.exemption_manager:
                active_dims = {d: w for d, w in self.weights.items() if not self.exemption_manager.is_dimension_exempt(project_name, d)}
            else:
                active_dims = self.weights.copy()
            if active_dims:
                total_w = sum(active_dims.values())
                active_weights = {d: (w / total_w if total_w > 0 else 0.0) for d, w in active_dims.items()}
            else:
                active_weights = {}

            # Initialize repository-level assessments
            repository_assessments: Dict[str, RepositoryAssessment] = {}

            # Process each repository with project context
            repository_data = raw_data["repository_data"]
            repositories = raw_data["repositories"]
            builds = raw_data["builds"]
            build_data = raw_data["build_data"]
            
            # Group repositories by project for proper assessment
            project_repositories: Dict[str, List[str]] = {}
            
            for repo_id, repo_data in repository_data.items():
                repo_info = next((r for r in repositories if r["id"] == repo_id), None)
                if not repo_info:
                    logger.warning(f"Repository info not found for {repo_id}")
                    continue

                repo_name = repo_info["name"]
                project_info = repo_info.get("project", {})
                
                if not project_info:
                    logger.warning(f"Project info not found for repository {repo_name}")
                    continue
                
                project_id = project_info.get("id")
                if not project_id:
                    logger.warning(f"Project ID not found for repository {repo_name}")
                    continue
                
                if project_id not in project_repositories:
                    project_repositories[project_id] = []
                project_repositories[project_id].append(repo_id)
                
                # Get repository-specific builds and build data
                repo_builds = [
                    b for b in builds
                    if b.get("repository", {}).get("id") == repo_id
                ]
                
                logger.info(f"Found {len(repo_builds)} builds for repository {repo_name} ({repo_id})")
                if repo_builds:
                    logger.info(f"Sample build ID: {repo_builds[0].get('id')} (type: {type(repo_builds[0].get('id')).__name__})")
                
                # Log build_data keys for debugging
                build_data_keys = list(build_data.keys())
                logger.info(f"Total build_data keys: {len(build_data_keys)}")
                if build_data_keys:
                    logger.info(f"Sample build_data key: {build_data_keys[0]} (type: {type(build_data_keys[0]).__name__})")
                
                # Create repo_build_data with more detailed logging
                repo_build_data = {}
                
                # Log some sample build IDs from repo_builds
                if repo_builds and len(repo_builds) > 0:
                    sample_ids = [str(b.get("id")) for b in repo_builds[:min(5, len(repo_builds))]]
                    logger.info(f"Sample build IDs from repo_builds: {sample_ids}")
                
                # Log some sample keys from build_data
                if build_data_keys and len(build_data_keys) > 0:
                    sample_keys = build_data_keys[:min(5, len(build_data_keys))]
                    logger.info(f"Sample keys from build_data: {sample_keys}")
                
                # Create repo_build_data with more detailed logging
                for bid, data in build_data.items():
                    # Check if this build ID is in the repo_builds list
                    match_found = next((True for b in repo_builds if str(b.get("id")) == str(bid)), False)
                    if match_found:
                        # Store with string key to match the format in build_data
                        repo_build_data[str(bid)] = data
                        logger.info(f"Match found for build ID {bid}")
                
                logger.info(f"Created repo_build_data with {len(repo_build_data)} entries for repository {repo_name}")
                
                # Initialize repository assessment with empty results and project metadata
                repo_project_name = project_info.get('name') or project_name
                repo_assessment: RepositoryAssessment = {
                    'name': repo_name,
                    'overall': cast(OverallResult, {}),
                    # Include minimal project metadata expected by downstream aggregation/exporters
                    'project': {
                        'id': project_id,
                        'name': repo_project_name,
                    },
                }
                for dim in DIMENSION_KEYS:
                    repo_assessment[dim] = cast(AssessmentResult, {})
                
                # Assess repository using each assessor
                for dimension, assessor in self.assessors.items():
                    repo_assessment[dimension] = assessor.assess(
                        repository_data[repo_id],
                        repo_builds,
                        repo_build_data,
                        project_name=project_info.get('name') or project_name,
                        repo_name=repo_name,
                    )
                
                # Calculate repository overall score
                repo_scores: List[ScoreWeight] = []
                total_weight = 0.0
                missing_dimensions: List[Tuple[str, float]] = []
                explanations: List[str] = []
                
                for dimension, weight in active_weights.items():
                    dim_assessment = repo_assessment[dimension]
                    if dim_assessment['score'] > 0.0:
                        repo_scores.append((dim_assessment['score'], weight))
                        total_weight += weight
                        explanations.extend(dim_assessment['explanation'])
                    else:
                        missing_dimensions.append((dimension, weight))
                        explanations.append(f"Missing {dimension} assessment")

                if repo_scores:
                    base_score = sum(score * weight for score, weight in repo_scores) / total_weight if total_weight > 0 else 0.0
                    final_score = self.scoring.apply_missing_metrics_penalty(base_score, missing_dimensions, explanations)
                else:
                    final_score = 0.0
                    explanations.append("No valid dimension scores available")

                level = self.scoring.determine_maturity_level(final_score)
                repo_assessment['overall'] = {
                    'score': final_score,
                    'level': level,
                    'label': self.scoring.get_maturity_label(level),
                    'explanation': explanations
                }

                repository_assessments[repo_id] = repo_assessment

            # Initialize project assessment with empty results, include project_name
            project_assessment: ProjectAssessment = {
                'project_name': project_name,  # Add project name here
                'overall': cast(OverallResult, {}),
                'repositories': repository_assessments
            }
            for dim in DIMENSION_KEYS:
                project_assessment[dim] = cast(AssessmentResult, {})

            # Calculate project-level dimension scores
            for dimension in active_weights.keys():
                dimension_scores: List[ScoreExplanation] = [
                    (assessment[dimension]['score'], assessment[dimension]['explanation'])
                    for assessment in repository_assessments.values()
                    if assessment[dimension]['score'] > 0.0
                ]

                if dimension_scores:
                    avg_score = sum(score for score, _ in dimension_scores) / len(dimension_scores)
                    # Use a set to deduplicate explanations
                    all_explanations = [exp for _, exps in dimension_scores for exp in exps]
                    unique_explanations = list(dict.fromkeys(all_explanations))  # Preserves order while removing duplicates
                    level = self.scoring.determine_maturity_level(avg_score)
                    project_assessment[dimension] = {
                        'score': avg_score,
                        'level': level,
                        'metrics': {},
                        'explanation': unique_explanations
                    }
                else:
                    project_assessment[dimension] = {
                        'score': 0.0,
                        'level': 1,
                        'metrics': {},
                        'explanation': [f"No {dimension} data available"]
                    }

            # Calculate project overall score
            project_scores: List[ScoreWeight] = []
            total_weight = 0.0
            missing_dimensions = []
            project_explanations: List[str] = []

            for dimension, weight in active_weights.items():
                if project_assessment[dimension]['score'] > 0.0:
                    project_scores.append((project_assessment[dimension]['score'], weight))
                    total_weight += weight
                else:
                    missing_dimensions.append((dimension, weight))
            
            # Add repository summaries to explanations
            for repo_id, assessment in repository_assessments.items():
                project_explanations.append(
                    f"{assessment['name']}: {assessment['overall']['label']} "
                    f"(Level {assessment['overall']['level']}, {assessment['overall']['score']:.1%})"
                )

            if project_scores:
                base_score = sum(score * weight for score, weight in project_scores) / total_weight if total_weight > 0 else 0.0
                final_score = self.scoring.apply_missing_metrics_penalty(base_score, missing_dimensions, project_explanations)
            else:
                final_score = 0.0
                project_explanations.append("No valid dimension scores available")

            level = self.scoring.determine_maturity_level(final_score)
            project_assessment['overall'] = {
                'score': final_score,
                'level': level,
                'label': self.scoring.get_maturity_label(level),
                'explanation': project_explanations
            }

            return project_assessment

        except Exception as e:
            logger.error(f"Error assessing raw data: {str(e)}")
            return self._create_empty_assessment()

    def _create_empty_assessment(self) -> ProjectAssessment:
        """Create an empty assessment result.
        
        Returns:
            Empty project assessment
        """
        empty_dimension: AssessmentResult = {
            'score': 0.0,
            'level': 0,
            'metrics': {},
            'explanation': ["Assessment failed"]
        }
        
        assessment: ProjectAssessment = {
            'overall': {
                'score': 0.0,
                'level': 0,
                'label': self.scoring.get_maturity_label(1),
                'explanation': ["Assessment failed"]
            },
            'repositories': {}
        }
        
        for dim in DIMENSION_KEYS:
            assessment[dim] = empty_dimension.copy()
        
        return assessment

    async def collect_metrics(self) -> Dict[str, Any]:
        """Not used - this service only processes already collected data"""
        return {}

    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Not used - this service only processes already collected data"""
        return None
