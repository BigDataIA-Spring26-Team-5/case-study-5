"""
routers/tc_vr_scoring.py — CS3 Task 5.0e + 5.2 Endpoints

Endpoints:
  POST /api/v1/scoring/tc-vr/portfolio     — Compute TC + V^R for all 5 CS3 companies

Already registered in main.py as tc_vr_router.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import structlog
import time

from app.core.dependencies import get_composite_scoring_service, get_company_repository
from app.services.composite_scoring_service import TCVRResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/scoring", tags=["CS3 TC + V^R Scoring"])


# =====================================================================
# Response Models
# =====================================================================

class PortfolioTCVRResponse(BaseModel):
    status: str
    companies_scored: int
    companies_failed: int
    results: List[TCVRResponse]
    summary_table: List[Dict[str, Any]]
    duration_seconds: float


# =====================================================================
# POST /api/v1/scoring/tc-vr/portfolio — Score all 5 CS3 companies
# =====================================================================

@router.post(
    "/tc-vr/portfolio",
    response_model=PortfolioTCVRResponse,
    summary="Compute TC + V^R for all 5 CS3 portfolio companies",
    description="""
    Runs Task 5.0e (Talent Concentration) + Task 5.2 (V^R) for:
    NVDA, JPM, WMT, GE, DG.

    Returns individual breakdowns + summary comparison table.
    Validates against CS3 Table 5 expected ranges.
    """,
)
async def score_portfolio_tc_vr(
    svc=Depends(get_composite_scoring_service),
    company_repo=Depends(get_company_repository),
):
    """Score all 5 companies — TC + V^R."""
    start = time.time()

    logger.info("=" * 70)
    logger.info("TC + V^R PORTFOLIO SCORING — 5 COMPANIES")
    logger.info("=" * 70)

    results = []
    scored = 0
    failed = 0

    rows = company_repo.get_all()
    tickers = [str(r.get("ticker") or "").upper() for r in rows or [] if r.get("ticker")]
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail="No companies found in the companies table. Ingest/register companies before portfolio scoring.",
        )

    for ticker in tickers:
        result = svc.compute_tc_vr(ticker)
        results.append(result)
        if result.status == "success":
            scored += 1
        else:
            failed += 1

    # Build summary table
    summary = []
    logger.info("")
    logger.info("=" * 70)
    logger.info("PORTFOLIO SUMMARY TABLE")
    logger.info("=" * 70)
    logger.info(
        f"{'Ticker':<8} {'TC':>8} {'TalentRiskAdj':>15} "
        f"{'WeightedDim':>13} {'V^R':>8} {'TC OK':>7} {'VR OK':>7}"
    )
    logger.info("-" * 70)

    for r in results:
        if r.status == "success":
            tc_ok = r.validation.tc_in_range if r.validation else "N/A"
            vr_ok = r.validation.vr_in_range if r.validation else "N/A"
            tc_sym = "OK" if tc_ok is True else ("WARN" if tc_ok is False else "-")
            vr_sym = "OK" if vr_ok is True else ("WARN" if vr_ok is False else "-")

            logger.info(
                f"{r.ticker:<8} {r.talent_concentration:>8.4f} "
                f"{r.vr_result.talent_risk_adj:>15.4f} "
                f"{r.vr_result.weighted_dim_score:>13.2f} "
                f"{r.vr_result.vr_score:>8.2f} "
                f"{tc_sym:>7} {vr_sym:>7}"
            )

            summary.append({
                "ticker": r.ticker,
                "talent_concentration": r.talent_concentration,
                "talent_risk_adj": r.vr_result.talent_risk_adj,
                "weighted_dim_score": r.vr_result.weighted_dim_score,
                "vr_score": r.vr_result.vr_score,
                "ai_jobs": r.job_analysis.total_ai_jobs if r.job_analysis else 0,
                "glassdoor_reviews": r.review_count or 0,
                "tc_in_expected_range": tc_ok if isinstance(tc_ok, bool) else None,
                "vr_in_expected_range": vr_ok if isinstance(vr_ok, bool) else None,
            })
        else:
            logger.info(f"{r.ticker:<8} FAILED: {r.error}")
            summary.append({"ticker": r.ticker, "status": "failed", "error": r.error})

    logger.info("-" * 70)
    logger.info(
        f"Scored: {scored}  Failed: {failed}  Duration: {time.time() - start:.2f}s"
    )
    logger.info("=" * 70)

    return PortfolioTCVRResponse(
        status="success" if failed == 0 else "partial",
        companies_scored=scored,
        companies_failed=failed,
        results=results,
        summary_table=summary,
        duration_seconds=round(time.time() - start, 2),
    )
