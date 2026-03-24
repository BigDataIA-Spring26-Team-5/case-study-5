"""
routers/orgair_scoring.py — CS3 Task 6.4 Endpoints

Endpoints:
  POST /api/v1/scoring/orgair/results         — Generate results/*.json for submission
  POST /api/v1/scoring/orgair/portfolio       — Compute Org-AI-R for all 5 CS3 companies
  GET  /api/v1/assessments/{ticker}           — Read-only assessment in CompanyAssessmentRead shape
"""

from fastapi import APIRouter, Depends
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID
import logging
import time

from app.config.company_mappings import CS3_PORTFOLIO
from app.core.dependencies import (
    get_company_repository,
    get_composite_scoring_repository,
    get_composite_scoring_service,
    get_scoring_repository,
    get_portfolio_data_service,
    get_document_collector_service,
    get_document_parsing_service,
    get_document_chunking_service,
    get_job_signal_service,
    get_patent_signal_service,
    get_tech_signal_service,
    get_leadership_service,
)
from app.core.errors import NotFoundError
from app.schemas.portfolio import (
    PortfolioOrgAIRRequest,
    PortfolioOrgAIRResponse,
    ResultsGenerationResponse,
    MAX_TICKERS_FOR_RANGE_ESTIMATION,
)
from app.schemas.scoring import CompanyAssessmentRead, DimensionScoreRead
from app.services.composite_scoring_service import OrgAIRResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scoring", tags=["CS3 Org-AI-R"])


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
    summary="Calculate Org-AI-R for a portfolio (tickers or CS1 fund_id)",
)
async def score_portfolio_orgair(
    req: Optional[PortfolioOrgAIRRequest] = None,
    svc=Depends(get_composite_scoring_service),
    portfolio_svc=Depends(get_portfolio_data_service),
    company_repo=Depends(get_company_repository),
    document_collector=Depends(get_document_collector_service),
    document_parser=Depends(get_document_parsing_service),
    document_chunker=Depends(get_document_chunking_service),
    job_signal_svc=Depends(get_job_signal_service),
    patent_signal_svc=Depends(get_patent_signal_service),
    tech_signal_svc=Depends(get_tech_signal_service),
    leadership_svc=Depends(get_leadership_service),
):
    """Calculate Org-AI-R for a set of companies."""
    start = time.time()

    logger.info("=" * 70)
    logger.info("Org-AI-R PORTFOLIO SCORING")
    logger.info("=" * 70)

    tickers: List[str]
    if req and req.tickers:
        tickers = [t.upper() for t in req.tickers if str(t).strip()]
    elif req and req.company_ids:
        tickers = []
        for raw_id in req.company_ids:
            try:
                company = company_repo.get_by_id(UUID(str(raw_id)))
                if company and company.get("ticker"):
                    tickers.append(str(company["ticker"]).upper())
            except Exception:
                continue
    elif req and req.fund_id:
        tickers = portfolio_svc.get_portfolio_tickers(req.fund_id)
    else:
        # Default: score the companies the platform knows about (companies table).
        # This avoids arbitrary tickers that aren't in Snowflake and therefore
        # can't have signals/docs persisted reliably.
        rows = company_repo.get_all()
        tickers = [str(r.get("ticker") or "").upper() for r in rows if r.get("ticker")]
        if req and (req.offset or req.limit):
            start_idx = max(0, int(req.offset or 0))
            end_idx = start_idx + int(req.limit) if req.limit else None
            tickers = tickers[start_idx:end_idx]
        if not tickers:
            tickers = list(CS3_PORTFOLIO)

    async def _maybe_await(fn: Callable, *args, **kwargs) -> Any:
        out = fn(*args, **kwargs)
        if hasattr(out, "__await__"):
            return await out
        return out

    async def _prepare_company(ticker: str) -> None:
        """Best-effort: generate missing CS2 signals + SEC chunks for scoring."""
        try:
            from app.services.scoring_service import get_scoring_service
            scoring_svc = get_scoring_service()
            prereqs = scoring_svc.check_scoring_prerequisites(ticker)
        except Exception as exc:
            logger.warning("Prereq check failed for %s: %s", ticker, exc)
            return

        if prereqs.get("ready"):
            return

        missing = prereqs.get("missing") or []
        missing_set = set(missing)

        # 1) CS2 signals
        categories = []
        for item in missing:
            if isinstance(item, str) and item.startswith("signal:"):
                categories.append(item.split(":", 1)[1])

        category_map = {
            "technology_hiring": (job_signal_svc, {"force_refresh": False}),
            "innovation_activity": (patent_signal_svc, {"years_back": 5}),
            "digital_presence": (tech_signal_svc, {"force_refresh": False}),
            "leadership_signals": (leadership_svc, {}),
        }

        for category in categories:
            entry = category_map.get(category)
            if not entry:
                continue
            svc_obj, kwargs = entry
            try:
                await _maybe_await(svc_obj.analyze_company, ticker, **kwargs)
            except Exception as exc:
                logger.warning("Signal collection failed: %s %s", category, exc)

        # 2) SEC docs → parsed → chunks
        if "document_chunks" in missing_set:
            try:
                from app.models.document import DocumentCollectionRequest

                await _maybe_await(
                    document_collector.collect_for_company,
                    DocumentCollectionRequest(ticker=ticker),
                )
                await _maybe_await(document_parser.parse_by_ticker, ticker)
                await _maybe_await(document_chunker.chunk_by_ticker, ticker, 750, 50)
            except Exception as exc:
                logger.warning("Document prep failed for %s: %s", ticker, exc)

    results = []
    scored = 0
    failed = 0

    for ticker in tickers:
        # If a user provided tickers explicitly, enforce that they exist in `companies`
        # so we can reliably create/read prerequisites and persist outputs.
        if req and req.tickers:
            try:
                if not company_repo.get_by_ticker(ticker):
                    results.append(OrgAIRResponse(
                        ticker=ticker,
                        status="failed",
                        error=(
                            f"Unknown ticker '{ticker}' (not found in companies table). "
                            f"Pick from GET /api/v1/companies or add the company first."
                        ),
                        duration_seconds=0.0,
                    ))
                    failed += 1
                    continue
            except Exception:
                pass

        result = svc.compute_orgair(ticker)

        # If user asked for dynamic tickers, try to prepare prerequisites automatically.
        if (
            result.status != "success"
            and req is not None
            and req.prepare_if_missing
        ):
            await _prepare_company(ticker)
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

    # Expected-range validation is only defined for the CS3 calibration set.
    # For other tickers we can estimate a helpful range.
    llm_router = None
    if req and req.estimate_ranges and req.range_strategy.lower() == "groq" and len(tickers) <= MAX_TICKERS_FOR_RANGE_ESTIMATION:
        try:
            from app.services.llm.router import get_llm_router
            llm_router = get_llm_router()
        except Exception:
            llm_router = None

    def _estimated_range_ci(r: OrgAIRResponse) -> Optional[tuple[float, float]]:
        if not r.breakdown or not r.breakdown.orgair_ci:
            return None
        low = float(r.breakdown.orgair_ci.ci_lower)
        high = float(r.breakdown.orgair_ci.ci_upper)
        if low > high:
            low, high = high, low
        low = max(0.0, min(100.0, low))
        high = max(0.0, min(100.0, high))
        if high - low < 1.0:
            # Avoid zero-width ranges for display.
            center = float(r.breakdown.org_air_score or 0.0)
            low = max(0.0, center - 2.0)
            high = min(100.0, center + 2.0)
        return low, high

    async def _estimated_range_groq(r: OrgAIRResponse) -> Optional[tuple[float, float]]:
        if llm_router is None or not r.breakdown:
            return None
        try:
            import json as _json

            b = r.breakdown
            ci = _estimated_range_ci(r)
            ci_low, ci_high = ci if ci else (None, None)
            prompt = {
                "ticker": r.ticker,
                "org_air_score": float(b.org_air_score),
                "vr_score": float(b.vr_score),
                "hr_score": float(b.hr_score),
                "synergy_score": float(b.synergy_score),
                "weighted_base": float(b.weighted_base),
                "synergy_contribution": float(b.synergy_contribution),
                "confidence_interval": {"low": ci_low, "high": ci_high},
            }

            messages = [
                {
                    "role": "system",
                    "content": (
                        "Estimate a reasonable expected score range for Org-AI-R for a ticker. "
                        "Return ONLY valid JSON with keys: low, high. Values are numbers between 0 and 100."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Estimate an expected Org-AI-R range for this company based only on these scores/CI. "
                        "Keep the range width reasonable (typically 8-20 points) and include the score.\n\n"
                        f"{_json.dumps(prompt)}"
                    ),
                },
            ]
            raw = await llm_router.complete("keyword_matching", messages, max_tokens=120)
            data = _json.loads(str(raw or "").strip())
            low = float(data.get("low"))
            high = float(data.get("high"))
            if low > high:
                low, high = high, low
            low = max(0.0, min(100.0, low))
            high = max(0.0, min(100.0, high))
            score = float(b.org_air_score)
            if not (low <= score <= high):
                low = min(low, score)
                high = max(high, score)
            if high - low < 5.0:
                mid = (high + low) / 2.0
                low = max(0.0, mid - 4.0)
                high = min(100.0, mid + 4.0)
            return low, high
        except Exception:
            return None

    est_within = 0
    est_total = 0

    for r in results:
        if r.status == "success" and r.breakdown:
            b = r.breakdown

            range_str = ""
            val_status = ""

            if r.validation:
                val_status = r.validation.status
                range_str = r.validation.orgair_expected
            else:
                strategy = (req.range_strategy if req else "none").lower()
                est = None
                if req and req.estimate_ranges and strategy in ("groq", "ci"):
                    est = await _estimated_range_groq(r) if strategy == "groq" else _estimated_range_ci(r)
                if est is None and req and req.estimate_ranges and strategy != "none":
                    est = _estimated_range_ci(r)

                if est is None:
                    range_str = "N/A"
                    val_status = "N/A"
                else:
                    low, high = est
                    range_str = f"{low:.1f} to {high:.1f}"
                    in_est = low <= float(b.org_air_score) <= high
                    val_status = "Y" if in_est else "N"
                    est_total += 1
                    if in_est:
                        est_within += 1

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
                "orgair_in_expected_range": (r.validation.orgair_in_range if r.validation else None),
            })
        else:
            logger.info(f"{r.ticker:<8} FAILED: {r.error}")
            summary.append({"ticker": r.ticker, "status": "failed", "error": r.error})

    logger.info("-" * 70)
    orgair_pass = sum(1 for r in results if r.validation and r.validation.orgair_in_range)
    orgair_total = sum(1 for r in results if r.validation)
    logger.info(f"Scored: {scored}  Failed: {failed}")
    if orgair_total == 0:
        if est_total:
            logger.info(f"Org-AI-R Validation: N/A (estimated ranges: {est_within}/{est_total} within)")
        else:
            logger.info("Org-AI-R Validation: N/A (no expected ranges configured for these tickers)")
    else:
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
