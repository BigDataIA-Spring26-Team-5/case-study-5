# app/routers/dimension_scores.py

from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.dimension import DIMENSION_WEIGHTS
from app.services.cache import cached_query, TTL_DIMENSION_WEIGHTS
from app.core.errors import PlatformError


# ROUTER CONFIGURATION


router = APIRouter(
    prefix="/api/v1",
    tags=["Dimension Scores"]
)


# SCHEMA


class DimensionWeightsResponse(BaseModel):
    """Response schema for dimension weights configuration"""
    weights: Dict[str, float]
    total: float
    is_valid: bool
    timestamp: str


# HELPER FUNCTIONS


def validate_weights_sum_to_one():
    """Standalone function to validate DIMENSION_WEIGHTS sum to 1.0"""
    total = sum(DIMENSION_WEIGHTS.values())
    if not (0.999 <= total <= 1.001):
        raise ValueError(f"DIMENSION_WEIGHTS must sum to 1.0, but got {total}.")
    return True


# Validate weights sum to 1.0 at module load time
validate_weights_sum_to_one()


def _build_weights_response() -> DimensionWeightsResponse:
    """Build a DimensionWeightsResponse from the current DIMENSION_WEIGHTS."""
    total_weight = sum(DIMENSION_WEIGHTS.values())
    is_valid = 0.999 <= total_weight <= 1.001

    if not is_valid:
        raise PlatformError(
            f"Dimension weights misconfigured. Sum is {total_weight}, expected 1.0",
            "WEIGHTS_MISCONFIGURED",
        )

    return DimensionWeightsResponse(
        weights={dim.value: weight for dim, weight in DIMENSION_WEIGHTS.items()},
        total=total_weight,
        is_valid=is_valid,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# API ENDPOINTS


@router.get(
    "/dimensions/weights",
    summary="Get dimension weights configuration",
    description="Returns the current dimension weights configuration and validates they sum to 1.0",
)
async def get_dimension_weights() -> DimensionWeightsResponse:
    """Get the current dimension weights configuration."""
    try:
        result, hit, latency = cached_query(
            key="dimension:weights",
            ttl=TTL_DIMENSION_WEIGHTS,
            model_type=DimensionWeightsResponse,
            fallback_fn=_build_weights_response,
        )
        return result
    except Exception as e:
        if hasattr(e, 'status_code'):
            raise
        raise PlatformError("Failed to retrieve weights configuration.", "INTERNAL_ERROR")
