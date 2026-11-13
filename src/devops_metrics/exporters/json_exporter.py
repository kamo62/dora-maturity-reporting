"""JSON exporter implementation."""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any # Ensure Dict and Any are imported

from .base_exporter import BaseExporter
from ..types.export_types import ExportOptions, ExportResult
from ..types.assessment_types import ProjectAssessment
from ..utils.logging import get_logger

logger = get_logger(__name__)


class JsonExporter(BaseExporter):
    """JSON exporter implementation."""

    def export(self, data: Dict[str, Any], path: str, options: ExportOptions) -> ExportResult:
        """Export assessment data to JSON.
        
        Args:
            data: Assessment data to export
            path: Path to export to
            options: Export options
            
        Returns:
            Export result
        """
        try:
            # Create output directory if needed
            output_path = Path(path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Export to JSON
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2, default=str)  # Use str for non-serializable objects

            logger.info(f"Assessment exported to JSON: {output_path}")

            return {
                "path": str(output_path),
                "format": "json",
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error exporting to JSON: {str(e)}")
            raise
