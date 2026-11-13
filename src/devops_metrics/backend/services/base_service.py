"""Base service interface for metrics collection"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..api.ado_client import AzureDevOpsClient


class MetricsService(ABC):
    """Abstract base class for all metrics services"""

    def __init__(self, ado_client: "AzureDevOpsClient", **kwargs: Any) -> None:
        """Initialize metrics service with Azure DevOps client

        Args:
            ado_client: Azure DevOps client instance
            **kwargs: Additional arguments for specific service implementations
        """
        self.ado_client = ado_client

    @abstractmethod
    async def collect_metrics(self) -> Dict[str, Any]:
        """Collect metrics from Azure DevOps

        Returns:
            Dictionary containing collected metrics
        """

    @abstractmethod
    async def get_cached_metrics(self) -> Optional[Dict[str, Any]]:
        """Get metrics from cache if available

        Returns:
            Dictionary containing cached metrics if available, None otherwise
        """
