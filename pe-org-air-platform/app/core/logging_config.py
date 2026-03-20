"""
Structured logging configuration — PE Org-AI-R Platform
app/core/logging_config.py

Configures structlog so every log entry emitted by a structlog logger
automatically includes correlation_id (from the ContextVar set by
CorrelationIdMiddleware) and a UTC ISO timestamp.

stdlib loggers (logging.getLogger) are redirected through basicConfig
at the same level so they interleave cleanly in the terminal.
"""
import logging
import structlog
from app.middleware.correlation import get_correlation_id


def add_correlation_id(logger, method_name, event_dict):
    """Structlog processor: injects correlation_id into every log entry."""
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(log_level: str = "INFO"):
    """Configure structlog for the application. Call once at startup."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            add_correlation_id,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),  # Switch to JSONRenderer in production
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Redirect stdlib logging through basicConfig at same level
    logging.basicConfig(
        format="%(message)s",
        level=logging.getLevelName(log_level),
    )
