"""Specialist agent implementations for the PE Org-AI-R due diligence workflow.

Why this file exists
--------------------
Each specialist agent is a callable node in the LangGraph graph.  They share
``DueDiligenceState`` (defined in ``app.agents.state``) and communicate with
the rest of the platform exclusively through FastAPI endpoints — the same
endpoints that the MCP server exposes to external LLM clients.

How agents call tools
----------------------
``MCPToolCaller`` is a thin HTTP wrapper around the FastAPI server running at
``http://localhost:8000``.  It mirrors the same endpoint mapping used by
``app/mcp/server.py`` so there is a single source of truth for business logic.

How agents call the LLM
------------------------
Every agent reuses ``ModelRouter`` from ``app/services/llm/router.py``.
``ModelRouter.complete_sync(task, messages)`` selects the cheapest model that
can handle the task (Groq for extraction tasks, Claude Haiku for quality
summaries) and falls back automatically.

Agent → State mapping
----------------------
SECAnalysisAgent  → writes ``state["sec_analysis"]``     (CS2)
ScoringAgent      → writes ``state["scoring_result"]``   (CS3)
EvidenceAgent     → writes ``state["talent_analysis"]``  (CS2 talent dimension)
ValueCreationAgent→ writes ``state["value_creation_plan"]`` (CS5)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.agents.state import AgentMessage, DueDiligenceState
from app.agents.memory import agent_memory
from app.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)

_FASTAPI_BASE = "http://localhost:8000"


# ---------------------------------------------------------------------------
# MCPToolCaller — thin HTTP wrapper for FastAPI endpoints
# ---------------------------------------------------------------------------

class MCPToolCaller:
    """Mirrors the tool implementations in app/mcp/server.py.

    Tools 1, 5: use CS3Client (calls GET /api/v1/scoring/{ticker}/dimensions).
    Tool  2   : calls FastAPI GET /api/v1/rag/evidence/{ticker} via httpx.
    Tool  3   : calls FastAPI GET /api/v1/rag/justify/{ticker}/{dim} via httpx.
    Tool  4   : pure local math via EBITDACalculator (no HTTP).
    Tool  5   : CS3Client + local GapAnalyzer (no dedicated REST endpoint).
    Tool  6   : CS3Client iteration over portfolio tickers.

    All HTTP calls use httpx.Client(timeout=None) to prevent ReadTimeout on
    slow Snowflake queries.
    """

    def __init__(self, base_url: str = _FASTAPI_BASE):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=None)
        self._composite_repo = None  # lazy
        self._ebitda = None
        self._gap = None

    # ── lazy singletons (same pattern as server.py) ──────────────────────────

    def _get_composite_repo(self):
        if self._composite_repo is None:
            from app.repositories.composite_scoring_repository import CompositeScoringRepository
            self._composite_repo = CompositeScoringRepository()
        return self._composite_repo

    def _get_ebitda(self):
        if self._ebitda is None:
            from app.services.value_creation.ebitda import EBITDACalculator
            self._ebitda = EBITDACalculator()
        return self._ebitda

    def _get_gap(self):
        if self._gap is None:
            from app.services.value_creation.gap_analysis import GapAnalyzer
            self._gap = GapAnalyzer()
        return self._gap

    # ── Tool 1: calculate_org_air_score ──────────────────────────────────────
    def calculate_org_air_score(self, company_id: str) -> Dict[str, Any]:
        """Reads composite scores directly from the SCORING table in Snowflake.

        Snowflake DictCursor returns uppercase keys; we normalise to lowercase
        before accessing values. Dimension scores come from the dimensions
        endpoint (they are stored in a separate table).
        """
        ticker = company_id.upper()
        repo = self._get_composite_repo()

        # Fetch all composite columns in one query; normalise uppercase keys
        row = repo._query(
            ticker,
            ["ticker", "org_air", "vr_score", "hr_score", "synergy_score",
             "talent_concentration", "position_factor", "ci_lower", "ci_upper"],
        )
        if row is None:
            return {
                "company_id": ticker,
                "error": "No composite scores found in SCORING table for this ticker.",
            }
        row = {k.lower(): v for k, v in row.items()}  # normalise Snowflake uppercase keys

        # Dimension scores from the scoring/{ticker}/dimensions endpoint
        dim_scores: Dict[str, float] = {}
        try:
            r = self._http.get(f"{self.base_url}/api/v1/scoring/{ticker}/dimensions")
            if r.status_code == 200:
                for d in r.json().get("scores", []):
                    dim = d.get("dimension", "")
                    if dim:
                        dim_scores[dim] = float(d.get("score", 0.0))
        except Exception:
            pass  # dimension scores are supplementary; don't fail the whole call

        ci_lower = float(row.get("ci_lower") or 0.0)
        ci_upper = float(row.get("ci_upper") or 0.0)
        return {
            "company_id": ticker,
            "org_air": float(row.get("org_air") or 0.0),
            "vr_score": float(row.get("vr_score") or 0.0),
            "hr_score": float(row.get("hr_score") or 0.0),
            "synergy_score": float(row.get("synergy_score") or 0.0),
            "confidence_interval": [ci_lower, ci_upper],
            "dimension_scores": dim_scores,
        }

    # ── Tool 2: get_company_evidence ─────────────────────────────────────────
    def get_company_evidence(
        self,
        company_id: str,
        dimension: Optional[str] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """FastAPI GET /api/v1/rag/evidence/{ticker}"""
        params: Dict[str, Any] = {"limit": limit}
        if dimension:
            params["dimension"] = dimension
        r = self._http.get(
            f"{self.base_url}/api/v1/rag/evidence/{company_id.upper()}",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    # ── Tool 3: generate_justification ───────────────────────────────────────
    def generate_justification(self, company_id: str, dimension: str) -> Dict[str, Any]:
        """FastAPI GET /api/v1/rag/justify/{ticker}/{dimension}"""
        r = self._http.get(
            f"{self.base_url}/api/v1/rag/justify/{company_id.upper()}/{dimension}"
        )
        r.raise_for_status()
        return r.json()

    # ── Tool 4: project_ebitda_impact ────────────────────────────────────────
    def project_ebitda_impact(
        self,
        company_id: str,
        entry_score: float,
        target_score: float,
        h_r_score: float,
    ) -> Dict[str, Any]:
        """Pure local math via EBITDACalculator — no HTTP."""
        from app.services.composite_scoring_service import COMPANY_SECTORS
        ticker = company_id.upper()
        sector = COMPANY_SECTORS.get(ticker, "technology")
        projection = self._get_ebitda().project(
            ticker, entry_score, target_score, h_r_score, sector
        )
        net = projection.net_impact_pct
        return {
            "delta_air": float(projection.score_improvement),
            "scenarios": {
                "conservative": f"{net * 0.7:.2f}%",
                "base": f"{net:.2f}%",
                "optimistic": f"{net * 1.3:.2f}%",
            },
            "risk_adjusted": f"{projection.adjusted_net_impact_pct:.2f}%",
            "requires_approval": (
                projection.score_improvement > 20
                or projection.adjusted_net_impact_pct > 10.0
            ),
        }

    # ── Tool 5: run_gap_analysis ─────────────────────────────────────────────
    def run_gap_analysis(
        self, company_id: str, target_org_air: float = 85.0
    ) -> Dict[str, Any]:
        """SCORING table (org_air) + dimensions endpoint + local GapAnalyzer."""
        ticker = company_id.upper()

        # Current org_air from SCORING table (same normalisation as tool 1)
        row = self._get_composite_repo()._query(ticker, ["ticker", "org_air"])
        current_org_air = 0.0
        if row:
            row = {k.lower(): v for k, v in row.items()}
            current_org_air = float(row.get("org_air") or 0.0)

        # Dimension scores from the dimensions endpoint, normalised to short names
        from app.services.integration.cs3_client import _DIM_ALIAS_MAP
        dim_scores: Dict[str, float] = {}
        try:
            r = self._http.get(f"{self.base_url}/api/v1/scoring/{ticker}/dimensions")
            if r.status_code == 200:
                for d in r.json().get("scores", []):
                    dim = d.get("dimension", "")
                    if dim:
                        dim_scores[_DIM_ALIAS_MAP.get(dim, dim)] = float(d.get("score", 0.0))
        except Exception:
            pass

        result = self._get_gap().analyze(ticker, dim_scores, current_org_air, target_org_air)
        return result.to_dict()

    # ── Tool 6: get_portfolio_summary ────────────────────────────────────────
    def get_portfolio_summary(self, fund_id: str = "PE-FUND-I") -> Dict[str, Any]:
        """Reads portfolio membership from CS1, then org_air from the SCORING table."""
        from app.services.composite_scoring_service import COMPANY_SECTORS
        import re

        def _looks_like_uuid(v: str) -> bool:
            return bool(
                re.fullmatch(
                    r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}",
                    (v or "").strip(),
                )
            )

        repo = self._get_composite_repo()

        fund_id = (fund_id or "").strip() or "PE-FUND-I"
        if _looks_like_uuid(fund_id):
            portfolio_id = fund_id
        else:
            resp = self._http.get(
                f"{self.base_url}/api/v1/portfolios/resolve",
                params={"name": fund_id},
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Portfolio resolve failed: {resp.status_code} {resp.text[:200]}"
                )
            portfolio_id = (resp.json() or {}).get("portfolio_id")

        if not portfolio_id:
            raise ValueError(f"Unknown portfolio: {fund_id}")

        resp = self._http.get(f"{self.base_url}/api/v1/portfolios/{portfolio_id}/companies")
        if resp.status_code != 200:
            raise RuntimeError(
                f"Portfolio companies fetch failed: {resp.status_code} {resp.text[:200]}"
            )
        items = (resp.json() or {}).get("items", [])
        tickers = [
            str(it.get("ticker") or "").upper()
            for it in items
            if it.get("ticker")
        ]
        if not tickers:
            raise ValueError(f"Portfolio '{fund_id}' has no companies with tickers")

        companies: List[Dict[str, Any]] = []
        for ticker in tickers:
            row = repo._query(ticker, ["ticker", "org_air"])
            if row:
                row = {k.lower(): v for k, v in row.items()}
                org_air = float(row.get("org_air") or 0.0)
            else:
                org_air = 0.0
            companies.append(
                {
                    "ticker": ticker,
                    "org_air": org_air,
                    "sector": COMPANY_SECTORS.get(ticker, ""),
                }
            )

        scores = [c["org_air"] for c in companies if c["org_air"] > 0]
        return {
            "fund_id": fund_id,
            "fund_air": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "company_count": len(companies),
            "companies": companies,
        }

    def call_tool(self, tool_name: str, arguments: dict) -> Dict[str, Any]:
        """Unified tool dispatch — mirrors app/mcp/server.py routing."""
        dispatch = {
            "calculate_org_air_score": lambda a: self.calculate_org_air_score(a["company_id"]),
            "get_company_evidence": lambda a: self.get_company_evidence(
                a["company_id"], a.get("dimension"), a.get("limit", 5),
            ),
            "generate_justification": lambda a: self.generate_justification(
                a["company_id"], a["dimension"],
            ),
            "project_ebitda_impact": lambda a: self.project_ebitda_impact(
                a["company_id"], a["entry_score"], a["target_score"], a["h_r_score"],
            ),
            "run_gap_analysis": lambda a: self.run_gap_analysis(
                a["company_id"], a.get("target_org_air", 85.0),
            ),
            "get_portfolio_summary": lambda a: self.get_portfolio_summary(
                a.get("fund_id", "PE-FUND-I"),
            ),
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return handler(arguments)

    def close(self) -> None:
        self._http.close()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _append_message(
    state: DueDiligenceState,
    agent_name: str,
    content: str,
) -> List[AgentMessage]:
    """Return a single-item list; LangGraph's operator.add reducer appends it."""
    return [
        AgentMessage(
            role="assistant",
            content=content,
            agent_name=agent_name,
            timestamp=_now(),
        )
    ]


