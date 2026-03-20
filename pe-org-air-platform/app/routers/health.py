"""
Health Check Router - PE Org-AI-R Platform
app/routers/health.py

- /healthz: lightweight health check for platform (always 200) -> use for Render
- /health: deep dependency checks (Snowflake, Redis, S3) -> returns 200 or 503
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict
from datetime import datetime, timezone

from app.core.dependencies import get_health_repository

logger = structlog.get_logger()

router = APIRouter(tags=["Health"])


# -------------------------
# Schemas
# -------------------------

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    dependencies: Dict[str, str]


# -------------------------
# Dependency Health Checks
# -------------------------

async def check_snowflake(health_repo=None) -> str:
    """Check Snowflake connection health."""
    try:
        if health_repo is None:
            from app.repositories.health_repository import HealthRepository
            health_repo = HealthRepository()
        user, role = health_repo.ping()
        return f"healthy (User: {user}, Role: {role})"
    except Exception as e:
        msg = str(e)
        msg = (msg[:160] + "...") if len(msg) > 160 else msg
        return f"unhealthy: {msg}"


async def check_redis() -> str:
    """Check Redis connection health via cache singleton."""
    try:
        from app.services.cache import get_cache

        cache = get_cache()
        if cache is None:
            return "unhealthy: cache not available"
        cache.client.ping()
        return "healthy"

    except Exception as e:
        msg = str(e)
        msg = (msg[:160] + "...") if len(msg) > 160 else msg
        return f"unhealthy: {msg}"


async def check_s3() -> str:
    """Check AWS S3 connection health."""
    try:
        from botocore.exceptions import ClientError, NoCredentialsError
        from app.services.s3_storage import get_s3_service

        svc = get_s3_service()
        svc.s3_client.head_bucket(Bucket=svc.bucket_name)
        return f"healthy (Bucket: {svc.bucket_name})"

    except NoCredentialsError:
        return "unhealthy: AWS credentials not configured"
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        return f"unhealthy: AWS error - {code}"
    except Exception as e:
        msg = str(e)
        msg = (msg[:160] + "...") if len(msg) > 160 else msg
        return f"unhealthy: {msg}"


# -------------------------
# Routes
# -------------------------

@router.get("/healthz", summary="Lightweight health check (Render)")
def healthz():
    """Always returns 200. Use this for Render health check."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        200: {"description": "All dependencies healthy"},
        503: {"description": "One or more dependencies unhealthy"},
    },
    summary="Deep health check",
    description="Checks Snowflake, Redis, and S3 connectivity.",
)
async def health_check(health_repo=Depends(get_health_repository)):
    dependencies = {
        "snowflake": await check_snowflake(health_repo),
        "redis": await check_redis(),
        "s3": await check_s3(),
    }

    all_healthy = all(v.startswith("healthy") for v in dependencies.values())

    response = HealthResponse(
        status="healthy" if all_healthy else "degraded",
        timestamp=datetime.now(timezone.utc),
        version="1.0.0",
        dependencies=dependencies,
    )

    if all_healthy:
        return response

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=response.model_dump(mode="json"),
    )
