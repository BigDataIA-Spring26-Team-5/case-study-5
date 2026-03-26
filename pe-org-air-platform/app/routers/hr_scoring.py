"""
routers/hr_scoring.py — CS3 Task 6.1 Endpoints

Endpoints:
  POST /api/v1/scoring/hr/portfolio       — Compute H^R for all 5 companies

Already registered in main.py as hr_router.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import logging
import time

from app.core.dependencies import get_composite_scoring_service, get_company_repository
from app.services.composite_scoring_service import HRResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["CS3 H^R (Human Readiness)"])


# =====================================================================
# Response Models
# =====================================================================

class PortfolioHRResponse(BaseModel):
    """Portfolio H^R response."""
    status: str
    companies_scored: int
    companies_failed: int
    results: List[HRResponse]
    summary_table: List[Dict[str, Any]]
    duration_seconds: float


# =====================================================================
# POST /api/v1/scoring/hr/portfolio — Calculate H^R for all 5 companies
# =====================================================================

@router.post(
    "/hr/portfolio",
    response_model=PortfolioHRResponse,
    summary="Calculate H^R for all 5 CS3 portfolio companies",
    description="""
    Runs Task 6.1 (H^R calculation) for all 5 companies: NVDA, JPM, WMT, GE, DG.

    Pipeline for each company:
    1. Get Position Factor from PF endpoint (Task 6.0a)
    2. Get sector baseline H^R
    3. Calculate H^R = HR_base × (1 + 0.15 × PF)
    4. Validate against expected ranges

    Returns individual breakdowns + summary comparison table.
    """,
)
async def score_portfolio_hr(
    svc=Depends(get_composite_scoring_service),
    company_repo=Depends(get_company_repository),
):
    """Calculate H^R for all 5 companies."""
    start = time.time()

    logger.info("=" * 70)
    logger.info("H^R PORTFOLIO SCORING — 5 COMPANIES")
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
        result = svc.compute_hr(ticker)
        results.append(result)
        if result.status == "success":
            scored += 1
        else:
            failed += 1

    # Build summary table
    summary = []
    logger.info("")
    logger.info("=" * 70)
    logger.info("H^R SUMMARY TABLE")
    logger.info("=" * 70)
    logger.info(
        f"{'Ticker':<8} {'Sector':<20} {'HR Base':>9} {'PF':>8} "
        f"{'Adj':>8} {'H^R':>8} {'Range':>15} {'OK':>3}"
    )
    logger.info("-" * 70)

    for r in results:
        if r.status == "success" and r.hr_breakdown:
            b = r.hr_breakdown
            val_status = r.validation.status if r.validation else "-"
            range_str = r.validation.hr_expected if r.validation else "-"

            logger.info(
                f"{r.ticker:<8} {b.sector:<20} {b.hr_base:>9.2f} "
                f"{b.position_factor:>8.4f} {b.position_adjustment:>8.4f} "
                f"{b.hr_score:>8.2f} {range_str:>15} {val_status:>3}"
            )

            summary.append({
                "ticker": r.ticker,
                "sector": b.sector,
                "hr_base": b.hr_base,
                "position_factor": b.position_factor,
                "position_adjustment": b.position_adjustment,
                "hr_score": b.hr_score,
                "interpretation": b.interpretation,
                "hr_in_expected_range": r.validation.hr_in_range if r.validation else None,
            })
        else:
            logger.info(f"{r.ticker:<8} FAILED: {r.error}")
            summary.append({"ticker": r.ticker, "status": "failed", "error": r.error})

    logger.info("-" * 70)

    hr_pass = sum(1 for r in results if r.validation and r.validation.hr_in_range)
    hr_total = sum(1 for r in results if r.validation)

    logger.info(f"Scored: {scored}  Failed: {failed}")
    logger.info(f"H^R Validation: {hr_pass}/{hr_total} within expected range")
    logger.info(f"Duration: {time.time() - start:.2f}s")
    logger.info("=" * 70)

    return PortfolioHRResponse(
        status="success" if failed == 0 else "partial",
        companies_scored=scored,
        companies_failed=failed,
        results=results,
        summary_table=summary,
        duration_seconds=round(time.time() - start, 2),
    )
