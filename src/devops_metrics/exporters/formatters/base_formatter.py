"""Base formatter interface."""
from abc import ABC, abstractmethod
from typing import Any, Dict

from openpyxl.styles import PatternFill, Font, Alignment  # type: ignore


class BaseFormatter(ABC):
    """Base class for all cell formatters."""

    @abstractmethod
    def format_cell(self, value: Any) -> Dict[str, Any]:
        """Format a cell value.
        
        Args:
            value: Value to format
            
        Returns:
            Dictionary containing:
            - value: Formatted value
            - fill: Cell fill style
            - font: Cell font style
            - alignment: Cell alignment
            - number_format: Cell number format
        """


class ScoreFormatter(BaseFormatter):
    """Formatter for score values."""

    def format_cell(self, value: float) -> Dict[str, Any]:
        """Format a score value.
        
        Args:
            value: Score value (0-1)
            
        Returns:
            Formatted cell properties
        """
        # Color coding based on score
        if value >= 0.81:
            color = "92D050"  # Green
        elif value >= 0.61:
            color = "00B0F0"  # Blue
        elif value >= 0.41:
            color = "FFFF00"  # Yellow
        elif value >= 0.21:
            color = "FFC000"  # Orange
        else:
            color = "FF0000"  # Red

        return {
            "value": value,
            "fill": PatternFill(
                start_color=color,
                end_color=color,
                fill_type="solid"
            ),
            "font": Font(bold=False),
            "alignment": Alignment(horizontal="center"),
            "number_format": "0.00%"
        }


class ExplanationFormatter(BaseFormatter):
    """Formatter for explanation text."""

    def format_cell(self, value: str) -> Dict[str, Any]:
        """Format explanation text.
        
        Args:
            value: Explanation text
            
        Returns:
            Formatted cell properties
        """
        return {
            "value": value,
            "fill": PatternFill(
                start_color="FFFFFF",
                end_color="FFFFFF",
                fill_type="solid"
            ),
            "font": Font(bold=False),
            "alignment": Alignment(
                wrap_text=True,
                vertical="top",
                horizontal="left"
            ),
            "number_format": "@"  # Text format
        }


class HeaderFormatter(BaseFormatter):
    """Formatter for header cells."""

    def format_cell(self, value: str) -> Dict[str, Any]:
        """Format header text.
        
        Args:
            value: Header text
            
        Returns:
            Formatted cell properties
        """
        return {
            "value": value,
            "fill": PatternFill(
                start_color="E6E6E6",
                end_color="E6E6E6",
                fill_type="solid"
            ),
            "font": Font(bold=True),
            "alignment": Alignment(
                horizontal="center",
                vertical="center"
            ),
            "number_format": "@"  # Text format
        }
