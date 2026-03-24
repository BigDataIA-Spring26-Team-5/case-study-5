"""CS5 Assessment History endpoints.

CS5 Task 9.4 requires capturing assessment snapshots for trend analysis.
This router exposes read-only access for dashboards.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_assessment_snapshot_repository


router = APIRouter(prefix="/api/v1/history", tags=["CS5 — History"])


@router.get(
    "/{ticker}",
    summary="List assessment history snapshots for a ticker",
)
async def list_history(
    ticker: str,
    days: int = Query(365, ge=1, le=3650, description="Lookback window in days"),
    portfolio_id: Optional[str] = Query(
        None, description="Optional portfolio id to filter snapshots"
    ),
    repo=Depends(get_assessment_snapshot_repository),
) -> Dict[str, Any]:
    ticker_u = (ticker or "").upper().strip()
    items = repo.list_snapshots(ticker=ticker_u, portfolio_id=portfolio_id, days=days)
    return {"ticker": ticker_u, "count": len(items), "items": items}