# ---------------------------------------------------------------------------
# SECAnalysisAgent — CS2 SEC / evidence collection
# ---------------------------------------------------------------------------

class SECAnalysisAgent:
    """Fetches broad company evidence and produces a structured SEC analysis.

    Reads:  ``company_id``
    Writes: ``sec_analysis``, ``messages``
    """

    NAME = "sec_agent"

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        tool_caller: Optional[MCPToolCaller] = None,
    ):
        self.router = router or ModelRouter()
        self.tools = tool_caller or MCPToolCaller()

    def __call__(self, state: DueDiligenceState) -> Dict[str, Any]:
        company_id = state["company_id"]
        logger.info("SECAnalysisAgent started for %s", company_id)

        # 1. Fetch evidence from FastAPI
        try:
            evidence_data = self.tools.get_company_evidence(company_id, limit=10)
        except Exception as exc:
            error_msg = f"Evidence fetch failed: {exc}"
            logger.error(error_msg)
            return {
                "sec_analysis": {"error": error_msg, "company_id": company_id},
                "messages": _append_message(self.NAME, self.NAME, error_msg),
                "error": error_msg,
            }

        evidence_items: List[Dict[str, Any]] = evidence_data.get("evidence", [])

        # 2. Ask the LLM to summarise the evidence
        evidence_text = "\n".join(
            f"- [{item.get('source_type', '?')}] {item.get('content', '')[:300]}"
            for item in evidence_items[:8]
        ) or "No evidence available."

        prior = agent_memory.recall_as_text(company_id, "SEC evidence findings and Org-AI-R history")
        sys_text = (
            "You are a PE due-diligence analyst.  Summarise the AI readiness "
            "signals from the evidence below in 3-5 bullet points.  Focus on "
            "data infrastructure, governance, and technology signals."
        )
        if prior:
            sys_text += f"\n\n{prior}"

        messages = [
            {
                "role": "system",
                "content": sys_text,
            },
            {
                "role": "user",
                "content": (
                    f"Company: {company_id}\n\nEvidence:\n{evidence_text}\n\n"
                    "Provide a concise SEC/evidence analysis."
                ),
            },
        ]

        try:
            summary = self.router.complete_sync("evidence_extraction", messages)
        except Exception as exc:
            summary = f"[LLM unavailable: {exc}]"

        result: Dict[str, Any] = {
            "company_id": company_id,
            "evidence_count": evidence_data.get("count", len(evidence_items)),
            "evidence": evidence_items,
            "llm_summary": summary,
        }

        logger.info("SECAnalysisAgent completed for %s (%d items)", company_id, len(evidence_items))
        return {
            "sec_analysis": result,
            "messages": _append_message(self.NAME, self.NAME, summary),
        }


