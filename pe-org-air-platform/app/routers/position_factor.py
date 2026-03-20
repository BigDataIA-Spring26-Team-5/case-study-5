"""
routers/position_factor.py — CS3 Task 6.0a Endpoints

Endpoints:
  POST /api/v1/scoring/pf/portfolio       — Compute PF for all 5 CS3 companies

Already registered in main.py as pf_router.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
import logging
import time

from app.config.company_mappings import CS3_PORTFOLIO
from app.core.dependencies import get_composite_scoring_service
from app.services.composite_scoring_service import PFResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["CS3 Position Factor"])


# =====================================================================
# Response Models
# =====================================================================

class PortfolioPFResponse(BaseModel):
    """Portfolio Position Factor response."""
    status: str
    companies_scored: int
    companies_failed: int
    results: List[PFResponse]
    summary_table: List[Dict[str, Any]]
    duration_seconds: float


# =====================================================================
# POST /api/v1/scoring/pf/portfolio — Calculate PF for all 5 companies
# =====================================================================

@router.post(
    "/pf/portfolio",
    response_model=PortfolioPFResponse,
    summary="Calculate Position Factor for all 5 CS3 portfolio companies",
    description="""
    Runs Task 6.0a (Position Factor) for all 5 companies: NVDA, JPM, WMT, GE, DG.

    Pipeline for each company:
    1. Get VR score from TC+VR endpoint (Task 5.2)
    2. Get market cap percentile (manual input)
    3. Calculate PF = 0.6 × VR_component + 0.4 × MCap_component
    4. Validate against CS3 Table 5 expected ranges

    Returns individual breakdowns + summary comparison table.
    """,
)
async def score_portfolio_pf(
    svc=Depends(get_composite_scoring_service),
):
    """Calculate Position Factor for all 5 companies."""
    start = time.time()

    logger.info("=" * 70)
    logger.info("POSITION FACTOR PORTFOLIO SCORING — 5 COMPANIES")
    logger.info("=" * 70)

    results = []
    scored = 0
    failed = 0

    for ticker in CS3_PORTFOLIO:
        result = svc.compute_pf(ticker)
        results.append(result)
        if result.status == "success":
            scored += 1
        else:
            failed += 1

    # Build summary table
    summary = []
    logger.info("")
    logger.info("=" * 70)
    logger.info("POSITION FACTOR SUMMARY TABLE")
    logger.info("=" * 70)
    logger.info(
        f"{'Ticker':<8} {'VR':>6} {'Sector Avg':>11} {'VR Comp':>9} "
        f"{'MCap %ile':>10} {'MCap Comp':>10} {'PF':>8} {'Range':>12} {'OK':>3}"
    )
    logger.info("-" * 70)

    for r in results:
        if r.status == "success" and r.pf_breakdown:
            b = r.pf_breakdown
            val_status = r.validation.status if r.validation else "-"
            range_str = r.validation.pf_expected if r.validation else "-"

            logger.info(
                f"{r.ticker:<8} {b.vr_score:>6.2f} {b.sector_avg_vr:>11.2f} "
                f"{b.vr_component:>9.4f} {b.market_cap_percentile:>10.2f} "
                f"{b.mcap_component:>10.4f} {b.position_factor:>8.4f} "
                f"{range_str:>12} {val_status:>3}"
            )

            summary.append({
                "ticker": r.ticker,
                "vr_score": b.vr_score,
                "sector_avg_vr": b.sector_avg_vr,
                "vr_component": b.vr_component,
                "market_cap_percentile": b.market_cap_percentile,
                "mcap_component": b.mcap_component,
                "position_factor": b.position_factor,
                "pf_in_expected_range": r.validation.pf_in_range if r.validation else None,
            })
        else:
            logger.info(f"{r.ticker:<8} FAILED: {r.error}")
            summary.append({"ticker": r.ticker, "status": "failed", "error": r.error})

    logger.info("-" * 70)

    pf_pass = sum(1 for r in results if r.validation and r.validation.pf_in_range)
    pf_total = sum(1 for r in results if r.validation)

    logger.info(f"Scored: {scored}  Failed: {failed}")
    logger.info(f"PF Validation: {pf_pass}/{pf_total} within expected range")
    logger.info(f"Duration: {time.time() - start:.2f}s")
    logger.info("=" * 70)

    return PortfolioPFResponse(
        status="success" if failed == 0 else "partial",
        companies_scored=scored,
        companies_failed=failed,
        results=results,
        summary_table=summary,
        duration_seconds=round(time.time() - start, 2),
    )
