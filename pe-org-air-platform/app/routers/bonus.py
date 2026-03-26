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

from fastapi import APIRouter, Depends, Query, BackgroundTasks
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

    gap_analysis = portfolio_svc.run_gap_analysis(ticker_u, float(target_org_air))
    ebitda_projection = portfolio_svc.project_ebitda_impact(
        company_id=ticker_u,
        entry_score=float(scoring_result["org_air"] or 0.0),
        target_score=float(target_org_air),
        h_r_score=float(scoring_result["hr_score"] or 0.0),
    )

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

    fund_id = (fund_id or "PE-FUND-I").strip()
    portfolio = portfolio_svc.get_portfolio_view(fund_id)
    companies = portfolio.get("companies", [])

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
    metrics = FundAIRCalculator().calculate_fund_metrics(fund_id, company_objs)
    metrics_dict = metrics.to_dict()

    company_scores = [
        {"ticker": c.get("ticker"), "org_air": float(c.get("org_air") or 0.0), "sector": c.get("sector") or ""}
        for c in companies
        if c.get("ticker")
    ]

    out_path = os.path.join(
        _reports_subdir("lp_letter"),
        f"lp_letter_{fund_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.pdf",
    )
    path = lp_letter_generator.generate(
        fund_id=fund_id,
        fund_metrics=metrics_dict,
        company_scores=company_scores,
        output_path=out_path,
    )

    if not persist:
        if background is None:
            background = BackgroundTasks()
        background.add_task(_safe_remove, path)
    return FileResponse(path, filename=os.path.basename(path), background=background)
