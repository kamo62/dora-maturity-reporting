"""Azure DevOps Metrics Collection Tool

This package provides tools for collecting and analyzing metrics from Azure DevOps.
"""

__version__ = "0.0.1"

# Removed direct imports of service classes from here to resolve circular dependency

from .config.settings import Settings

__all__ = [
    # Configuration
    "Settings",
]
