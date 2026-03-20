# """
# Signals API Router
# app/routers/signals.py
#
# Endpoints:
#   POST  /api/v1/signals/collect                       - Trigger signal collection (background)
#   GET   /api/v1/signals/tasks/{task_id}               - Get task status
#   GET   /api/v1/signals/detailed                      - List signals (filterable)
# """

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field

from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import SignalRepository
from app.services.task_store import TaskStore
from app.core.dependencies import (
    get_company_repository,
    get_signal_repository,
    get_job_signal_service,
    get_patent_signal_service,
    get_tech_signal_service,
    get_leadership_service,
    get_board_composition_service,
    get_culture_signal_service_dep,
    get_task_store,
)
from app.core.errors import NotFoundError
from app.routers.common import get_company_or_404

logger = logging.getLogger(__name__)

# =============================================================================
# Enums & Models
# =============================================================================

VALID_CATEGORIES = [
    "technology_hiring",
    "innovation_activity",
    "digital_presence",
    "leadership_signals",
    "board_composition",
    "culture",
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
# Collection (Background Task)
# =============================================================================

@router.post(
    "/signals/collect",
    response_model=CollectionResponse,
    summary="Trigger signal collection for a company",
    description=(
        "Trigger signal collection for a company. Runs asynchronously in the background.\n\n"
        "**Categories:** technology_hiring, innovation_activity, digital_presence, leadership_signals, board_composition, culture\n\n"
        "Returns a task_id to check status via GET /api/v1/signals/tasks/{task_id}"
    ),
)
async def collect_signals(
    request: CollectionRequest,
    background_tasks: BackgroundTasks,
    task_store: TaskStore = Depends(get_task_store),
    company_repo: CompanyRepository = Depends(get_company_repository),
    job_signal_svc=Depends(get_job_signal_service),
    patent_signal_svc=Depends(get_patent_signal_service),
    tech_signal_svc=Depends(get_tech_signal_service),
    leadership_svc=Depends(get_leadership_service),
    board_composition_svc=Depends(get_board_composition_service),
    culture_svc=Depends(get_culture_signal_service_dep),
):
    """Trigger signal collection for a company."""
    task_id = str(uuid4())
    task_store.create_task(task_id, metadata={
        "progress": {
            "total_categories": len(request.categories),
            "completed_categories": 0,
            "current_category": None,
        },
    })

    background_tasks.add_task(
        run_signal_collection,
        task_id=task_id,
        company_id=request.company_id,
        categories=request.categories,
        years_back=request.years_back,
        force_refresh=request.force_refresh,
        task_store=task_store,
        company_repo=company_repo,
        job_signal_svc=job_signal_svc,
        patent_signal_svc=patent_signal_svc,
        tech_signal_svc=tech_signal_svc,
        leadership_svc=leadership_svc,
        board_composition_svc=board_composition_svc,
        culture_svc=culture_svc,
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
    task_store: TaskStore,
    company_repo: CompanyRepository,
    job_signal_svc,
    patent_signal_svc,
    tech_signal_svc,
    leadership_svc,
    board_composition_svc=None,
    culture_svc=None,
):
    """Background task for signal collection."""
    logger.info(f"Starting signal collection: task_id={task_id}, company={company_id}")
    task_store.update_status(task_id, status="running")

    company = company_repo.get_by_ticker(company_id.upper())
    if not company:
        companies = company_repo.get_all()
        company = next((c for c in companies if str(c.get("id")) == company_id), None)

    if not company:
        task_store.update_status(
            task_id,
            status="failed",
            error=f"Company not found: {company_id}",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    ticker = company.get("ticker")
    result = {
        "company_id": str(company.get("id")),
        "company_name": company.get("name"),
        "ticker": ticker,
        "signals": {},
        "errors": [],
    }

    category_services = {
        "technology_hiring": (job_signal_svc, {"force_refresh": force_refresh}),
        "innovation_activity": (patent_signal_svc, {"years_back": years_back}),
        "digital_presence": (tech_signal_svc, {"force_refresh": force_refresh}),
        "leadership_signals": (leadership_svc, {}),
        "board_composition": (board_composition_svc, {}),
        "culture": (culture_svc, {"force_refresh": force_refresh}),
    }

    for i, category in enumerate(categories):
        task_store.update_status(task_id, progress={
            "total_categories": len(categories),
            "completed_categories": i,
            "current_category": category,
        })
        try:
            entry = category_services.get(category)
            if entry:
                svc, kwargs = entry
                signal_result = await svc.analyze_company(ticker, **kwargs)
                result["signals"][category] = {
                    "status": "success",
                    "score": signal_result.get("normalized_score"),
                    "details": signal_result,
                }
        except Exception as e:
            logger.error(f"Error collecting {category} signals: {e}")
            result["signals"][category] = {"status": "failed", "error": str(e)}
            result["errors"].append(f"{category}: {str(e)}")

    task_store.update_status(
        task_id,
        status="completed" if not result["errors"] else "completed_with_errors",
        completed_at=datetime.now(timezone.utc).isoformat(),
        result=result,
        progress={
            "total_categories": len(categories),
            "completed_categories": len(categories),
            "current_category": None,
        },
    )
    logger.info(f"Signal collection completed: task_id={task_id}")


# =============================================================================
# Task Status
# =============================================================================

@router.get(
    "/signals/tasks/{task_id}",
    summary="Get signal collection task status",
)
async def get_task_status(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
):
    task = task_store.get_task(task_id)
    if task is None:
        raise NotFoundError("task", task_id)
    return task


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
    signal_repo: SignalRepository = Depends(get_signal_repository),
    company_repo: CompanyRepository = Depends(get_company_repository),
):
    """List signals with optional filters."""
    results = []

    if ticker:
        company = get_company_or_404(ticker, company_repo)
        company_id = str(company["id"])
        results = (
            signal_repo.get_signals_by_category(company_id, category)
            if category
            else signal_repo.get_signals_by_company(company_id)
        )
    else:
        for company in company_repo.get_all():
            company_id = str(company.get("id"))
            signals = (
                signal_repo.get_signals_by_category(company_id, category)
                if category
                else signal_repo.get_signals_by_company(company_id)
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
