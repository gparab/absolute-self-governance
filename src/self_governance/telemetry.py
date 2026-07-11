"""Telemetry Logging and Context Tracking module.

Provides structured logging utilities, Context local variables for tracking request correlation IDs,
and custom formatters/handlers setup.
"""

import os
import logging
import json
import uuid
from datetime import datetime, timezone
import contextvars

# Context Local Variable for Correlation ID
correlation_id_var = contextvars.ContextVar("correlation_id", default="")

# Fields callers may attach via logger.info(..., extra={...}) that should
# flow into structured JSON output when present on the record.
_STRUCTURED_EXTRA_FIELDS = ("tenant_id", "event_type", "duration_ms")


class StructuredJSONFormatter(logging.Formatter):
    """Formats log records as JSON dictionaries including correlation IDs."""

    def format(self, record: logging.LogRecord) -> str:
        """Formats a LogRecord as a JSON string.

        Args:
            record: The logging event record.

        Returns:
            The JSON-formatted log string.
        """
        log_obj = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get() or "",
        }
        for field in _STRUCTURED_EXTRA_FIELDS:
            if hasattr(record, field):
                log_obj[field] = getattr(record, field)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def get_correlation_id() -> str:
    """Retrieves the current thread-local correlation ID.

    Returns:
        The correlation ID string.
    """
    return correlation_id_var.get()


def set_correlation_id(cid: str) -> None:
    """Assigns the current thread-local correlation ID.

    Args:
        cid: The correlation ID string to set.
    """
    correlation_id_var.set(cid)


def new_correlation_id() -> str:
    """Generates a new UUIDv4 and sets it as the active correlation ID.

    Returns:
        The generated correlation ID string.
    """
    cid = str(uuid.uuid4())
    set_correlation_id(cid)
    return cid


def setup_telemetry(json_logging: bool = False) -> None:
    """Configures root logging with custom structured formatters.

    Args:
        json_logging: If True, uses StructuredJSONFormatter, else ContextFormatter.
    """
    if os.getenv("TESTING") == "True":
        return
    root_logger = logging.getLogger()

    # Remove existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    if json_logging:
        handler.setFormatter(StructuredJSONFormatter())
    else:
        # Standard readable formatting (injecting correlation ID if present)
        class ContextFormatter(logging.Formatter):
            """Log Formatter injecting thread-local correlation IDs."""
            def format(self, record):
                cid = correlation_id_var.get()
                prefix = f"[{cid}] " if cid else ""
                formatted = super().format(record)
                return f"{prefix}{formatted}"

        handler.setFormatter(
            ContextFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

