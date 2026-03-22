"""Due Diligence Router — CS5 agentic workflow endpoint.

POST /api/v1/dd/run/{ticker}
    Runs the full LangGraph multi-agent due diligence graph for a company.

GET  /api/v1/dd/status/{thread_id}
    Returns the last saved state for a prior run (uses LangGraph checkpointer).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dd", tags=["CS5 — Due Diligence"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DDRequest(BaseModel):
    assessment_type: str = "full"       # "screening" | "limited" | "full"
    requested_by: str = "analyst"
    target_org_air: float = 85.0


class DDSummary(BaseModel):
    ticker: str
    thread_id: str
    assessment_type: str
    org_air: Optional[float] = None
    vr_score: Optional[float] = None
    hr_score: Optional[float] = None
    dimension_scores: Dict[str, float] = {}
    requires_approval: bool = False
    approval_status: Optional[str] = None
    approved_by: Optional[str] = None
    approval_reason: Optional[str] = None
    ebitda_base: Optional[str] = None
    ebitda_risk_adjusted: Optional[str] = None
    narrative: Optional[str] = None
    messages_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_summary(ticker: str, thread_id: str, state: Dict[str, Any]) -> DDSummary:
    """Pull key fields out of the final DueDiligenceState."""
    sr  = state.get("scoring_result") or {}
    vcp = state.get("value_creation_plan") or {}
    ebitda = vcp.get("ebitda_projection") or {}
    scenarios = ebitda.get("scenarios") or {}

    started = state.get("started_at")
    completed = state.get("completed_at")

    return DDSummary(
        ticker=ticker,
        thread_id=thread_id,
        assessment_type=state.get("assessment_type", "full"),
        org_air=sr.get("org_air"),
        vr_score=sr.get("vr_score"),
        hr_score=sr.get("hr_score"),
        dimension_scores=sr.get("dimension_scores", {}),
        requires_approval=state.get("requires_approval", False),
        approval_status=state.get("approval_status"),
        approved_by=state.get("approved_by"),
        approval_reason=state.get("approval_reason"),
        ebitda_base=scenarios.get("base"),
        ebitda_risk_adjusted=ebitda.get("risk_adjusted"),
        narrative=vcp.get("narrative"),
        messages_count=len(state.get("messages", [])),
        started_at=str(started) if started else None,
        completed_at=str(completed) if completed else None,
        error=state.get("error"),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/dd/run/{ticker}
# ---------------------------------------------------------------------------

@router.post(
    "/run/{ticker}",
    response_model=DDSummary,
    summary="Run agentic due diligence for a company",
    description=(
        "Executes the full LangGraph multi-agent due diligence workflow: "
        "SEC Analysis → Scoring → Evidence Justification → Value Creation → HITL. "
        "assessment_type='screening' skips ValueCreationAgent (faster). "
        "Results are checkpointed so they can be retrieved via GET /api/v1/dd/status/{thread_id}."
    ),
)
async def run_due_diligence(
    ticker: str,
    body: DDRequest = None,
) -> DDSummary:
    if body is None:
        body = DDRequest()

    ticker = ticker.upper()
    thread_id = f"dd-{ticker.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    try:
        from app.agents.supervisor import dd_graph
        from app.agents.state import DueDiligenceState
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"LangGraph agents not available: {e}. Ensure langgraph is installed.",
        )

    initial_state: DueDiligenceState = {
        "company_id": ticker,
        "assessment_type": body.assessment_type,
        "requested_by": body.requested_by,
        "messages": [],
        "sec_analysis": None,
        "talent_analysis": None,
        "scoring_result": None,
        "evidence_justifications": None,
        "value_creation_plan": None,
        "next_agent": None,
        "requires_approval": False,
        "approval_reason": None,
        "approval_status": None,
        "approved_by": None,
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "total_tokens": 0,
        "error": None,
    }

    config = {"configurable": {"thread_id": thread_id}}

    logger.info("DD workflow started ticker=%s thread=%s type=%s", ticker, thread_id, body.assessment_type)

    try:
        final_state = await dd_graph.ainvoke(initial_state, config)
    except Exception as e:
        logger.error("DD workflow failed ticker=%s: %s", ticker, e)
        raise HTTPException(status_code=500, detail=f"Workflow error: {e}")

    logger.info("DD workflow complete ticker=%s thread=%s", ticker, thread_id)
    return _extract_summary(ticker, thread_id, final_state)


# ---------------------------------------------------------------------------
# GET /api/v1/dd/status/{thread_id}
# ---------------------------------------------------------------------------

@router.get(
    "/status/{thread_id}",
    response_model=DDSummary,
    summary="Get status of a prior due diligence run",
    description="Retrieves the checkpointed state of a prior DD run by thread_id.",
)
async def get_dd_status(thread_id: str) -> DDSummary:
    try:
        from app.agents.supervisor import dd_graph
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"LangGraph not available: {e}")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = await dd_graph.aget_state(config)
        if state is None or state.values is None:
            raise HTTPException(status_code=404, detail=f"No run found for thread_id: {thread_id}")
        values = state.values
        ticker = values.get("company_id", "UNKNOWN").upper()
        return _extract_summary(ticker, thread_id, values)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status fetch error: {e}")
