"""Exporter factory."""
from typing import Dict, Type

from .base_exporter import BaseExporter
from .excel_exporter import ExcelExporter
from .json_exporter import JsonExporter
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ExporterFactory:
    """Factory for creating exporters."""

    _exporters: Dict[str, Type[BaseExporter]] = {
        "excel": ExcelExporter,
        "json": JsonExporter
    }

    @classmethod
    def create_exporter(cls, format_type: str) -> BaseExporter:
        """Create an exporter for the specified format.
        
        Args:
            format_type: Type of exporter to create ("excel" or "json")
            
        Returns:
            Exporter instance
            
        Raises:
            ValueError: If format_type is not supported
        """
        exporter_class = cls._exporters.get(format_type.lower())
        if not exporter_class:
            supported = ", ".join(cls._exporters.keys())
            raise ValueError(
                f"Unsupported export format: {format_type}. "
                f"Supported formats are: {supported}"
            )
        
        logger.debug(f"Creating {format_type} exporter")
        return exporter_class()

    @classmethod
    def register_exporter(cls, format_type: str, exporter_class: Type[BaseExporter]) -> None:
        """Register a new exporter type.
        
        Args:
            format_type: Format identifier for the exporter
            exporter_class: Exporter class to register
            
        Raises:
            ValueError: If format_type is already registered
        """
        if format_type.lower() in cls._exporters:
            raise ValueError(f"Exporter for format {format_type} is already registered")
        
        cls._exporters[format_type.lower()] = exporter_class
        logger.info(f"Registered new exporter for format: {format_type}")
