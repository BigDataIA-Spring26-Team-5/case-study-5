"""
CS3 Dimensions Scoring API Router
app/routers/scoring.py

Endpoints:
  POST /api/v1/scoring/{ticker}           — Score one company (full pipeline → Snowflake)
  POST /api/v1/scoring/all                — Score all companies with CS2 data
  GET  /api/v1/scoring/{ticker}/dimensions — View 7 dimension scores from Snowflake

Already registered in main.py as scoring_router.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import structlog
import time

from app.core.dependencies import get_scoring_service, get_scoring_repository
from app.core.errors import NotFoundError, PlatformError, PipelineIncompleteError, ScoringInProgressError, ExternalServiceError
from app.utils.serialization import serialize_row

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["CS3 Dimensions Scoring"])


# =====================================================================
# Response Models
# =====================================================================

class ScoringResponse(BaseModel):
    """Response from scoring a single company."""
    ticker: str
    company_id: Optional[str] = None
    status: str  # "success" or "failed"
    scored_at: Optional[str] = None
    dimension_scores: Optional[List[Dict[str, Any]]] = None
    mapping_matrix: Optional[List[Dict[str, Any]]] = None
    coverage: Optional[Dict[str, Any]] = None
    evidence_sources: Optional[Dict[str, Any]] = None
    persisted: bool = False
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


class AllScoringResponse(BaseModel):
    """Response from scoring all companies."""
    status: str
    companies_scored: int
    companies_failed: int
    results: List[ScoringResponse]
    duration_seconds: float


class DimensionScoresResponse(BaseModel):
    """Response for viewing dimension scores."""
    ticker: str
    scores: List[Dict[str, Any]]
    score_count: int


# =====================================================================
# Helpers
# =====================================================================



# =====================================================================
# POST /api/v1/scoring/all — Score all companies
# NOTE: This MUST be defined BEFORE /scoring/{ticker} so FastAPI
#       matches the static "/all" path before the dynamic "{ticker}".
# =====================================================================

@router.post(
    "/scoring/all",
    response_model=AllScoringResponse,
    summary="Score all companies with CS2 data",
    description="""
    Runs the CS3 Dimensions Scoring pipeline for every company that has a signal summary
    in `company_signal_summaries`. Returns individual results for each.
    """,
    tags=["CS3 Dimensions Scoring"],
)
async def score_all_companies(
    service=Depends(get_scoring_service),
):
    """Score all companies."""
    start = time.time()

    try:
        results = service.score_all_companies()

        responses = []
        scored = 0
        failed = 0
        for r in results:
            if r.get("error"):
                failed += 1
                responses.append(ScoringResponse(
                    ticker=r["ticker"],
                    status="failed",
                    error=r["error"],
                ))
            else:
                scored += 1
                responses.append(ScoringResponse(
                    ticker=r["ticker"],
                    company_id=r.get("company_id"),
                    status="success",
                    scored_at=r.get("scored_at"),
                    dimension_scores=r.get("dimension_scores"),
                    persisted=r.get("persisted", False),
                ))

        return AllScoringResponse(
            status="completed",
            companies_scored=scored,
            companies_failed=failed,
            results=responses,
            duration_seconds=round(time.time() - start, 2),
        )
    except Exception as e:
        logger.error("scoring_all_failed", error=str(e), exc_info=True)
        raise ExternalServiceError("scoring", "Failed to score all companies.")


# =====================================================================
# GET /api/v1/scoring/{ticker}/prerequisites — Check CS2 data readiness
# NOTE: Must be registered before POST /scoring/{ticker} to avoid conflicts.
# =====================================================================

@router.get(
    "/scoring/{ticker}/prerequisites",
    summary="Check CS2 data prerequisites for scoring",
    description="Check whether all CS2 signal data and document chunks required for scoring exist.",
    tags=["CS3 Dimensions Scoring"],
)
async def get_scoring_prerequisites(
    ticker: str,
    service=Depends(get_scoring_service),
):
    """Check whether all CS2 data required for scoring exists."""
    ticker = ticker.upper()
    return service.check_scoring_prerequisites(ticker)


# =====================================================================
# POST /api/v1/scoring/{ticker} — Score one company
# =====================================================================

@router.post(
    "/scoring/{ticker}",
    response_model=ScoringResponse,
    summary="Score a company (CS3 full pipeline)",
    description="""
    Runs the full CS3 Dimensions Scoring pipeline for a single company:

    1. **Reads CS2 signals** from `company_signal_summaries` (hiring, innovation, digital, leadership)
    2. **Reads SEC sections** from `document_chunks` + S3 (Item 1, 1A, 7)
    3. **Rubric scores** SEC text against 7-dimension rubrics (Task 5.0b)
    4. **Maps evidence** to 7 dimensions using weighted matrix (Task 5.0a)
    5. **Persists** mapping matrix + dimension scores to Snowflake

    **Prerequisite:** Company must have CS2 signal data (run signal scoring first).
    """,
    tags=["CS3 Dimensions Scoring"],
)
async def score_company(
    ticker: str,
    service=Depends(get_scoring_service),
):
    """Score one company — full CS3 pipeline."""
    start = time.time()
    ticker = ticker.upper()

    try:
        result = service.score_company(ticker)

        return ScoringResponse(
            ticker=ticker,
            company_id=result.get("company_id"),
            status="success",
            scored_at=result.get("scored_at"),
            dimension_scores=result.get("dimension_scores"),
            mapping_matrix=result.get("mapping_matrix"),
            coverage=result.get("coverage"),
            evidence_sources=result.get("evidence_sources"),
            persisted=result.get("persisted", False),
            duration_seconds=round(time.time() - start, 2),
        )
    except PlatformError:
        raise  # let middleware translate to 424/409/404/etc.
    except Exception as e:
        logger.error(f"scoring_unexpected_error ticker=%s error=%s", ticker, e, exc_info=True)
        return ScoringResponse(
            ticker=ticker,
            status="failed",
            error=str(e),
            duration_seconds=round(time.time() - start, 2),
        )


# =====================================================================
# GET /api/v1/scoring/{ticker}/dimensions — View dimension scores
# =====================================================================

@router.get(
    "/scoring/{ticker}/dimensions",
    response_model=DimensionScoresResponse,
    summary="View 7 dimension scores for a company",
    description="""
    Returns the 7 aggregated dimension scores for a company from Snowflake.

    **Equivalent Snowflake query:**
    ```sql
    SELECT * FROM evidence_dimension_scores WHERE ticker = '{ticker}'
    ```
    """,
    tags=["CS3 Dimensions Scoring"],
)
async def get_dimension_scores(
    ticker: str,
    repo=Depends(get_scoring_repository),
):
    """View dimension scores from Snowflake."""
    ticker = ticker.upper()

    try:
        latest_run = repo.get_latest_scoring_run(ticker)
        if latest_run and latest_run["status"] == "running":
            raise ScoringInProgressError(ticker=ticker, run_id=latest_run["run_id"])
        if latest_run and latest_run["status"] == "failed":
            raise PipelineIncompleteError(
                ticker=ticker,
                missing_steps=[f"scoring (last run failed: {latest_run.get('error_message', 'unknown')})"],
            )

        rows = repo.get_dimension_scores(ticker)

        if not rows:
            raise NotFoundError("dimension_scores", ticker)

        clean_rows = [serialize_row(r) for r in rows]

        return DimensionScoresResponse(
            ticker=ticker,
            scores=clean_rows,
            score_count=len(clean_rows),
        )
    except PlatformError:
        raise  # let middleware translate to 404/409/424/etc.
    except Exception as e:
        if hasattr(e, 'status_code'):
            raise
        logger.error("get_dimensions_failed", ticker=ticker, error=str(e))
        raise PlatformError("Failed to retrieve dimension scores.", "FETCH_FAILED")
