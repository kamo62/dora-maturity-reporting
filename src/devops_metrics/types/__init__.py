"""Type definitions for the devops_metrics package."""
from .data_types import (
    RawData as RawProjectData,  # Alias for backward compatibility
    RawData,
    RawDataRequired,
    RawDataKey,
    ProjectInfo,
    ConsolidatedData,
    RepositoryData,
    CollectedRepositoryData,
    validate_project_info,
    create_project_info,
    merge_project_info,
    validate_consolidated_data
)
from .assessment_types import (
    AssessmentResult,
    ProjectAssessment,
    BuildTestData,
    DimensionScore,
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
from .export_types import (
    ExportOptions,
    ExportResult
)

__all__ = [
    # Data types
    'RawProjectData',  # Backward compatibility alias
    'RawData',
    'RawDataRequired',
    'RawDataKey',
    'ProjectInfo',
    'ConsolidatedData',
    'RepositoryData',
    'CollectedRepositoryData',
    'validate_project_info',
    'create_project_info',
    'merge_project_info',
    'validate_consolidated_data',
    
    # Assessment types
    'AssessmentResult',
    'ProjectAssessment',
    'BuildTestData',
    'DimensionScore',
    'DimensionName',
    'RepositoryAssessment',
    'OverallResult',
    'ScoreWeight',
    'ScoreExplanation',
    'QUALITY_ASSURANCE',
    'DEPLOYMENT_PIPELINE',
    'SECURITY_COMPLIANCE',
    'COLLABORATION_METRICS',
    'DIMENSION_KEYS',
    
    # Export types
    'ExportOptions',
    'ExportResult'
]
