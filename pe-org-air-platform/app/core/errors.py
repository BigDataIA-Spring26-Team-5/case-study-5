"""
Unified Error Hierarchy — PE Org-AI-R Platform
app/core/errors.py

PlatformError subclasses are raised by services and routers. A FastAPI
exception handler in main.py translates them to JSON responses with the
shape: {error_code, message, details, timestamp}.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class PlatformError(Exception):
    """Base platform exception. Carries message, error_code, and details dict."""

    def __init__(
        self,
        message: str,
        error_code: str = "PLATFORM_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.details = details
        super().__init__(message)


class NotFoundError(PlatformError):
    """Resource not found → 404."""

    def __init__(self, resource: str, identifier: Any, **kw: Any):
        self.resource = resource
        self.identifier = identifier
        super().__init__(
            message=f"{resource} not found: {identifier}",
            error_code=f"{resource.upper()}_NOT_FOUND",
            details={"resource": resource, "identifier": str(identifier), **kw},
        )


class ConflictError(PlatformError):
    """Conflict (duplicate, concurrent modification) → 409."""

    def __init__(self, message: str, error_code: str = "CONFLICT", **kw: Any):
        super().__init__(message=message, error_code=error_code, details=kw or None)


class ValidationError(PlatformError):
    """Domain validation failure → 422."""

    def __init__(self, message: str, error_code: str = "VALIDATION_FAILED", **kw: Any):
        super().__init__(message=message, error_code=error_code, details=kw or None)


class ExternalServiceError(PlatformError):
    """External dependency failure → 502."""

    def __init__(self, service: str, message: str, **kw: Any):
        self.service = service
        super().__init__(
            message=message,
            error_code=f"{service.upper()}_ERROR",
            details={"service": service, **kw},
        )


class PipelineIncompleteError(PlatformError):
    """A prerequisite pipeline step has not been run → 424."""

    def __init__(self, ticker: str, missing_steps: Optional[List[str]] = None, **kw: Any):
        self.ticker = ticker
        self.missing_steps = missing_steps or []
        steps_str = ", ".join(self.missing_steps) if self.missing_steps else "unknown"
        super().__init__(
            message=f"Pipeline incomplete for {ticker}: missing {steps_str}",
            error_code="PIPELINE_INCOMPLETE",
            details={"ticker": ticker, "missing_steps": self.missing_steps, **kw},
        )


class ScoringInProgressError(PlatformError):
    """Scoring is already running for this ticker → 409."""

    def __init__(self, ticker: str, **kw: Any):
        super().__init__(
            message=f"Scoring already in progress for {ticker}",
            error_code="SCORING_IN_PROGRESS",
            details={"ticker": ticker, **kw},
        )


# ---------------------------------------------------------------------------
# HTTP status mapping — used by the exception handler in main.py
# ---------------------------------------------------------------------------

ERROR_STATUS_MAP: Dict[type, int] = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    ExternalServiceError: 502,
    PipelineIncompleteError: 424,
    ScoringInProgressError: 409,
}
