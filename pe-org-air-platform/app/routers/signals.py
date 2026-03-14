# """
# Signals API Router
# app/routers/signals.py
#
# Endpoints:
#   POST  /api/v1/signals/collect                       - Trigger signal collection (background)
#   GET   /api/v1/signals/detailed                      - List signals (filterable)
# """

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import get_signal_repository
from app.services.job_signal_service import get_job_signal_service
from app.services.leadership_service import get_leadership_service
from app.services.patent_signal_service import get_patent_signal_service
from app.services.tech_signal_service import get_tech_signal_service

logger = logging.getLogger(__name__)

# In-memory task status store (in production, use Redis or database)
_task_store: Dict[str, Dict[str, Any]] = {}

# =============================================================================
# Enums & Models
# =============================================================================

VALID_CATEGORIES = [
    "technology_hiring",
    "innovation_activity",
    "digital_presence",
    "leadership_signals",
]


class CollectionRequest(BaseModel):
    company_id: str = Field(..., description="Company ID or ticker symbol")
    categories: List[str] = Field(
        default=VALID_CATEGORIES,
        description="Signal categories to collect",
    )
    years_back: int = Field(default=5, ge=1, le=10, description="Years back for patent search")
    force_refresh: bool = Field(default=False, description="Force refresh cached data")


class CollectionResponse(BaseModel):
    task_id: str
    status: str
    message: str


# =============================================================================
# Router
# =============================================================================

router = APIRouter(prefix="/api/v1", tags=["Signals"])


# =============================================================================
# Helper: look up company or 404
# =============================================================================

def _get_company_or_404(ticker: str) -> dict:
    company = CompanyRepository().get_by_ticker(ticker.upper())
    if not company:
        raise HTTPException(status_code=404, detail=f"Company not found: {ticker}")
    return company


# =============================================================================
# Collection (Background Task)
# =============================================================================

@router.post(
    "/signals/collect",
    response_model=CollectionResponse,
    summary="Trigger signal collection for a company",
    description=(
        "Trigger signal collection for a company. Runs asynchronously in the background.\n\n"
        "**Categories:** technology_hiring, innovation_activity, digital_presence, leadership_signals\n\n"
        "Returns a task_id to check status via GET /api/v1/signals/tasks/{task_id}"
    ),
)
async def collect_signals(request: CollectionRequest, background_tasks: BackgroundTasks):
    """Trigger signal collection for a company."""
    task_id = str(uuid4())
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "progress": {
            "total_categories": len(request.categories),
            "completed_categories": 0,
            "current_category": None,
        },
        "result": None,
        "error": None,
    }

    background_tasks.add_task(
        run_signal_collection,
        task_id=task_id,
        company_id=request.company_id,
        categories=request.categories,
        years_back=request.years_back,
        force_refresh=request.force_refresh,
    )

    logger.info(f"Signal collection queued: task_id={task_id}, company={request.company_id}")
    return CollectionResponse(
        task_id=task_id,
        status="queued",
        message=f"Signal collection started for company {request.company_id}",
    )


async def run_signal_collection(
    task_id: str,
    company_id: str,
    categories: List[str],
    years_back: int,
    force_refresh: bool,
):
    """Background task for signal collection."""
    logger.info(f"Starting signal collection: task_id={task_id}, company={company_id}")
    _task_store[task_id]["status"] = "running"

    company_repo = CompanyRepository()
    company = company_repo.get_by_ticker(company_id.upper())
    if not company:
        companies = company_repo.get_all()
        company = next((c for c in companies if str(c.get("id")) == company_id), None)

    if not company:
        _task_store[task_id]["status"] = "failed"
        _task_store[task_id]["error"] = f"Company not found: {company_id}"
        _task_store[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        return

    ticker = company.get("ticker")
    result = {
        "company_id": str(company.get("id")),
        "company_name": company.get("name"),
        "ticker": ticker,
        "signals": {},
        "errors": [],
    }

    category_handlers = {
        "technology_hiring": lambda: get_job_signal_service().analyze_company(ticker, force_refresh=force_refresh),
        "innovation_activity": lambda: get_patent_signal_service().analyze_company(ticker, years_back=years_back),
        "digital_presence": lambda: get_tech_signal_service().analyze_company(ticker, force_refresh=force_refresh),
        "leadership_signals": lambda: get_leadership_service().analyze_company(ticker),
    }

    for i, category in enumerate(categories):
        _task_store[task_id]["progress"]["current_category"] = category
        try:
            handler = category_handlers.get(category)
            if handler:
                signal_result = await handler()
                result["signals"][category] = {
                    "status": "success",
                    "score": signal_result.get("normalized_score"),
                    "details": signal_result,
                }
        except Exception as e:
            logger.error(f"Error collecting {category} signals: {e}")
            result["signals"][category] = {"status": "failed", "error": str(e)}
            result["errors"].append(f"{category}: {str(e)}")

        _task_store[task_id]["progress"]["completed_categories"] = i + 1

    _task_store[task_id]["status"] = "completed" if not result["errors"] else "completed_with_errors"
    _task_store[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    _task_store[task_id]["result"] = result
    _task_store[task_id]["progress"]["current_category"] = None
    logger.info(f"Signal collection completed: task_id={task_id}")


# =============================================================================
# List / Query Signals
# =============================================================================

@router.get(
    "/signals/detailed",
    summary="List signals with details (filterable)",
    description="List all signals with optional filters by category, ticker, min_score, and limit.",
)
async def list_signals(
    category: Optional[str] = Query(None, description="Filter by category"),
    ticker: Optional[str] = Query(None, description="Filter by company ticker"),
    min_score: Optional[float] = Query(None, ge=0, le=100, description="Minimum score"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
):
    """List signals with optional filters."""
    repo = get_signal_repository()
    company_repo = CompanyRepository()
    results = []

    if ticker:
        company = _get_company_or_404(ticker)
        company_id = str(company["id"])
        results = (
            repo.get_signals_by_category(company_id, category)
            if category
            else repo.get_signals_by_company(company_id)
        )
    else:
        for company in company_repo.get_all():
            company_id = str(company.get("id"))
            signals = (
                repo.get_signals_by_category(company_id, category)
                if category
                else repo.get_signals_by_company(company_id)
            )
            results.extend(signals)

    if min_score is not None:
        results = [s for s in results if (s.get("normalized_score") or 0) >= min_score]

    results = results[:limit]
    return {
        "total": len(results),
        "filters": {"category": category, "ticker": ticker, "min_score": min_score},
        "signals": results,
    }
