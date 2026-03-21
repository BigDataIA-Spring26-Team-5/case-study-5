"""
routers/orgair_scoring.py — CS3 Task 6.4 Endpoints

Endpoints:
  POST /api/v1/scoring/orgair/results         — Generate results/*.json for submission
  POST /api/v1/scoring/orgair/portfolio       — Compute Org-AI-R for all 5 CS3 companies
  GET  /api/v1/assessments/{ticker}           — Read-only assessment in CompanyAssessmentRead shape
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import logging
import time

from app.config.company_mappings import CS3_PORTFOLIO
from app.core.dependencies import (
    get_company_repository,
    get_composite_scoring_repository,
    get_composite_scoring_service,
    get_scoring_repository,
)
from app.core.errors import NotFoundError
from app.schemas.scoring import CompanyAssessmentRead, DimensionScoreRead
from app.services.composite_scoring_service import OrgAIRResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["CS3 Org-AI-R"])


# =====================================================================
# Response Models
# =====================================================================

class PortfolioOrgAIRResponse(BaseModel):
    status: str
    companies_scored: int
    companies_failed: int
    results: List[OrgAIRResponse]
    summary_table: List[Dict[str, Any]]
    duration_seconds: float


class ResultsGenerationResponse(BaseModel):
    status: str
    files_generated: int
    local_files: List[str]
    s3_files: List[str]
    summary: List[Dict[str, Any]]
    duration_seconds: float


# =====================================================================
# POST /api/v1/scoring/orgair/results — Generate results/*.json
# =====================================================================

@router.post(
    "/orgair/results",
    response_model=ResultsGenerationResponse,
    summary="Generate results/*.json files for CS3 submission",
    description="""
    Runs the full Org-AI-R pipeline for all 5 companies, then generates
    individual JSON result files (nvda.json, jpm.json, etc.) saved both
    locally in results/ and to S3 under scoring/results/.

    Each JSON contains: final Org-AI-R score, V^R, H^R, synergy,
    7 dimension scores, TC, PF, confidence intervals, job analysis,
    and validation against CS3 Table 5 expected ranges.
    """,
)
async def generate_results(
    svc=Depends(get_composite_scoring_service),
):
    """Generate results JSON files for CS3 submission."""
    result = svc.compute_full_pipeline(CS3_PORTFOLIO)
    return ResultsGenerationResponse(
        status="success",
        files_generated=result["files_generated"],
        local_files=result["local_files"],
        s3_files=result["s3_files"],
        summary=result["summary"],
        duration_seconds=result["duration_seconds"],
    )


# =====================================================================
# POST /api/v1/scoring/orgair/portfolio
# =====================================================================

@router.post(
    "/orgair/portfolio",
    response_model=PortfolioOrgAIRResponse,
    summary="Calculate Org-AI-R for all 5 CS3 portfolio companies",
)
async def score_portfolio_orgair(
    svc=Depends(get_composite_scoring_service),
):
    """Calculate Org-AI-R for all 5 companies."""
    start = time.time()

    logger.info("=" * 70)
    logger.info("Org-AI-R PORTFOLIO SCORING — 5 COMPANIES")
    logger.info("=" * 70)

    results = []
    scored = 0
    failed = 0

    for ticker in CS3_PORTFOLIO:
        result = svc.compute_orgair(ticker)
        results.append(result)
        if result.status == "success":
            scored += 1
        else:
            failed += 1

    summary = []
    logger.info("")
    logger.info("=" * 70)
    logger.info("Org-AI-R SUMMARY TABLE")
    logger.info("=" * 70)
    logger.info(
        f"{'Ticker':<8} {'V^R':>8} {'H^R':>8} {'Synergy':>9} "
        f"{'Org-AI-R':>10} {'Range':>15} {'OK':>3}"
    )
    logger.info("-" * 70)

    for r in results:
        if r.status == "success" and r.breakdown:
            b = r.breakdown
            val_status = r.validation.status if r.validation else "-"
            range_str = r.validation.orgair_expected if r.validation else "-"
            logger.info(
                f"{r.ticker:<8} {b.vr_score:>8.2f} {b.hr_score:>8.2f} "
                f"{b.synergy_score:>9.2f} {b.org_air_score:>10.2f} "
                f"{range_str:>15} {val_status:>3}"
            )
            summary.append({
                "ticker": r.ticker,
                "vr_score": b.vr_score,
                "hr_score": b.hr_score,
                "synergy_score": b.synergy_score,
                "org_air_score": b.org_air_score,
                "weighted_base": b.weighted_base,
                "synergy_contribution": b.synergy_contribution,
                "orgair_in_expected_range": (
                    r.validation.orgair_in_range if r.validation else None
                ),
            })
        else:
            logger.info(f"{r.ticker:<8} FAILED: {r.error}")
            summary.append({"ticker": r.ticker, "status": "failed", "error": r.error})

    logger.info("-" * 70)
    orgair_pass = sum(1 for r in results if r.validation and r.validation.orgair_in_range)
    orgair_total = sum(1 for r in results if r.validation)
    logger.info(f"Scored: {scored}  Failed: {failed}")
    logger.info(f"Org-AI-R Validation: {orgair_pass}/{orgair_total} within expected range")
    logger.info(f"Duration: {time.time() - start:.2f}s")
    logger.info("=" * 70)

    return PortfolioOrgAIRResponse(
        status="success" if failed == 0 else "partial",
        companies_scored=scored,
        companies_failed=failed,
        results=results,
        summary_table=summary,
        duration_seconds=round(time.time() - start, 2),
    )


# =====================================================================
# GET /api/v1/assessments/{ticker} — Read-only assessment
# =====================================================================

SCORE_LEVEL_THRESHOLDS = [
    (90, 5, "Leading"),
    (70, 4, "Advanced"),
    (50, 3, "Developing"),
    (30, 2, "Emerging"),
    (0, 1, "Nascent"),
]


def _score_to_level(score: float) -> tuple[int, str]:
    for threshold, level, name in SCORE_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level, name
    return 1, "Nascent"


assessment_router = APIRouter(prefix="/api/v1", tags=["Assessments"])


@assessment_router.get(
    "/assessments/{ticker}",
    response_model=CompanyAssessmentRead,
    summary="Get company assessment by ticker",
    description=(
        "Returns the full CompanyAssessmentRead shape for a company, "
        "including composite scores (Org-AI-R, V^R, H^R, TC, PF, synergy) "
        "and per-dimension scores read from Snowflake."
    ),
)
async def get_assessment(
    ticker: str,
    company_repo=Depends(get_company_repository),
    scoring_repo=Depends(get_scoring_repository),
    composite_repo=Depends(get_composite_scoring_repository),
):
    ticker = ticker.upper()

    # 1. Verify the company exists
    company = company_repo.get_by_ticker(ticker)
    if not company:
        raise NotFoundError("company", ticker)

    company_id = str(company["id"])

    # 2. Read composite scores from SCORING table
    # Snowflake DictCursor returns uppercase keys — normalise to lowercase
    def _norm(row):
        return {k.lower(): v for k, v in row.items()} if row else None

    tc_vr_row = _norm(composite_repo.fetch_tc_vr_row(ticker))
    orgair_row = _norm(composite_repo.fetch_orgair_row(ticker))

    tc      = float(tc_vr_row["tc"])  if tc_vr_row and tc_vr_row.get("tc")  is not None else 0.0
    vr      = float(tc_vr_row["vr"])  if tc_vr_row and tc_vr_row.get("vr")  is not None else 0.0
    pf      = float(tc_vr_row["pf"])  if tc_vr_row and tc_vr_row.get("pf")  is not None else 0.0
    hr      = float(tc_vr_row["hr"])  if tc_vr_row and tc_vr_row.get("hr")  is not None else 0.0
    org_air = float(orgair_row["org_air"]) if orgair_row and orgair_row.get("org_air") is not None else 0.0
    synergy = 0.0

    # Prefer the more detailed orgair row columns when available
    if orgair_row:
        if orgair_row.get("vr_score") is not None:
            vr = float(orgair_row["vr_score"])
        if orgair_row.get("hr_score") is not None:
            hr = float(orgair_row["hr_score"])
        if orgair_row.get("synergy_score") is not None:
            synergy = float(orgair_row["synergy_score"])

    scored_at = None
    if tc_vr_row and tc_vr_row.get("scored_at"):
        scored_at = str(tc_vr_row["scored_at"])
    elif orgair_row and orgair_row.get("scored_at"):
        scored_at = str(orgair_row["scored_at"])

    # 3. Read dimension scores from evidence_dimension_scores
    dim_rows = scoring_repo.get_dimension_scores(ticker)
    dimension_scores: Dict[str, DimensionScoreRead] = {}
    for row in dim_rows:
        dim_name = row["dimension"]
        score = float(row["score"]) if row.get("score") is not None else 0.0
        level, level_name = _score_to_level(score)
        dimension_scores[dim_name] = DimensionScoreRead(
            dimension=dim_name,
            score=score,
            level=level,
            level_name=level_name,
            confidence_interval=(0.0, 0.0),
            evidence_count=int(row.get("source_count", 0)),
        )

    return CompanyAssessmentRead(
        company_id=company_id,
        ticker=ticker,
        org_air_score=org_air,
        vr_score=vr,
        hr_score=hr,
        synergy_score=synergy,
        talent_concentration=tc,
        position_factor=pf,
        dimension_scores=dimension_scores,
        scored_at=scored_at,
    )
