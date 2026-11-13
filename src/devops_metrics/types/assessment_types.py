"""Assessment type definitions."""
from typing import Dict, Any, List, Literal, TypedDict, Tuple
from typing_extensions import NotRequired


class PolicyStatistics(TypedDict):
    """Statistics for repository policies."""
    total_policies: int
    blocking_policies: int
    enabled_policies: int
    policy_types: List[str]


class PolicyData(TypedDict):
    """Repository policy data."""
    repository_policies: List[Dict[str, Any]]
    branch_policies: List[Dict[str, Any]]
    statistics: PolicyStatistics


class BuildTestResult(TypedDict):
    """Build test result data."""
    total: int
    failed: int
    passed: int
    duration: float


class MetricThresholds(TypedDict):
    """Thresholds for metric levels."""
    level1: float
    level2: float
    level3: float
    level4: float
    level5: float


class BuildTestData(TypedDict):
    """Build test data."""
    # The 'tests' section contains a 'results' list of test summary dicts
    tests: Dict[str, List[Dict[str, int]]]


class DimensionScore(TypedDict):
    """Score and metadata for a dimension."""
    score: float
    level: int
    metrics: Dict[str, Any]
    explanation: List[str]


class AssessmentResult(TypedDict):
    """Assessment result for a dimension."""
    score: float
    level: int
    metrics: Dict[str, Any]
    explanation: List[str]
    # Optional presence signal to decouple data presence from score
    present: NotRequired[bool]


class OverallResult(TypedDict):
    """Overall assessment result."""
    score: float
    level: int
    label: str
    explanation: List[str]


class RepositoryAssessment(TypedDict, total=False):
    """Assessment results for a repository."""
    name: str
    # Minimal project metadata for grouping/exports
    project: Dict[str, Any]
    quality_assurance: AssessmentResult
    deployment_pipeline: AssessmentResult
    security_compliance: AssessmentResult
    collaboration_metrics: AssessmentResult
    overall: OverallResult


class ProjectAssessment(TypedDict, total=False):
    """Assessment results for a project."""
    quality_assurance: AssessmentResult
    deployment_pipeline: AssessmentResult
    security_compliance: AssessmentResult
    collaboration_metrics: AssessmentResult
    overall: OverallResult
    repositories: Dict[str, RepositoryAssessment]
    project_name: str


# Type aliases for dimension names
DimensionName = Literal[
    "quality_assurance",
    "deployment_pipeline",
    "security_compliance",
    "collaboration_metrics"
]

# Score tuple types
ScoreWeight = Tuple[float, float]
ScoreExplanation = Tuple[float, List[str]]

# Dimension names
QUALITY_ASSURANCE: DimensionName = "quality_assurance"
DEPLOYMENT_PIPELINE: DimensionName = "deployment_pipeline"
SECURITY_COMPLIANCE: DimensionName = "security_compliance"
COLLABORATION_METRICS: DimensionName = "collaboration_metrics"

# List of all dimension keys
DIMENSION_KEYS: List[DimensionName] = [
    QUALITY_ASSURANCE,
    DEPLOYMENT_PIPELINE,
    SECURITY_COMPLIANCE,
    COLLABORATION_METRICS
]


class AssessmentContext(TypedDict):
    """Context for assessment calculations."""
    start_date: str
    end_date: str
    project_name: str
    organization: str
    assessment_date: str
    assessment_type: str
