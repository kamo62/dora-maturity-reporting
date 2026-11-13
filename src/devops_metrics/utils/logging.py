"""Logging configuration for the application.

Respects environment variables:
- ADO_LOG_LEVEL: DEBUG|INFO|WARNING|ERROR|CRITICAL (default: INFO)
- ADO_LOG_JSON: true/false (default: false)
"""

import logging
import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
from devops_metrics.config.access import checked_path

# Configure logging format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Configure logging level from environment variable (fallback INFO)
_LEVEL_MAP = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
_env_level = os.getenv("ADO_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = _LEVEL_MAP.get(_env_level, logging.INFO)
LOG_JSON = os.getenv("ADO_LOG_JSON", "false").lower() in {"1", "true", "yes"}


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name.

    Args:
        name: Name of the logger

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # Only set up handlers if they haven't been set up yet
    if not logger.handlers:
        logger.setLevel(LOG_LEVEL)

        # Create logs directory if it doesn't exist
        logs_dir = checked_path("logs")
        logs_dir.mkdir(exist_ok=True)

        # Create a unique log file for this run
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"devops_metrics_{timestamp}.log"

        # Create file handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(LOG_LEVEL)

        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(LOG_LEVEL)

        # Create formatter and add it to the handlers
        if LOG_JSON:
            class JsonFormatter(logging.Formatter):
                def format(self, record: logging.LogRecord) -> str:
                    payload: Dict[str, Any] = {
                        "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                        "logger": record.name,
                        "level": record.levelname,
                        "message": record.getMessage(),
                    }
                    if record.exc_info:
                        try:
                            payload["exception"] = self.formatException(record.exc_info)
                        except Exception:
                            payload["exception"] = "<unavailable>"
                    if record.stack_info:
                        try:
                            payload["stack_info"] = self.formatStack(record.stack_info)
                        except Exception:
                            payload["stack_info"] = record.stack_info
                    return json.dumps(payload)

            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


def log_with_context(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Log a message with additional context.

    Args:
        logger: Logger instance
        level: Log level (e.g., logging.INFO)
        message: Log message
        **context: Additional context as keyword arguments
    """
    log_data = {"message": message, "timestamp": datetime.now(timezone.utc).isoformat(), **context}
    logger.log(level, log_data)


def log_event(logger: logging.Logger, event_type: str, data: Dict[str, Any]) -> None:
    """Log an event with structured data.

    Args:
        logger: Logger instance
        event_type: Type of event
        data: Event data
    """
    event = {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    logger.info(event)


def log_error(logger: logging.Logger, error: Exception, context: Dict[str, Any]) -> None:
    """Log an error with context.

    Args:
        logger: Logger instance
        error: Exception that occurred
        context: Error context
    """
    error_data = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "context": context,
    }
    logger.error(error_data)
