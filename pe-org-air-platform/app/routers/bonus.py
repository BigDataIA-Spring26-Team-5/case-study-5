"""CS5 Bonus (+20) feature endpoints.

Implements the CS5 extensions section:
- Mem0 semantic memory
- Investment tracker with ROI
- IC memo generator (PDF)
- LP letter generator (PDF)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from app.core.dependencies import (
    get_assessment_snapshot_repository,
    get_composite_scoring_repository,
    get_scoring_repository,
    get_portfolio_data_service,
)
from app.core.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/api/v1/bonus", tags=["CS5 — Bonus"])


def _reports_dir() -> str:
    base = os.path.join("results", "reports")
    os.makedirs(base, exist_ok=True)
    return base


def _reports_subdir(name: str) -> str:
    base = _reports_dir()
    sub = os.path.join(base, name)
    os.makedirs(sub, exist_ok=True)
    return sub


def _safe_ticker(ticker: str) -> str:
    return (ticker or "").upper().strip()


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


@router.get("/roi/{ticker}", summary="Compute AI-driven ROI projection for a company")
async def compute_roi(
    ticker: str,
    entry_org_air: Optional[float] = Query(
        None, description="Optional override for entry Org-AI-R score"
    ),
    entry_price: Optional[float] = Query(
        None, description="Optional override for entry price (not required for ROI estimate)"
    ),
    composite_repo=Depends(get_composite_scoring_repository),
    snapshot_repo=Depends(get_assessment_snapshot_repository),
):
    from app.services.tracking.investment_tracker import investment_tracker

    ticker_u = _safe_ticker(ticker)
    row = composite_repo.fetch_orgair_row(ticker_u)
    if not row:
        raise NotFoundError("scoring_row", ticker_u)
    r = {k.lower(): v for k, v in row.items()}
    current_org_air = float(r.get("org_air") or 0.0)
    if current_org_air <= 0:
        raise ConflictError(f"Org-AI-R not computed for {ticker_u} yet")

    entry = entry_org_air
    if entry is None:
        try:
            entry = snapshot_repo.get_entry_org_air(ticker=ticker_u, portfolio_id=None)
        except Exception:
            entry = None

    roi = investment_tracker.compute_roi(
        ticker_u,
        current_org_air=current_org_air,
        entry_org_air=entry,
        entry_price=entry_price,
    )
    return roi.to_dict()


@router.get("/memory/{ticker}", summary="Recall Mem0 semantic memory for a company")
async def recall_memory(
    ticker: str,
    query: str = Query("prior due diligence", description="Memory search query"),
    debug: bool = Query(False, description="Include Mem0 debug status"),
):
    from app.agents.memory import agent_memory

    ticker_u = _safe_ticker(ticker)
    items = agent_memory.recall(ticker_u, query)
    payload: Dict[str, Any] = {"ticker": ticker_u, "query": query, "items": items}
    if debug:
        payload["mem0"] = agent_memory.debug_status()
    return payload


@router.post("/reports/ic-memo/{ticker}", summary="Generate IC memo (.docx) for a company")
async def generate_ic_memo(
    ticker: str,
    target_org_air: float = Query(85.0, description="Target Org-AI-R for gap analysis / value creation"),
    format: str = Query(
        "docx",
        description="Output format: docx (recommended), pdf, or txt",
        pattern="^(docx|pdf|txt)$",
    ),
    persist: bool = Query(
        True,
        description=(
            "When true, keep a server-side copy under results/reports/ic_memo/. "
            "When false, delete it after download (useful when downloading locally to avoid duplicates)."
        ),
    ),
    background: BackgroundTasks = None,
    portfolio_svc=Depends(get_portfolio_data_service),
    composite_repo=Depends(get_composite_scoring_repository),
    scoring_repo=Depends(get_scoring_repository),
):
    from app.services.reporting.ic_memo import ic_memo_generator

    ticker_u = _safe_ticker(ticker)

    row = composite_repo.fetch_orgair_row(ticker_u)
    if not row:
        raise NotFoundError("scoring_row", ticker_u)
    r = {k.lower(): v for k, v in row.items()}
    scoring_result: Dict[str, Any] = {
        "company_id": ticker_u,
        "org_air": float(r.get("org_air") or 0.0),
        "vr_score": float(r.get("vr_score") or 0.0),
        "hr_score": float(r.get("hr_score") or 0.0),
        "synergy_score": float(r.get("synergy_score") or 0.0),
        "confidence_interval": [
            float(r.get("ci_lower") or 0.0),
            float(r.get("ci_upper") or 0.0),
        ],
        "dimension_scores": {},
    }

    try:
        dims = scoring_repo.get_dimension_scores(ticker_u)
        scoring_result["dimension_scores"] = {
            d.get("dimension"): float(d.get("score") or 0.0) for d in dims
        }
    except Exception:
        scoring_result["dimension_scores"] = {}

    # If dimension scores empty, try direct repo
    if not scoring_result["dimension_scores"]:
        try:
            from app.repositories.scoring_repository import ScoringRepository
            direct_repo = ScoringRepository()
            dims = direct_repo.get_dimension_scores(ticker_u)
            scoring_result["dimension_scores"] = {
                d.get("dimension"): float(d.get("score") or 0.0) for d in dims
            }
        except Exception:
            pass

    # Gap analysis — use GapAnalyzer directly with dimension scores
    dim_scores = scoring_result.get("dimension_scores", {})
    current_air = float(scoring_result.get("org_air") or 0)
    if dim_scores:
        try:
            from app.services.value_creation.gap_analysis import GapAnalyzer
            gap_result = GapAnalyzer().analyze(
                company_id=ticker_u,
                dimension_scores=dim_scores,
                current_org_air=current_air,
                target_org_air=float(target_org_air),
            )
            gap_analysis = gap_result.to_dict() if hasattr(gap_result, "to_dict") else gap_result
        except Exception:
            gap_analysis = portfolio_svc.run_gap_analysis(ticker_u, float(target_org_air))
    else:
        gap_analysis = portfolio_svc.run_gap_analysis(ticker_u, float(target_org_air))

    # Use entry score from history for EBITDA projection (shows improvement achieved)
    entry_score = float(scoring_result["org_air"] or 0.0)
    try:
        from app.repositories.assessment_snapshot_repository import AssessmentSnapshotRepository
        snap_repo = AssessmentSnapshotRepository()
        snaps = snap_repo.list_snapshots(ticker=ticker_u, days=3650)
        if snaps:
            entry_score = float(snaps[0].get("org_air") or entry_score)
    except Exception:
        pass

    ebitda_raw = portfolio_svc.project_ebitda_impact(
        company_id=ticker_u,
        entry_score=entry_score,
        target_score=float(target_org_air),
        h_r_score=float(scoring_result["hr_score"] or 0.0),
    )
    # Map EBITDA calculator fields to what IC memo generator expects
    ebitda_projection = {
        **ebitda_raw,
        "risk_adjusted": f"{ebitda_raw.get('adjusted_net_impact_pct', 0):.2f}%",
        "delta_air": f"{ebitda_raw.get('score_improvement', 0):.1f}",
        "scenarios": {
            "conservative": f"{ebitda_raw.get('net_impact_pct', 0) * 0.6:.2f}%",
            "base": f"{ebitda_raw.get('net_impact_pct', 0):.2f}%",
            "optimistic": f"{ebitda_raw.get('ebitda_impact_pct', 0):.2f}%",
        },
        "requires_approval": ebitda_raw.get('adjusted_net_impact_pct', 0) > 5.0,
    }

    fmt = (format or "docx").lower().strip()
    ext = "docx" if fmt not in ("pdf", "txt") else fmt
    out_path = os.path.join(
        _reports_subdir("ic_memo"),
        f"ic_memo_{ticker_u}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.{ext}",
    )
    path = ic_memo_generator.generate(
        company_id=ticker_u,
        scoring_result=scoring_result,
        gap_analysis=gap_analysis,
        ebitda_projection=ebitda_projection,
        output_path=out_path,
        output_format=fmt,
    )

    if not persist:
        if background is None:
            background = BackgroundTasks()
        background.add_task(_safe_remove, path)
    media_type = None
    if path.lower().endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif path.lower().endswith(".pdf"):
        media_type = "application/pdf"
    elif path.lower().endswith(".txt"):
        media_type = "text/plain"
    return FileResponse(path, filename=os.path.basename(path), background=background, media_type=media_type)


@router.post("/reports/lp-letter/{fund_id}", summary="Generate LP letter (.pdf) for a fund")
async def generate_lp_letter(
    fund_id: str,
    persist: bool = Query(
        True,
        description=(
            "When true, keep a server-side copy under results/reports/lp_letter/. "
            "When false, delete it after download (useful when downloading locally to avoid duplicates)."
        ),
    ),
    background: BackgroundTasks = None,
    portfolio_svc=Depends(get_portfolio_data_service),
):
    from app.services.reporting.lp_letter import lp_letter_generator
    from app.services.analytics.fund_air import FundAIRCalculator
    from types import SimpleNamespace
    import traceback as _tb

    fund_id = (fund_id or "PE-FUND-I").strip()

    try:
        portfolio = portfolio_svc.get_portfolio_view(fund_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Portfolio '{fund_id}' not found or inaccessible: {exc}")

    companies = portfolio.get("companies", [])
    if not companies:
        raise HTTPException(status_code=422, detail=f"Portfolio '{fund_id}' has no companies. Add companies first.")

    # Calculate CS5 fund metrics
    company_objs = [
        SimpleNamespace(
            company_id=c.get("company_id") or c.get("ticker") or "",
            org_air=float(c.get("org_air") or 0.0),
            sector=c.get("sector") or "technology",
            delta_since_entry=float(c.get("delta_since_entry") or 0.0),
        )
        for c in companies
    ]

    try:
        metrics = FundAIRCalculator().calculate_fund_metrics(fund_id, company_objs)
        metrics_dict = metrics.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fund metrics calculation failed: {exc}")

    company_scores = [
        {"ticker": c.get("ticker"), "org_air": float(c.get("org_air") or 0.0), "sector": c.get("sector") or ""}
        for c in companies
        if c.get("ticker")
    ]

    out_path = os.path.join(
        _reports_subdir("lp_letter"),
        f"lp_letter_{fund_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.docx",
    )

    try:
        path = lp_letter_generator.generate(
            fund_id=fund_id,
            fund_metrics=metrics_dict,
            company_scores=company_scores,
            output_path=out_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LP letter generation failed: {exc}\n{_tb.format_exc()}")

    if not persist:
        if background is None:
            background = BackgroundTasks()
        background.add_task(_safe_remove, path)
    return FileResponse(path, filename=os.path.basename(path), background=background)