# ---------------------------------------------------------------------------
# ScoringAgent — CS3 Org-AI-R composite score
# ---------------------------------------------------------------------------

class ScoringAgent:
    """Calls the scoring endpoint and records the composite Org-AI-R result.

    Reads:  ``company_id``
    Writes: ``scoring_result``, ``requires_approval``, ``approval_reason``, ``messages``
    """

    NAME = "scoring_agent"

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        tool_caller: Optional[MCPToolCaller] = None,
        hitl_score_change_threshold: float = 20.0,
    ):
        self.router = router or ModelRouter()
        self.tools = tool_caller or MCPToolCaller()
        self.hitl_threshold = hitl_score_change_threshold

    def __call__(self, state: DueDiligenceState) -> Dict[str, Any]:
        company_id = state["company_id"]
        logger.info("ScoringAgent started for %s", company_id)

        try:
            score_data = self.tools.calculate_org_air_score(company_id)
        except Exception as exc:
            error_msg = f"Scoring fetch failed: {exc}"
            logger.error(error_msg)
            return {
                "scoring_result": {"error": error_msg, "company_id": company_id},
                "messages": _append_message(self.NAME, self.NAME, error_msg),
                "error": error_msg,
            }

        org_air = score_data.get("org_air", 0.0)

        # HITL gate: score outside normal operating range triggers human review
        requires_approval = org_air > 80 or org_air < 40
        approval_reason: Optional[str] = None
        if requires_approval:
            approval_reason = (
                f"Score {org_air:.1f} outside normal range [40, 80].  "
                "Human review required before proceeding."
            )
            logger.warning("HITL triggered for %s: score=%.1f", company_id, org_air)

        summary = (
            f"Org-AI-R score for {company_id}: {org_air:.1f}  "
            f"(V^R={score_data.get('vr_score', 0):.1f}, "
            f"H^R={score_data.get('hr_score', 0):.1f})"
        )

        logger.info("ScoringAgent completed for %s: %.1f", company_id, org_air)
        updates: Dict[str, Any] = {
            "scoring_result": score_data,
            "requires_approval": requires_approval,
            "messages": _append_message(self.NAME, self.NAME, summary),
        }
        if approval_reason:
            updates["approval_reason"] = approval_reason
        return updates


