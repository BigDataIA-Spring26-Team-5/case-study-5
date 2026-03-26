"""CS1 Portfolio endpoints (CS5 integration support).

CS5 Task 9.1 expects portfolio membership to be sourced from CS1 portfolio
management (not hardcoded tickers). This router exposes portfolio membership
stored in `cs4_portfolios` / `cs4_portfolio_companies`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_company_repository
from app.core.errors import NotFoundError

router = APIRouter(prefix="/api/v1/portfolios", tags=["Portfolios"])


class PortfolioUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Portfolio name (case-insensitive exact match).")
    tickers: List[str] = Field(default_factory=list, description="Tickers to set as portfolio members.")
    fund_vintage: Optional[int] = Field(default=None, description="Optional fund vintage year.")


class PortfolioUpsertResponse(BaseModel):
    portfolio_id: str
    name: str
    company_count: int
    tickers: List[str]


@router.get(
    "",
    summary="List portfolios",
)
async def list_portfolios(
    limit: int = Query(200, ge=1, le=1000),
    company_repo=Depends(get_company_repository),
) -> Dict[str, Any]:
    items = company_repo.list_portfolios(limit=int(limit))
    # Normalize to include a consistent `portfolio_id` key
    out = []
    for p in items or []:
        out.append(
            {
                "portfolio_id": str(p.get("id") or ""),
                "name": p.get("name") or "",
                "fund_vintage": p.get("fund_vintage"),
                "created_at": p.get("created_at"),
            }
        )
    return {"items": out, "count": len(out)}


@router.get(
    "/{portfolio_id}",
    summary="Get portfolio metadata",
)
async def get_portfolio(
    portfolio_id: str,
    company_repo=Depends(get_company_repository),
) -> Dict[str, Any]:
    portfolio = company_repo.get_portfolio(portfolio_id)
    if not portfolio:
        raise NotFoundError("portfolio", portfolio_id)
    return portfolio


@router.get(
    "/{portfolio_id}/companies",
    summary="List companies in a portfolio",
)
async def list_portfolio_companies(
    portfolio_id: str,
    company_repo=Depends(get_company_repository),
) -> Dict[str, Any]:
    companies: List[Dict[str, Any]] = company_repo.get_by_portfolio(portfolio_id)
    return {"portfolio_id": portfolio_id, "items": companies, "count": len(companies)}


@router.get(
    "/resolve",
    summary="Resolve portfolio ID by name",
)
async def resolve_portfolio(
    name: str = Query(..., description="Portfolio name (case-insensitive exact match)"),
    company_repo=Depends(get_company_repository),
) -> Dict[str, Optional[str]]:
    portfolio_id = company_repo.find_portfolio_id_by_name(name)
    return {"name": name, "portfolio_id": portfolio_id}


@router.post(
    "/by-name",
    summary="Create or update a portfolio by name (set membership)",
    response_model=PortfolioUpsertResponse,
)
async def upsert_portfolio_by_name(
    req: PortfolioUpsertRequest,
    company_repo=Depends(get_company_repository),
) -> PortfolioUpsertResponse:
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Portfolio name is required")

    portfolio_id = company_repo.find_portfolio_id_by_name(name)
    if portfolio_id is None:
        portfolio_id = company_repo.create_portfolio(name=name, fund_vintage=req.fund_vintage)

    tickers = [str(t).upper().strip() for t in (req.tickers or []) if str(t).strip()]
    company_ids: List[str] = []
    missing: List[str] = []
    for t in tickers:
        row = company_repo.get_by_ticker(t)
        if not row:
            missing.append(t)
            continue
        cid = str(row.get("id") or "")
        if cid:
            company_ids.append(cid)

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tickers (not found in companies table): {', '.join(missing)}",
        )

    company_repo.set_portfolio_companies(portfolio_id, company_ids)
    return PortfolioUpsertResponse(
        portfolio_id=portfolio_id,
        name=name,
        company_count=len(company_ids),
        tickers=tickers,
    )

