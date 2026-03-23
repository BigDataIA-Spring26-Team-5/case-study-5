"""Portfolio Data Service — unified facade for MCP tools and agents.

This is the ONLY data source for CS5 MCP tools and LangGraph agents.
It delegates to CS1-CS4 clients (in-process, not HTTP) and value-creation modules.
"""
from __future__ import annotations

import structlog
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

from app.services.integration.cs1_client import CS1Client, Company, Sector
from app.services.integration.cs2_client import CS2Client, CS2Evidence
from app.services.integration.cs3_client import (
    CS3Client, CompanyAssessment, DimensionScore, DIMENSIONS, score_to_level,
    _DIM_ALIAS_MAP,
)
from app.services.integration.cs4_client import CS4Client, JustificationResult
from app.services.value_creation.ebitda import EBITDACalculator, EBITDAProjection
from app.services.value_creation.gap_analysis import GapAnalyzer, GapAnalysisResult
from app.services.composite_scoring_service import (
    CompositeScoringService, OrgAIRResponse,
    COMPANY_SECTORS, COMPANY_NAMES, MARKET_CAP_PERCENTILES,
)
from app.config.company_mappings import CS3_PORTFOLIO

logger = structlog.get_logger(__name__)


@dataclass
class PortfolioCompanyView:
    """Enriched company view combining CS1-CS3 data."""
    company_id: str = ""
    ticker: str = ""
    name: str = ""
    sector: str = ""
    org_air: float = 0.0
    vr_score: float = 0.0
    hr_score: float = 0.0
    synergy_score: float = 0.0
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    confidence_interval: tuple = (0.0, 0.0)
    entry_org_air: float = 0.0
    delta_since_entry: float = 0.0
    evidence_count: int = 0
    position_factor: float = 0.0
    market_cap_percentile: float = 0.0
    revenue_millions: float = 0.0
    employee_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Backward compatibility alias
CompanyView = PortfolioCompanyView