# ---------------------------------------------------------------------------
# EvidenceAgent — CS2 talent dimension specialist
# ---------------------------------------------------------------------------

class EvidenceAgent:
    """Retrieves talent-specific evidence and builds a technology-hiring signal summary.

    Reads:  ``company_id``
    Writes: ``talent_analysis``, ``messages``
    """

    NAME = "talent_agent"

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        tool_caller: Optional[MCPToolCaller] = None,
    ):
        self.router = router or ModelRouter()
        self.tools = tool_caller or MCPToolCaller()

    def __call__(self, state: DueDiligenceState) -> Dict[str, Any]:
        company_id = state["company_id"]
        logger.info("EvidenceAgent started for %s", company_id)

        # Fetch evidence for the 3 key dimensions per CS5 spec
        dimensions = ["data_infrastructure", "talent", "use_case_portfolio"]
        all_evidence_items: List[Dict[str, Any]] = []
        dimension_evidence: Dict[str, List[Dict[str, Any]]] = {}

        for dim in dimensions:
            try:
                evidence_data = self.tools.get_company_evidence(
                    company_id, dimension=dim, limit=5
                )
                items = evidence_data.get("evidence", [])
                dimension_evidence[dim] = items
                all_evidence_items.extend(items)
            except Exception as exc:
                logger.warning("Evidence fetch failed for %s/%s: %s", company_id, dim, exc)
                dimension_evidence[dim] = []

        evidence_items = all_evidence_items

        # Filter to technology_hiring signals
        tech_hiring = [
            item for item in evidence_items
            if item.get("signal_category") == "technology_hiring"
        ]

        # LLM summarises multi-dimension evidence
        talent_text = "\n".join(
            f"- [{item.get('dimension', '?')}] {item.get('content', '')[:300]}"
            for item in (evidence_items)[:8]
        ) or "No evidence available."

        prior = agent_memory.recall_as_text(company_id, "talent signals and prior assessments")
        sys_text = (
            "You are a talent and workforce analyst for a private equity firm.  "
            "Analyse the technology-hiring evidence and identify AI talent trends."
        )
        if prior:
            sys_text += f"\n\n{prior}"
        messages = [
            {
                "role": "system",
                "content": sys_text,
            },
            {
                "role": "user",
                "content": (
                    f"Company: {company_id}\n\nTalent evidence:\n{talent_text}\n\n"
                    "Summarise the AI talent readiness in 3 bullet points."
                ),
            },
        ]

        try:
            summary = self.router.complete_sync("evidence_extraction", messages)
        except Exception as exc:
            summary = f"[LLM unavailable: {exc}]"

        result: Dict[str, Any] = {
            "company_id": company_id,
            "evidence": evidence_items,
            "dimension_evidence": dimension_evidence,
            "technology_hiring_signals": tech_hiring,
            "llm_summary": summary,
        }

        logger.info(
            "EvidenceAgent completed for %s (%d items across %d dimensions, %d tech-hiring)",
            company_id, len(evidence_items), len(dimensions), len(tech_hiring),
        )
        return {
            "talent_analysis": result,
            "evidence_justifications": dimension_evidence,
            "messages": _append_message(self.NAME, self.NAME, summary),
        }


