"""Supervisor agent with HITL approval gates for the PE Org-AI-R due diligence workflow.

Graph structure
---------------
supervisor (conditional router)
  ├─► sec_analyst    → supervisor
  ├─► scorer         → supervisor
  ├─► evidence_agent → supervisor
  ├─► value_creator  → supervisor
  ├─► hitl_approval  → supervisor
  └─► complete       → END

The supervisor inspects the current state and decides which node to run next.
When a specialist sets ``requires_approval=True``, the supervisor sets
``approval_status="pending"`` and routes to ``hitl_approval`` before
continuing.  After approval the loop continues from where it paused.

HITL thresholds come from ``app/config`` (HITL_SCORE_CHANGE_THRESHOLD,
HITL_EBITDA_PROJECTION_THRESHOLD) so they are never hard-coded here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agents.state import AgentMessage, DueDiligenceState
from app.agents.specialists import (
    EvidenceAgent,
    ScoringAgent,
    SECAnalysisAgent,
    ValueCreationAgent,
)
from app.config import get_settings

logger = structlog.get_logger()

_settings = get_settings()

# Singleton specialist agents — shared across graph invocations.
# ScoringAgent's HITL threshold comes from settings so it matches the
# value documented in state.py.
_sec_agent = SECAnalysisAgent()
_scoring_agent = ScoringAgent(
    hitl_score_change_threshold=_settings.HITL_SCORE_CHANGE_THRESHOLD
)
_evidence_agent = EvidenceAgent()
_value_agent = ValueCreationAgent(
    hitl_ebitda_threshold=_settings.HITL_EBITDA_PROJECTION_THRESHOLD
)


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------

async def supervisor_node(state: DueDiligenceState) -> Dict[str, Any]:
    """Decides which specialist to run next, or routes to HITL / complete."""

    # If approval is required and not yet resolved, pause for HITL.
    if state.get("requires_approval"):
        status = state.get("approval_status")
        if status == "rejected":
            logger.error("hitl_rejected", company_id=state["company_id"])
            return {
                "next_agent": "complete",
                "error": "Workflow aborted: HITL approval rejected.",
            }
        if status not in ("approved",):
            # Mark as pending and route to the HITL gate.
            return {"next_agent": "hitl_approval", "approval_status": "pending"}

    # Sequential specialist pipeline
    if not state.get("sec_analysis"):
        return {"next_agent": "sec_analyst"}
    elif not state.get("scoring_result"):
        return {"next_agent": "scorer"}
    elif not state.get("talent_analysis"):
        return {"next_agent": "evidence_agent"}
    elif (
        not state.get("value_creation_plan")
        and state.get("assessment_type") != "screening"
    ):
        return {"next_agent": "value_creator"}
    else:
        return {"next_agent": "complete"}


# ---------------------------------------------------------------------------
# Specialist wrapper nodes — sync __call__ → async via asyncio.to_thread
# ---------------------------------------------------------------------------

async def sec_analyst_node(state: DueDiligenceState) -> Dict[str, Any]:
    return await asyncio.to_thread(_sec_agent, state)


async def scorer_node(state: DueDiligenceState) -> Dict[str, Any]:
    return await asyncio.to_thread(_scoring_agent, state)


async def evidence_node(state: DueDiligenceState) -> Dict[str, Any]:
    return await asyncio.to_thread(_evidence_agent, state)


async def value_creator_node(state: DueDiligenceState) -> Dict[str, Any]:
    return await asyncio.to_thread(_value_agent, state)


# ---------------------------------------------------------------------------
# HITL approval node
# ---------------------------------------------------------------------------

async def hitl_approval_node(state: DueDiligenceState) -> Dict[str, Any]:
    """Human-in-the-loop gate.

    Production: send Slack/email notification and block until human responds.
    Exercise: auto-approves after logging the reason.
    """
    logger.warning(
        "hitl_approval_required",
        company_id=state["company_id"],
        reason=state.get("approval_reason"),
    )

    # In production replace this block with a real approval mechanism.
    return {
        "approval_status": "approved",
        "approved_by": "exercise_auto_approve",
        "requires_approval": False,   # reset so the supervisor loop continues
        "messages": [
            AgentMessage(
                role="system",
                content=f"HITL approval granted: {state.get('approval_reason')}",
                agent_name="hitl",
                timestamp=datetime.now(tz=timezone.utc),
            )
        ],
    }


# ---------------------------------------------------------------------------
# Complete node
# ---------------------------------------------------------------------------

async def complete_node(state: DueDiligenceState) -> Dict[str, Any]:
    return {
        "completed_at": datetime.now(tz=timezone.utc),
        "messages": [
            AgentMessage(
                role="assistant",
                content=f"Due diligence complete for {state['company_id']}",
                agent_name="supervisor",
                timestamp=datetime.now(tz=timezone.utc),
            )
        ],
    }


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def create_due_diligence_graph():
    """Build and compile the LangGraph due diligence workflow."""
    workflow = StateGraph(DueDiligenceState)

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("sec_analyst", sec_analyst_node)
    workflow.add_node("scorer", scorer_node)
    workflow.add_node("evidence_agent", evidence_node)
    workflow.add_node("value_creator", value_creator_node)
    workflow.add_node("hitl_approval", hitl_approval_node)
    workflow.add_node("complete", complete_node)

    workflow.add_conditional_edges(
        "supervisor",
        lambda s: s["next_agent"],
        {
            "sec_analyst":    "sec_analyst",
            "scorer":         "scorer",
            "evidence_agent": "evidence_agent",
            "value_creator":  "value_creator",
            "hitl_approval":  "hitl_approval",
            "complete":       "complete",
        },
    )

    for agent_node in ["sec_analyst", "scorer", "evidence_agent", "value_creator"]:
        workflow.add_edge(agent_node, "supervisor")
    workflow.add_edge("hitl_approval", "supervisor")
    workflow.add_edge("complete", END)

    workflow.set_entry_point("supervisor")
    return workflow.compile(checkpointer=MemorySaver())


dd_graph = create_due_diligence_graph()
