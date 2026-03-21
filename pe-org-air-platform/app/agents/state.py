"""LangGraph state definitions for PE Org-AI-R due diligence agents.

Why this file exists
--------------------
LangGraph graphs are stateful: every node reads from and writes to a shared
``DueDiligenceState`` dict that is passed through the graph.  This module is
the single source of truth for that shared state so that:

* Every specialist agent (SEC, talent, scoring, justification, value-creation)
  knows exactly which keys it can read and which it must write.
* The supervisor node can inspect ``next_agent`` to route work and check
  ``requires_approval`` to pause the graph for human-in-the-loop (HITL) gates.
* The HITL gate reads ``approval_status`` to decide whether to resume or abort.
* Observability tooling (LangSmith, logging) gets a consistent schema to trace.

How it fits into the codebase
------------------------------
CS1 → company registration  →  ``company_id`` / ``requested_by``
CS2 → evidence collection   →  ``sec_analysis``, ``talent_analysis``
CS3 → composite scoring     →  ``scoring_result`` (org_air, vr, hr, dimensions)
CS4 → RAG justification     →  ``evidence_justifications``
CS5 → value-creation plan   →  ``value_creation_plan`` (gap analysis + EBITDA)

The ``messages`` field uses an ``operator.add`` reducer so that every agent
can *append* its own messages without overwriting the history of earlier agents.
"""
from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict


class AgentMessage(TypedDict):
    """A single message in the agent conversation thread."""
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    agent_name: Optional[str]   # which specialist produced this message
    timestamp: datetime


class DueDiligenceState(TypedDict):
    """Shared LangGraph state for the full due diligence workflow.

    Lifecycle
    ---------
    1. Supervisor initialises ``company_id``, ``assessment_type``, ``requested_by``.
    2. Each specialist agent reads its required inputs and writes its output key.
    3. When ``requires_approval`` is True the graph pauses; a human sets
       ``approval_status`` to "approved" or "rejected" and optionally ``approved_by``.
    4. ``completed_at`` is set by the supervisor when all nodes have run.
    """

    # ── Input ────────────────────────────────────────────────────────────────
    company_id: str
    """Ticker symbol for the portfolio company being assessed (e.g. 'NVDA')."""

    assessment_type: Literal["screening", "limited", "full"]
    """
    screening — quick Org-AI-R score only (CS3).
    limited   — score + top-3 dimension justifications (CS3 + CS4).
    full      — all agents run including gap analysis and EBITDA projection (CS3-CS5).
    """

    requested_by: str
    """Identity of the analyst or system that triggered the workflow."""

    # ── Conversation history (append-only) ───────────────────────────────────
    messages: Annotated[List[AgentMessage], operator.add]
    """
    Append-only log of all agent messages.
    The ``operator.add`` reducer means each node appends; it never replaces.
    """

    # ── Specialist agent outputs ──────────────────────────────────────────────
    sec_analysis: Optional[Dict[str, Any]]
    """
    Written by the SEC/evidence agent (CS2).
    Expected keys: source_type, content, confidence, signal_category per item.
    """

    talent_analysis: Optional[Dict[str, Any]]
    """
    Written by the talent specialist agent (CS2 talent dimension).
    Expected keys: evidence list filtered to technology_hiring signal category.
    """

    scoring_result: Optional[Dict[str, Any]]
    """
    Written by the scoring agent (CS3).
    Expected keys: company_id, org_air, vr_score, hr_score, synergy_score,
    confidence_interval, dimension_scores {dim: score}.
    Shape matches the calculate_org_air_score MCP tool response.
    """

    evidence_justifications: Optional[Dict[str, Any]]
    """
    Written by the justification agent (CS4 RAG).
    Keyed by dimension name; each value is a generate_justification response:
    {dimension, score, level, level_name, evidence_strength, rubric_criteria,
     supporting_evidence, gaps_identified}.
    """

    value_creation_plan: Optional[Dict[str, Any]]
    """
    Written by the value-creation agent (CS5).
    Contains gap analysis (run_gap_analysis) and EBITDA projection
    (project_ebitda_impact): {delta_air, scenarios, risk_adjusted,
    requires_approval, gaps: [...]}.
    """

    # ── Workflow control ─────────────────────────────────────────────────────
    next_agent: Optional[str]
    """
    Set by the supervisor to route to the next specialist node.
    Values: 'sec_agent' | 'talent_agent' | 'scoring_agent' |
            'justification_agent' | 'value_creation_agent' | 'END'.
    """

    requires_approval: bool
    """
    True when the workflow must pause for human review before continuing.
    Triggered when Org-AI-R score change > HITL_SCORE_CHANGE_THRESHOLD or
    projected EBITDA impact > HITL_EBITDA_PROJECTION_THRESHOLD (from settings).
    """

    approval_reason: Optional[str]
    """Human-readable explanation of why approval is required."""

    approval_status: Optional[Literal["pending", "approved", "rejected"]]
    """
    Set by the HITL gate node:
      pending  — waiting for human decision.
      approved — human confirmed; workflow resumes.
      rejected — human blocked; workflow aborts with error.
    """

    approved_by: Optional[str]
    """Identity of the human approver (email, username, etc.)."""

    # ── Metadata ─────────────────────────────────────────────────────────────
    started_at: datetime
    """UTC timestamp when the workflow was initiated."""

    completed_at: Optional[datetime]
    """UTC timestamp set by the supervisor when all nodes have finished."""

    total_tokens: int
    """Cumulative LLM token count across all agent calls (for cost tracking)."""

    error: Optional[str]
    """Set if any agent raises an unhandled exception; supervisor routes to END."""