# ---------------------------------------------------------------------------
# ValueCreationAgent — CS5 gap analysis + EBITDA projection
# ---------------------------------------------------------------------------

class ValueCreationAgent:
    """Runs gap analysis and projects EBITDA impact from AI readiness improvement.

    Reads:  ``company_id``, ``scoring_result``
    Writes: ``value_creation_plan``, ``requires_approval``, ``approval_reason``, ``messages``
    """

    NAME = "value_creation_agent"

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        tool_caller: Optional[MCPToolCaller] = None,
        target_org_air: float = 85.0,
        hitl_ebitda_threshold: float = 50.0,
    ):
        self.router = router or ModelRouter()
        self.tools = tool_caller or MCPToolCaller()
        self.target_org_air = target_org_air
        self.hitl_ebitda_threshold = hitl_ebitda_threshold

    def __call__(self, state: DueDiligenceState) -> Dict[str, Any]:
        company_id = state["company_id"]
        scoring = state.get("scoring_result") or {}
        logger.info("ValueCreationAgent started for %s", company_id)

        # 1. Gap analysis
        try:
            gap_result = self.tools.run_gap_analysis(company_id, self.target_org_air)
        except Exception as exc:
            error_msg = f"Gap analysis failed: {exc}"
            logger.error(error_msg)
            return {
                "value_creation_plan": {"error": error_msg, "company_id": company_id},
                "messages": _append_message(self.NAME, self.NAME, error_msg),
                "error": error_msg,
            }

        # 2. EBITDA projection
        entry_score = float(scoring.get("org_air", 50.0))
        h_r_score = float(scoring.get("hr_score", 70.0))
        try:
            ebitda_result = self.tools.project_ebitda_impact(
                company_id=company_id,
                entry_score=entry_score,
                target_score=self.target_org_air,
                h_r_score=h_r_score,
            )
        except Exception as exc:
            ebitda_result = {"error": str(exc)}

        # 3. HITL gate — large EBITDA projection requires human sign-off
        requires_approval = ebitda_result.get("requires_approval", False)
        approval_reason: Optional[str] = None
        risk_adjusted = ebitda_result.get("risk_adjusted", 0.0)
        if requires_approval or (isinstance(risk_adjusted, (int, float)) and abs(risk_adjusted) >= self.hitl_ebitda_threshold):
            requires_approval = True
            approval_reason = (
                f"Projected EBITDA impact ({risk_adjusted}%) exceeds HITL threshold "
                f"({self.hitl_ebitda_threshold}%).  Human approval required."
            )
            logger.warning("HITL triggered for value-creation %s: ebitda=%s", company_id, risk_adjusted)

        # 4. LLM narrative
        gap_text = json.dumps(gap_result, indent=2)[:800]
        ebitda_text = json.dumps(ebitda_result, indent=2)[:400]
        prior = agent_memory.recall_as_text(company_id, "value creation plan, gaps, and EBITDA impact")
        sys_text = (
            "You are a value-creation specialist at a private equity firm.  "
            "Synthesise the gap analysis and EBITDA projection into a concise "
            "investment narrative (3-5 sentences)."
        )
        if prior:
            sys_text += f"\n\n{prior}"

        messages = [
            {
                "role": "system",
                "content": sys_text,
            },
            {
                "role": "user",
                "content": (
                    f"Company: {company_id}\n"
                    f"Entry Org-AI-R: {entry_score:.1f} → Target: {self.target_org_air}\n\n"
                    f"Gap analysis:\n{gap_text}\n\nEBITDA projection:\n{ebitda_text}"
                ),
            },
        ]
        try:
            narrative = self.router.complete_sync("ic_summary", messages)
        except Exception as exc:
            narrative = f"[LLM unavailable: {exc}]"

        plan: Dict[str, Any] = {
            "company_id": company_id,
            "gap_analysis": gap_result,
            "ebitda_projection": ebitda_result,
            "narrative": narrative,
            "delta_air": ebitda_result.get("delta_air"),
            "scenarios": ebitda_result.get("scenarios"),
            "risk_adjusted": risk_adjusted,
            "requires_approval": requires_approval,
        }

        logger.info("ValueCreationAgent completed for %s", company_id)
        updates: Dict[str, Any] = {
            "value_creation_plan": plan,
            "requires_approval": requires_approval,
            "messages": _append_message(self.NAME, self.NAME, narrative),
        }
        if approval_reason:
            updates["approval_reason"] = approval_reason
        return updates
