# app/routers/dimensionScores.py

from datetime import datetime
from typing import Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.models.dimension import DIMENSION_WEIGHTS
from app.services.cache import get_cache, TTL_DIMENSION_WEIGHTS


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


# API ENDPOINTS


@router.get(
    "/dimensions/weights",
    summary="Get dimension weights configuration",
    description="Returns the current dimension weights configuration and validates they sum to 1.0",
    responses={
        200: {
            "description": "Weights configuration retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "weights": {
                            "data_infrastructure": 0.25,
                            "ai_governance": 0.20,
                            "technology_stack": 0.15,
                            "talent_skills": 0.15,
                            "leadership_vision": 0.10,
                            "use_case_portfolio": 0.10,
                            "culture_change": 0.05
                        },
                        "total": 1.0,
                        "is_valid": True,
                        "timestamp": "2024-01-15T10:30:00Z"
                    }
                }
            }
        },
        500: {
            "description": "Internal Server Error - Weights misconfigured",
            "content": {
                "application/json": {
                    "example": {
                        "error": "Internal Server Error",
                        "message": "Dimension weights misconfigured. Sum is 0.95, expected 1.0",
                        "timestamp": "2024-01-15T10:30:00Z"
                    }
                }
            }
        }
    }
)
async def get_dimension_weights() -> DimensionWeightsResponse:
    """Get the current dimension weights configuration."""
    cache_key = "dimension:weights"
    cache = get_cache()

    # Try cache first (with graceful failure)
    if cache:
        try:
            cached = cache.get(cache_key, DimensionWeightsResponse)
            if cached:
                return cached  # Cache hit!
        except Exception:
            pass  # Redis failed, continue

    try:
        total_weight = sum(DIMENSION_WEIGHTS.values())
        is_valid = 0.999 <= total_weight <= 1.001

        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "Internal Server Error",
                    "message": f"Dimension weights misconfigured. Sum is {total_weight}, expected 1.0",
                    "timestamp": datetime.utcnow().isoformat()
                }
            )

        response = DimensionWeightsResponse(
            weights={dim.value: weight for dim, weight in DIMENSION_WEIGHTS.items()},
            total=total_weight,
            is_valid=is_valid,
            timestamp=datetime.utcnow().isoformat()
        )

        # Cache the result
        if cache:
            try:
                cache.set(cache_key, response, TTL_DIMENSION_WEIGHTS)
            except Exception:
                pass  # Don't fail if cache write fails

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Internal Server Error",
                "message": f"Failed to retrieve weights configuration: {str(e)}",
                "timestamp": datetime.utcnow().isoformat()
            }
        )