@dataclass
class PortfolioView:
    """Portfolio-level aggregated view."""
    fund_id: str
    companies: List[PortfolioCompanyView] = field(default_factory=list)
    fund_air_score: float = 0.0
    total_companies: int = 0
    ai_leaders: int = 0
    ai_laggards: int = 0
    avg_vr: float = 0.0
    avg_hr: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PortfolioDataService:
    """Unified data service for CS5 — wraps CS1-CS4 clients in-process."""

    def __init__(
        self,
        cs1_client: CS1Client,
        cs2_client: CS2Client,
        cs3_client: CS3Client,
        cs4_client: Optional[CS4Client] = None,
        composite_scoring_service: Optional[CompositeScoringService] = None,
    ):
        self.cs1 = cs1_client
        self.cs2 = cs2_client
        self.cs3 = cs3_client
        self.cs4 = cs4_client
        self.scoring = composite_scoring_service or CompositeScoringService()
        self.ebitda_calculator = EBITDACalculator()
        self.gap_analyzer = GapAnalyzer()

    # ------------------------------------------------------------------
    # Company-level queries
    # ------------------------------------------------------------------

    def get_company_assessment(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get full scoring assessment for a company via CS3."""
        ticker = ticker.upper()
        assessment = self.cs3.get_assessment(ticker)
        if not assessment:
            return None
        return {
            "ticker": assessment.ticker,
            "company_id": assessment.company_id,
            "org_air_score": assessment.org_air_score,
            "vr_score": assessment.valuation_risk,
            "hr_score": assessment.human_capital_risk,
            "synergy": assessment.synergy,
            "position_factor": assessment.position_factor,
            "talent_concentration": assessment.talent_concentration,
            "dimension_scores": {
                dim: {
                    "score": ds.score,
                    "level": ds.level,
                    "level_name": ds.level_name,
                }
                for dim, ds in assessment.dimension_scores.items()
            },
        }

    def get_company_evidence(
        self,
        ticker: str,
        dimension: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get evidence for a company via CS2."""
        ticker = ticker.upper()
        signal_cats = None
        if dimension:
            # Map dimensions to signal categories
            dim_to_signal = {
                "data_infrastructure": ["digital_presence"],
                "ai_governance": ["governance_signals"],
                "technology_stack": ["digital_presence", "technology_hiring"],
                "talent": ["technology_hiring"],
                "leadership": ["leadership_signals"],
                "use_case_portfolio": ["innovation_activity"],
                "culture": ["culture_signals"],
            }
            signal_cats = dim_to_signal.get(dimension)

        evidence = self.cs2.get_evidence(
            ticker=ticker,
            signal_categories=signal_cats,
        )
        return [
            {
                "evidence_id": e.evidence_id,
                "source_type": e.source_type,
                "signal_category": e.signal_category,
                "content": e.content[:500],
                "confidence": e.confidence,
            }
            for e in evidence[:limit]
        ]

    def generate_justification(
        self, ticker: str, dimension: str
    ) -> Dict[str, Any]:
        """Generate evidence-backed justification via CS4."""
        if self.cs4 is None:
            raise RuntimeError("CS4 client not available (ChromaDB/onnxruntime missing)")
        result = self.cs4.generate_justification(ticker.upper(), dimension)
        return result.to_dict()

    def compute_org_air_score(self, ticker: str) -> Dict[str, Any]:
        """Compute full Org-AI-R score via composite scoring service."""
        response: OrgAIRResponse = self.scoring.compute_orgair(ticker.upper())
        if response.status != "success":
            return {"ticker": ticker, "status": "failed", "error": response.error}
        b = response.breakdown
        return {
            "ticker": response.ticker,
            "status": "success",
            "org_air_score": response.org_air_score,
            "vr_score": b.vr_score if b else None,
            "hr_score": b.hr_score if b else None,
            "synergy": b.synergy_score if b else None,
            "confidence_interval": {
                "lower": b.orgair_ci.ci_lower if b and b.orgair_ci else None,
                "upper": b.orgair_ci.ci_upper if b and b.orgair_ci else None,
            } if b else None,
        }

    # ------------------------------------------------------------------
    # Value creation
    # ------------------------------------------------------------------

    def project_ebitda_impact(
        self,
        company_id: str,
        entry_score: float,
        target_score: float,
        h_r_score: float,
    ) -> Dict[str, Any]:
        """Project EBITDA impact from score improvement."""
        sector = COMPANY_SECTORS.get(company_id.upper(), "technology")
        projection = self.ebitda_calculator.project(
            company_id=company_id.upper(),
            entry_score=entry_score,
            target_score=target_score,
            h_r_score=h_r_score,
            sector=sector,
        )
        return projection.to_dict()

    def run_gap_analysis(
        self, ticker: str, target_org_air: float
    ) -> Dict[str, Any]:
        """Run gap analysis for a company."""
        ticker = ticker.upper()
        assessment = self.cs3.get_assessment(ticker)

        if assessment:
            dim_scores = {
                _DIM_ALIAS_MAP.get(dim, dim): ds.score
                for dim, ds in assessment.dimension_scores.items()
            }
            current_org_air = assessment.org_air_score
        else:
            dim_scores = {}
            current_org_air = 0.0

        result = self.gap_analyzer.analyze(
            company_id=ticker,
            dimension_scores=dim_scores,
            current_org_air=current_org_air,
            target_org_air=target_org_air,
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # Portfolio-level queries
    # ------------------------------------------------------------------

    async def _get_entry_score(self, company_id: str) -> float:
        """Query entry Org-AI-R score for a company.

        Stub — in production this would query the PORTFOLIO_POSITIONS table.
        Falls back to current score (delta_since_entry = 0).
        """
        return 0.0

    def get_portfolio_view(self, fund_id: str = "PE-FUND-I") -> Dict[str, Any]:
        """Get aggregated portfolio view with all company scores."""
        companies: List[PortfolioCompanyView] = []

        for ticker in CS3_PORTFOLIO:
            assessment = self.cs3.get_assessment(ticker)
            dim_scores: Dict[str, float] = {}
            org_air = 0.0
            vr = 0.0
            hr = 0.0
            synergy = 0.0
            pf = 0.0
            ci = (0.0, 0.0)

            if assessment:
                org_air = assessment.org_air_score
                vr = assessment.valuation_risk
                hr = assessment.human_capital_risk
                synergy = assessment.synergy
                pf = assessment.position_factor
                dim_scores = {
                    dim: ds.score
                    for dim, ds in assessment.dimension_scores.items()
                }

            # Evidence count from CS2
            evidence_count = 0
            try:
                evidence = self.cs2.get_evidence(ticker=ticker)
                evidence_count = len(evidence)
            except Exception:
                pass

            companies.append(PortfolioCompanyView(
                company_id=ticker,
                ticker=ticker,
                name=COMPANY_NAMES.get(ticker, ticker),
                sector=COMPANY_SECTORS.get(ticker, ""),
                org_air=org_air,
                vr_score=vr,
                hr_score=hr,
                synergy_score=synergy,
                position_factor=pf,
                dimension_scores=dim_scores,
                confidence_interval=ci,
                evidence_count=evidence_count,
                market_cap_percentile=MARKET_CAP_PERCENTILES.get(ticker, 0.0),
            ))

        # Portfolio aggregates
        scores = [c.org_air for c in companies if c.org_air > 0]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        vr_scores = [c.vr_score for c in companies if c.vr_score > 0]
        hr_scores = [c.hr_score for c in companies if c.hr_score > 0]

        portfolio = PortfolioView(
            fund_id=fund_id,
            companies=companies,
            fund_air_score=round(avg_score, 2),
            total_companies=len(companies),
            ai_leaders=sum(1 for c in companies if c.org_air >= 70),
            ai_laggards=sum(1 for c in companies if 0 < c.org_air < 50),
            avg_vr=round(sum(vr_scores) / len(vr_scores), 2) if vr_scores else 0.0,
            avg_hr=round(sum(hr_scores) / len(hr_scores), 2) if hr_scores else 0.0,
        )
        return portfolio.to_dict()


# Module-level singleton — set by lifespan.py at startup
portfolio_data_service: Optional[PortfolioDataService] = None
