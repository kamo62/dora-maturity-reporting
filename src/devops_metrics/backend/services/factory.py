"""Service Factory Module"""

from typing import Dict, Type, List, Any

from .base_service import MetricsService
from .raw_data_collector import RawDataCollector
from .raw_data_exporter import RawDataExporter
from .maturity_assessment_exporter import MaturityAssessmentExporter
from .raw_maturity_assessment import RawMaturityAssessmentService
from .segment_maturity_assessment import SegmentMaturityAssessmentService
from ..api.ado_client import AzureDevOpsClient


class MetricsServiceFactory:
    """Factory for creating metrics services"""

    _services: Dict[str, Type[MetricsService]] = {
        # Data services
        "raw": RawDataCollector,
        "export": RawDataExporter,
        "maturity_export": MaturityAssessmentExporter,
        "raw_maturity": RawMaturityAssessmentService,
        "segment_maturity": SegmentMaturityAssessmentService,
    }

    @classmethod
    def create_service(cls, service_type: str, ado_client: AzureDevOpsClient, **kwargs: Any) -> MetricsService:
        """Create a metrics service instance

        Args:
            service_type: Type of service to create
            ado_client: Azure DevOps client instance
            **kwargs: Additional arguments to pass to the service constructor

        Returns:
            MetricsService instance

        Raises:
            ValueError: If service type is not supported
        """
        if service_type not in cls._services:
            available_services = ", ".join(sorted(cls._services.keys()))
            raise ValueError(
                f"Unsupported service type: {service_type}. "
                f"Available services: {available_services}"
            )

        return cls._services[service_type](ado_client, **kwargs)

    @classmethod
    def get_available_services(cls) -> List[str]:
        """Get list of available service types

        Returns:
            List of service type strings
        """
        return sorted(cls._services.keys())
