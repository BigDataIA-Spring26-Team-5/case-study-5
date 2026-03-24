"""CS1 Portfolio endpoints (CS5 integration support).

CS5 Task 9.1 expects portfolio membership to be sourced from CS1 portfolio
management (not hardcoded tickers). This router exposes portfolio membership
stored in `cs4_portfolios` / `cs4_portfolio_companies`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_company_repository
from app.core.errors import NotFoundError

router = APIRouter(prefix="/api/v1/portfolios", tags=["Portfolios"])


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

