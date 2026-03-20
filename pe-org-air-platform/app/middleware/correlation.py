"""
Correlation ID middleware — PE Org-AI-R Platform
app/middleware/correlation.py

Assigns a UUID4 to every request and propagates it through:
  - response header: X-Correlation-ID
  - request.state.correlation_id
  - correlation_id_var ContextVar (accessible from any async/sync code)
"""

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the correlation ID for the current request context."""
    return correlation_id_var.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

        token = correlation_id_var.set(correlation_id)
        request.state.correlation_id = correlation_id

        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)

        response.headers["X-Correlation-ID"] = correlation_id
        return response
