"""
Custom Exceptions - PE Org-AI-R Platform
app/exceptions.py

Custom exception classes for repository operations.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request, status


# ---------------------------------------------------------------------------
# Shared raise_error helper (used by companies, industries, assessments routers)
# ---------------------------------------------------------------------------

def raise_error(status_code: int, error_code: str, message: str) -> None:
    # DEPRECATED: Use app.core.errors.PlatformError subclasses instead.
    """Raise an HTTPException with a standardised error detail dict."""
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": error_code,
            "message": message,
            "details": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Validation error handler (registered in main.py)
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

