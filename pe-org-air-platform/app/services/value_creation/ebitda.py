"""EBITDA Impact Projector — estimates AI readiness value creation potential."""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Sector-specific EBITDA multipliers (basis points per Org-AI-R point improvement)
SECTOR_EBITDA_MULTIPLIERS: Dict[str, float] = {
    "technology": 0.45,
    "financial_services": 0.38,
    "healthcare": 0.35,
    "manufacturing": 0.30,
    "retail": 0.28,
    "business_services": 0.32,
    "consumer": 0.25,
}

# Implementation cost factors (% of revenue per Org-AI-R point)
IMPLEMENTATION_COST_FACTOR: Dict[str, float] = {
    "technology": 0.08,
    "financial_services": 0.12,
    "healthcare": 0.15,
    "manufacturing": 0.14,
    "retail": 0.10,
    "business_services": 0.11,
    "consumer": 0.09,
}


@dataclass
class EBITDAProjection:
    """EBITDA impact projection result."""
    company_id: str
    entry_score: float
    target_score: float
    score_improvement: float
    sector: str
    ebitda_impact_pct: float
    ebitda_impact_bps: int
    implementation_cost_pct: float
    net_impact_pct: float
    hr_risk_adjustment: float
    adjusted_net_impact_pct: float
    time_to_value_months: int
    confidence: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EBITDACalculator:
    """Projects EBITDA impact from AI readiness score improvements."""

    def project(
        self,
        company_id: str,
        entry_score: float,
        target_score: float,
        h_r_score: float,
        sector: str = "technology",
        revenue_millions: float = 0.0,
    ) -> EBITDAProjection:
        """
        Project EBITDA impact from improving Org-AI-R score.

        Args:
            company_id: Company ticker
            entry_score: Current Org-AI-R score
            target_score: Target Org-AI-R score
            h_r_score: Current H^R score (human capital risk)
            sector: Company sector
            revenue_millions: Company revenue (for absolute projections)
        """
        score_improvement = max(0, target_score - entry_score)
        sector_key = sector.lower().replace(" ", "_")

        # EBITDA impact: score improvement * sector multiplier
        multiplier = SECTOR_EBITDA_MULTIPLIERS.get(sector_key, 0.30)
        ebitda_impact_pct = score_improvement * multiplier
        ebitda_impact_bps = int(ebitda_impact_pct * 100)

        # Implementation costs
        cost_factor = IMPLEMENTATION_COST_FACTOR.get(sector_key, 0.10)
        implementation_cost_pct = score_improvement * cost_factor

        # Net impact before H^R adjustment
        net_impact_pct = ebitda_impact_pct - implementation_cost_pct

        # H^R risk adjustment: high H^R reduces projected impact
        # H^R > 70 = low risk (minimal adjustment), H^R < 50 = high risk
        hr_risk_factor = min(1.0, max(0.5, h_r_score / 80.0))
        adjusted_net_impact_pct = net_impact_pct * hr_risk_factor

        # Time to value estimate (months)
        if score_improvement <= 10:
            time_to_value = 12
        elif score_improvement <= 20:
            time_to_value = 18
        elif score_improvement <= 30:
            time_to_value = 24
        else:
            time_to_value = 36

        # Confidence assessment
        if score_improvement <= 15 and h_r_score >= 60:
            confidence = "high"
        elif score_improvement <= 25:
            confidence = "medium"
        else:
            confidence = "low"

        return EBITDAProjection(
            company_id=company_id,
            entry_score=entry_score,
            target_score=target_score,
            score_improvement=round(score_improvement, 2),
            sector=sector_key,
            ebitda_impact_pct=round(ebitda_impact_pct, 2),
            ebitda_impact_bps=ebitda_impact_bps,
            implementation_cost_pct=round(implementation_cost_pct, 2),
            net_impact_pct=round(net_impact_pct, 2),
            hr_risk_adjustment=round(hr_risk_factor, 3),
            adjusted_net_impact_pct=round(adjusted_net_impact_pct, 2),
            time_to_value_months=time_to_value,
            confidence=confidence,
        )
