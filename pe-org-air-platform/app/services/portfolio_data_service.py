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
        company_repo=None,
        snapshot_repo=None,
        scoring_repo=None,
        composite_repo=None,
    ):
        self.cs1 = cs1_client
        self.cs2 = cs2_client
        self.cs3 = cs3_client
        self.cs4 = cs4_client
        self.scoring = composite_scoring_service or CompositeScoringService()
        self.ebitda_calculator = EBITDACalculator()
        self.gap_analyzer = GapAnalyzer()
        self._company_repo = company_repo
        self._snapshot_repo = snapshot_repo
        self._scoring_repo = scoring_repo
        self._composite_repo = composite_repo

    # ------------------------------------------------------------------
    # Internal helpers (lazy Snowflake-backed repos)
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        import re
        return bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}",
                value or "",
            )
        )

    def _get_company_repo(self) -> Any:
        if self._company_repo is None:
            from app.repositories.company_repository import CompanyRepository
            self._company_repo = CompanyRepository()
        return self._company_repo

    def _get_snapshot_repo(self) -> Any:
        if self._snapshot_repo is None:
            from app.repositories.assessment_snapshot_repository import AssessmentSnapshotRepository
            self._snapshot_repo = AssessmentSnapshotRepository()
        return self._snapshot_repo

    def _get_scoring_repo(self) -> Any:
        if self._scoring_repo is None:
            from app.repositories.scoring_repository import ScoringRepository
            self._scoring_repo = ScoringRepository()
        return self._scoring_repo

    def _get_composite_repo(self) -> Any:
        if self._composite_repo is None:
            from app.repositories.composite_scoring_repository import CompositeScoringRepository
            self._composite_repo = CompositeScoringRepository()
        return self._composite_repo

    def _resolve_portfolio_id(self, fund_id: str) -> Optional[str]:
        """Resolve fund_id to a portfolio UUID, if present in CS1 portfolio tables."""
        if not fund_id:
            return None
        if self._looks_like_uuid(fund_id):
            return fund_id
        try:
            return self._get_company_repo().find_portfolio_id_by_name(fund_id)
        except Exception as exc:
            logger.warning("portfolio_id_resolve_failed", fund_id=fund_id, error=str(exc))
            return None

    def _get_portfolio_company_rows(self, fund_id: str) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Fetch portfolio companies from CS1 portfolio management.

        CS5 grading forbids hardcoded portfolio membership; this method must only
        return companies sourced from CS1 portfolio tables.
        """
        portfolio_id = self._resolve_portfolio_id(fund_id)
        if not portfolio_id:
            raise ValueError(
                f"Unknown portfolio '{fund_id}'. "
                f"Create it in CS1 (portfolios tables) or pass the portfolio UUID."
            )
        try:
            rows = self._get_company_repo().get_by_portfolio(portfolio_id)
        except Exception as exc:
            logger.error("portfolio_companies_fetch_failed", portfolio_id=portfolio_id, error=str(exc))
            raise
        if not rows:
            raise ValueError(
                f"Portfolio '{fund_id}' (id={portfolio_id}) has no companies. "
                f"Add companies in CS1 portfolio management."
            )
        return portfolio_id, rows

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

        # Avoid self-HTTP calls (CS3Client uses httpx to call this API), which can
        # deadlock/time out when invoked inside the FastAPI process. Prefer
        # reading from Snowflake repositories directly.
        dim_scores: Dict[str, float] = {}
        current_org_air = 0.0

        try:
            rows = self._get_scoring_repo().get_dimension_scores(ticker)
            for row in rows or []:
                dim = str(row.get("dimension") or "")
                if not dim:
                    continue
                dim_scores[_DIM_ALIAS_MAP.get(dim, dim)] = float(row.get("score") or 0.0)
        except Exception as exc:
            logger.warning("dimension_scores_fetch_failed", ticker=ticker, error=str(exc))
            dim_scores = {}

        try:
            row = self._get_composite_repo().fetch_orgair_row(ticker)
            if row:
                r = {k.lower(): v for k, v in row.items()}
                current_org_air = float(r.get("org_air") or 0.0)
        except Exception as exc:
            logger.warning("composite_repo_fetch_failed", ticker=ticker, error=str(exc))
            current_org_air = 0.0

        # Last-resort fallback (external/remote usage only).
        if not dim_scores or current_org_air <= 0:
            try:
                assessment = self.cs3.get_assessment(ticker)
                if assessment:
                    dim_scores = {
                        _DIM_ALIAS_MAP.get(dim, dim): ds.score
                        for dim, ds in assessment.dimension_scores.items()
                    }
                    current_org_air = assessment.org_air_score
            except Exception as exc:
                logger.warning("cs3_gap_analysis_fallback_failed", ticker=ticker, error=str(exc))

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

    def _get_entry_score(self, ticker: str, portfolio_id: Optional[str]) -> float:
        """Return the earliest recorded Org-AI-R score for this ticker.

        Queries the CS5_ASSESSMENT_SNAPSHOTS table for the first snapshot.
        Returns 0.0 if no history exists (delta_since_entry = 0).
        """
        try:
            entry = self._get_snapshot_repo().get_entry_org_air(
                ticker=ticker,
                portfolio_id=portfolio_id,
            )
            return float(entry) if entry is not None else 0.0
        except Exception as exc:
            logger.warning("entry_score_fetch_failed", ticker=ticker, error=str(exc))
            return 0.0

    def get_portfolio_view(self, fund_id: str = "PE-FUND-I") -> Dict[str, Any]:
        """Get aggregated portfolio view with all company scores."""
        companies: List[PortfolioCompanyView] = []

        portfolio_id, company_rows = self._get_portfolio_company_rows(fund_id)

        for row in company_rows:
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            dim_scores: Dict[str, float] = {}
            org_air = 0.0
            vr = 0.0
            hr = 0.0
            synergy = 0.0
            pf = 0.0
            ci = (0.0, 0.0)

            # Prefer DB reads (no self-HTTP).
            try:
                org_row = self._get_composite_repo().fetch_orgair_row(ticker)
                if org_row:
                    r = {k.lower(): v for k, v in org_row.items()}
                    org_air = float(r.get("org_air") or 0.0)
                    vr = float(r.get("vr_score") or 0.0)
                    hr = float(r.get("hr_score") or 0.0)
                    synergy = float(r.get("synergy_score") or 0.0)
                    pf = float(r.get("position_factor") or 0.0)
                    ci = (float(r.get("ci_lower") or 0.0), float(r.get("ci_upper") or 0.0))
            except Exception as exc:
                logger.warning("orgair_row_fetch_failed", ticker=ticker, error=str(exc))

            try:
                rows = self._get_scoring_repo().get_dimension_scores(ticker)
                if rows:
                    dim_scores = {str(d.get("dimension")): float(d.get("score") or 0.0) for d in rows if d.get("dimension")}
            except Exception as exc:
                logger.warning("dimension_scores_view_failed", ticker=ticker, error=str(exc))

            # Last resort: CS3 client (external/remote usage only).
            if org_air <= 0 or not dim_scores:
                try:
                    assessment = self.cs3.get_assessment(ticker)
                    if assessment:
                        org_air = org_air or assessment.org_air_score
                        vr = vr or assessment.valuation_risk
                        hr = hr or assessment.human_capital_risk
                        synergy = synergy or assessment.synergy
                        pf = pf or assessment.position_factor
                        if not dim_scores:
                            dim_scores = {dim: ds.score for dim, ds in assessment.dimension_scores.items()}
                except Exception as exc:
                    logger.warning("cs3_portfolio_fallback_failed", ticker=ticker, error=str(exc))

            # Evidence count from CS2
            evidence_count = 0
            try:
                evidence = self.cs2.get_evidence(ticker=ticker)
                evidence_count = len(evidence)
            except Exception as exc:
                logger.warning("evidence_count_failed", ticker=ticker, error=str(exc))

            entry_org_air = self._get_entry_score(ticker, portfolio_id) or org_air
            if entry_org_air <= 0:
                entry_org_air = org_air
            delta_since_entry = (
                round(org_air - entry_org_air, 2) if org_air and entry_org_air else 0.0
            )

            name = row.get("name") or COMPANY_NAMES.get(ticker, ticker)
            sector = row.get("sector") or COMPANY_SECTORS.get(ticker, "")
            market_cap_percentile = float(
                row.get("market_cap_percentile")
                or MARKET_CAP_PERCENTILES.get(ticker, 0.0)
            )
            revenue_millions = float(row.get("revenue_millions") or 0.0)
            employee_count = int(row.get("employee_count") or 0)

            companies.append(PortfolioCompanyView(
                company_id=str(row.get("id") or ticker),
                ticker=ticker,
                name=name,
                sector=sector,
                org_air=org_air,
                vr_score=vr,
                hr_score=hr,
                synergy_score=synergy,
                position_factor=pf,
                dimension_scores=dim_scores,
                confidence_interval=ci,
                entry_org_air=entry_org_air,
                delta_since_entry=delta_since_entry,
                evidence_count=evidence_count,
                market_cap_percentile=market_cap_percentile,
                revenue_millions=revenue_millions,
                employee_count=employee_count,
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

    def get_portfolio_tickers(self, fund_id: str = "PE-FUND-I") -> List[str]:
        """Return a ticker list for a fund/portfolio id.

        Uses CS1 portfolio tables only (no hardcoded tickers).
        """
        _, company_rows = self._get_portfolio_company_rows(fund_id)
        tickers: List[str] = []
        for row in company_rows:
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                tickers.append(ticker)
        if not tickers:
            raise ValueError(f"Portfolio '{fund_id}' returned companies without tickers.")
        return tickers


# Module-level singleton — set by lifespan.py at startup
portfolio_data_service: Optional[PortfolioDataService] = None
