"""IC Meeting Prep Workflow — full 7-dimension package for investment committees."""
from __future__ import annotations

import asyncio
import structlog
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict

from app.services.integration.cs1_client import Company, Sector
from app.services.integration.cs3_client import CompanyAssessment, DimensionScore, score_to_level
from app.services.justification.generator import JustificationGenerator, ScoreJustification

logger = structlog.get_logger()

DIMENSIONS = [
    "data_infrastructure",
    "ai_governance",
    "technology_stack",
    "talent",
    "leadership",
    "use_case_portfolio",
    "culture",
]


@dataclass
class ICMeetingPackage:
    company: Company
    assessment: Optional[CompanyAssessment]
    dimension_justifications: Dict[str, ScoreJustification]
    executive_summary: str
    key_strengths: List[str]
    key_gaps: List[str]
    risk_factors: List[str]
    recommendation: str  # "PROCEED" | "PROCEED WITH CAUTION" | "FURTHER DILIGENCE"
    generated_at: str
    total_evidence_count: int
    avg_evidence_strength: str  # "strong", "moderate", "weak"


class ICPrepWorkflow:
    """Orchestrates full IC meeting preparation package."""

    def __init__(
        self,
        company_repo=None,
        scoring_repo=None,
        composite_repo=None,
        generator: Optional[JustificationGenerator] = None,
    ):
        from app.repositories.company_repository import CompanyRepository
        from app.repositories.scoring_repository import ScoringRepository
        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        self.company_repo = company_repo or CompanyRepository()
        self.scoring_repo = scoring_repo or ScoringRepository()
        self.composite_repo = composite_repo or CompositeScoringRepository()
        self.generator = generator or JustificationGenerator(scoring_repo=self.scoring_repo)

    async def prepare_meeting(
        self,
        ticker: str,
        focus_dimensions: Optional[List[str]] = None,
    ) -> ICMeetingPackage:
        """Generate full IC meeting package for a company."""
        # Step 1: Fetch company metadata directly from DB (no HTTP)
        company = self._fetch_company(ticker)
        if company is None:
            company = Company(
                company_id=ticker,
                ticker=ticker,
                name=ticker,
                sector=Sector.BUSINESS_SERVICES,
            )

        # Step 2: Fetch assessment directly from DB (no HTTP)
        assessment = self._fetch_assessment(ticker)

        # Step 3: Generate justifications concurrently for all dimensions
        # asyncio.gather() runs all 7 dimensions in parallel instead of sequentially
        # This is the main performance benefit of making this method async
        dims_to_process = focus_dimensions or DIMENSIONS

        async def _safe_justify(dim: str) -> tuple[str, Optional[ScoreJustification]]:
            """Wrap sync justification call — returns (dim, result) or (dim, None) on error."""
            try:
                # JustificationGenerator is still sync — run in thread pool
                # to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    self.generator.generate_justification,
                    ticker,
                    dim,
                )
                return dim, result
            except Exception as e:
                logger.warning("justification_failed dim=%s ticker=%s error=%s", dim, ticker, e)
                return dim, None

        results = await asyncio.gather(*[_safe_justify(dim) for dim in dims_to_process])
        justifications: Dict[str, ScoreJustification] = {
            dim: j for dim, j in results if j is not None
        }

        # Step 4: Identify strengths (level >= 4, strong/moderate evidence)
        strengths = self._identify_strengths(justifications)

        # Step 5: Identify gaps (level <= 2)
        gaps = self._identify_gaps(justifications)

        # Step 6: Assess risks
        risks = self._assess_risks(assessment, justifications)

        # Step 7: Generate executive summary
        summary = self._generate_summary(company, assessment, justifications)

        # Step 8: Generate recommendation
        recommendation = self._generate_recommendation(assessment, justifications, risks)

        total_evidence = sum(
            len(j.supporting_evidence) for j in justifications.values()
        )
        strength_counts = {"strong": 0, "moderate": 0, "weak": 0}
        for j in justifications.values():
            strength_counts[j.evidence_strength] = (
                strength_counts.get(j.evidence_strength, 0) + 1
            )
        avg_strength = max(strength_counts, key=strength_counts.get) if strength_counts else "weak"

        return ICMeetingPackage(
            company=company,
            assessment=assessment,
            dimension_justifications=justifications,
            executive_summary=summary,
            key_strengths=strengths,
            key_gaps=gaps,
            risk_factors=risks,
            recommendation=recommendation,
            generated_at=datetime.utcnow().isoformat(),
            total_evidence_count=total_evidence,
            avg_evidence_strength=avg_strength,
        )

    def _fetch_company(self, ticker: str) -> Optional[Company]:
        """Build Company from DB row — no HTTP."""
        row = self.company_repo.get_by_ticker(ticker)
        if not row:
            return None
        raw_sector = row.get("sector", "")
        try:
            sector = Sector(raw_sector.lower().replace(" ", "_"))
        except (ValueError, AttributeError):
            sector = Sector.BUSINESS_SERVICES
        return Company(
            company_id=str(row.get("id", ticker)),
            ticker=row.get("ticker", ticker),
            name=row.get("name", ticker),
            sector=sector,
            sub_sector=row.get("sub_sector", ""),
            market_cap_percentile=float(row.get("market_cap_percentile") or 0.0),
            revenue_millions=float(row.get("revenue_millions") or 0.0),
            employee_count=int(row.get("employee_count") or 0),
            fiscal_year_end=row.get("fiscal_year_end", ""),
        )

    def _fetch_assessment(self, ticker: str) -> Optional[CompanyAssessment]:
        """Build CompanyAssessment from DB rows — no HTTP."""
        dim_rows = self.scoring_repo.get_dimension_scores(ticker)
        if not dim_rows:
            return None
        dim_scores: Dict[str, DimensionScore] = {}
        for row in dim_rows:
            dim = row["dimension"]
            score = float(row.get("score", 0.0))
            level, level_name = score_to_level(score)
            dim_scores[dim] = DimensionScore(
                dimension=dim, score=score, level=level, level_name=level_name,
            )
        # Composite scores (TC, VR, PF, HR) — DictCursor returns uppercase keys
        composite = self.composite_repo.fetch_tc_vr_row(ticker) or {}
        orgair = self.composite_repo.fetch_orgair_row(ticker) or {}
        return CompanyAssessment(
            company_id=ticker,
            ticker=ticker,
            dimension_scores=dim_scores,
            talent_concentration=float(composite.get("TC", 0.0) or 0.0),
            valuation_risk=float(composite.get("VR", 0.0) or 0.0),
            position_factor=float(composite.get("PF", 0.0) or 0.0),
            human_capital_risk=float(composite.get("HR", 0.0) or 0.0),
            org_air_score=float(orgair.get("ORG_AIR", 0.0) or 0.0),
        )

    @staticmethod
    def _identify_strengths(justifications: Dict[str, ScoreJustification]) -> List[str]:
        strengths = []
        for dim, j in justifications.items():
            if j.level >= 4:
                label = f"{dim.replace('_', ' ').title()}: Level {j.level} ({j.score:.0f}/100) — {j.level_name}"
                if j.evidence_strength == "weak":
                    label += " (score-driven; limited evidence)"
                strengths.append(label)
        return strengths

    @staticmethod
    def _identify_gaps(justifications: Dict[str, ScoreJustification]) -> List[str]:
        gaps = []
        for dim, j in justifications.items():
            if j.level <= 2:
                gaps.append(
                    f"{dim.replace('_', ' ').title()}: Level {j.level} ({j.score:.0f}/100) — {j.level_name}"
                )
        return gaps

    @staticmethod
    def _assess_risks(
        assessment: Optional[CompanyAssessment],
        justifications: Dict[str, ScoreJustification],
    ) -> List[str]:
        risks = []
        if assessment:
            if assessment.talent_concentration > 0.7:
                risks.append(
                    f"High talent concentration risk (TC={assessment.talent_concentration:.2f}) — "
                    "key-person dependency"
                )
            if assessment.valuation_risk > 0.6:
                risks.append(
                    f"Elevated valuation risk (V^R={assessment.valuation_risk:.2f})"
                )
            if assessment.position_factor < -0.3:
                risks.append(
                    f"Negative position factor (PF={assessment.position_factor:.2f}) — "
                    "unfavorable market positioning"
                )
        # Weak evidence dimensions
        weak_dims = [
            dim for dim, j in justifications.items()
            if j.evidence_strength == "weak"
        ]
        if weak_dims:
            risks.append(
                f"Insufficient evidence for: {', '.join(weak_dims)} — further diligence required"
            )
        return risks

    @staticmethod
    def _generate_summary(
        company: Company,
        assessment: Optional[CompanyAssessment],
        justifications: Dict[str, ScoreJustification],
    ) -> str:
        n_dims = len(justifications)
        avg_score = (
            sum(j.score for j in justifications.values()) / n_dims
            if n_dims > 0 else 0.0
        )
        org_air_str = (
            f"{assessment.org_air_score:.1f}"
            if assessment and assessment.org_air_score
            else "not yet computed"
        )
        employee_str = (
            f"{company.employee_count:,}" if company.employee_count else "N/A"
        )
        revenue_str = (
            f"${company.revenue_millions:.0f}M" if company.revenue_millions else "N/A"
        )
        return (
            f"{company.name} ({company.ticker}) demonstrates an average AI readiness score of "
            f"{avg_score:.0f}/100 across {n_dims} assessed dimensions, with an Org-AI-R composite "
            f"of {org_air_str}. The company operates in {company.sector.value.replace('_', ' ').title()} with approximately "
            f"{employee_str} employees and {revenue_str} revenue. "
            f"Key differentiators and risk factors are detailed in the dimension-level justifications below."
        )

    @staticmethod
    def _generate_recommendation(
        assessment: Optional[CompanyAssessment],
        justifications: Dict[str, ScoreJustification],
        risks: List[str],
    ) -> str:
        if not justifications:
            return "FURTHER DILIGENCE"
        avg_score = sum(j.score for j in justifications.values()) / len(justifications)
        n_weak = sum(1 for j in justifications.values() if j.level <= 2)
        n_high_risk = len([r for r in risks if "High" in r or "Elevated" in r])
        n_weak_evidence = sum(
            1 for j in justifications.values() if j.evidence_strength == "weak"
        )

        if avg_score >= 65 and n_weak == 0 and n_high_risk == 0 and n_weak_evidence <= 1:
            return "PROCEED"
        if avg_score >= 45 and n_weak <= 2 and n_weak_evidence <= 3:
            return "PROCEED WITH CAUTION"
        return "FURTHER DILIGENCE"