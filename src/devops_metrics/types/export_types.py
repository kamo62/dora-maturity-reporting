"""Export type definitions."""
from typing import Literal, TypedDict


class ExportOptions(TypedDict):
    """Options for export operations."""
    format: Literal["excel", "json"]
    include_charts: bool
    include_explanations: bool


class ExportResult(TypedDict):
    """Result of an export operation."""
    path: str
    format: str
    timestamp: str
