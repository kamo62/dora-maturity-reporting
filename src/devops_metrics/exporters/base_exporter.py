"""Base exporter interface."""
from abc import ABC, abstractmethod
from typing import Dict, Any # Import Dict and Any

from ..types.export_types import ExportOptions, ExportResult
from ..types.assessment_types import ProjectAssessment # Keep ProjectAssessment for now, might be needed elsewhere


class BaseExporter(ABC):
    """Base class for all exporters."""

    @abstractmethod
    def export(self, data: Dict[str, Any], path: str, options: ExportOptions) -> ExportResult:
        """Export data to the specified format.
        
        Args:
            data: Assessment data to export
            path: Path to export to
            options: Export options
            
        Returns:
            Export result containing path and metadata
        """
