"""
Unified Error Hierarchy — PE Org-AI-R Platform
app/core/errors.py

PlatformError subclasses are raised by services and routers. A FastAPI
exception handler in main.py translates them to JSON responses with the
shape: {error_code, message, details, timestamp}.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request, status


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


# ---------------------------------------------------------------------------
# Validation exception handler (registered in main.py)
# ---------------------------------------------------------------------------

FIELD_MESSAGES = {
    "name": {
        "missing": "Company name is required",
        "string_too_short": "Company name cannot be empty",
        "string_too_long": "Company name must not exceed 255 characters",
        "string_type": "Company name must be a string",
    },
    "ticker": {
        "string_too_long": "Ticker symbol must not exceed 10 characters",
        "string_pattern_mismatch": "Ticker symbol must contain only uppercase letters (A-Z)",
        "string_type": "Ticker symbol must be a string",
    },
    "industry_id": {
        "missing": "Industry ID is required",
        "uuid_parsing": "Industry ID must be a valid UUID format",
        "uuid_type": "Industry ID must be a valid UUID",
    },
    "position_factor": {
        "less_than_equal": "Position factor must be between -1.0 and 1.0",
        "greater_than_equal": "Position factor must be between -1.0 and 1.0",
        "float_type": "Position factor must be a number",
        "float_parsing": "Position factor must be a valid number",
    },
}

DEFAULT_MESSAGES = {
    "missing": "Field '{field}' is required",
    "string_too_short": "Field '{field}' is too short",
    "string_too_long": "Field '{field}' is too long",
    "string_pattern_mismatch": "Field '{field}' has invalid format",
    "less_than_equal": "Field '{field}' exceeds maximum allowed value",
    "greater_than_equal": "Field '{field}' is below minimum allowed value",
    "uuid_parsing": "Field '{field}' must be a valid UUID",
    "uuid_type": "Field '{field}' must be a valid UUID",
    "string_type": "Field '{field}' must be a string",
    "float_type": "Field '{field}' must be a number",
    "float_parsing": "Field '{field}' must be a valid number",
    "int_type": "Field '{field}' must be an integer",
    "int_parsing": "Field '{field}' must be a valid integer",
    "json_invalid": "Malformed JSON request body",
    "extra_forbidden": "Unknown field '{field}' is not allowed",
}


def get_validation_message(field: str, error_type: str) -> str:
    if field in FIELD_MESSAGES:
        for key in FIELD_MESSAGES[field]:
            if key in error_type:
                return FIELD_MESSAGES[field][key]
    for key, template in DEFAULT_MESSAGES.items():
        if key in error_type:
            return template.format(field=field)
    return f"Invalid value for field '{field}'"


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if not errors:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    err = errors[0]
    error_type = err.get("type", "")
    loc = err.get("loc", [])
    if "json_invalid" in error_type:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error_code": "INVALID_REQUEST",
                "message": "Malformed JSON request body",
                "details": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    field = ".".join(str(l) for l in loc if l != "body")
    message = get_validation_message(field, error_type)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": message,
            "details": {"field": field, "type": error_type} if field else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
