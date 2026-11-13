"""Assessor modules for different metric dimensions."""
from .quality_assurance import QualityAssuranceAssessor
from .deployment_pipeline import DeploymentPipelineAssessor
from .security_compliance import SecurityComplianceAssessor
from .collaboration_metrics import CollaborationMetricsAssessor
from typing import Union

Assessor = Union[
    QualityAssuranceAssessor,
    DeploymentPipelineAssessor,
    SecurityComplianceAssessor,
    CollaborationMetricsAssessor
]

__all__ = [
    'QualityAssuranceAssessor',
    'DeploymentPipelineAssessor',
    'SecurityComplianceAssessor',
    'CollaborationMetricsAssessor'
]
