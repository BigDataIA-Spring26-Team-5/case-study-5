"""Fund-AI-R Calculator — portfolio-level AI readiness metrics."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional

from app.services.integration.cs3_client import CS3Client, DIMENSIONS
from app.services.composite_scoring_service import (
    COMPANY_SECTORS, MARKET_CAP_PERCENTILES, CS3_PORTFOLIO,
)

logger = logging.getLogger(__name__)

# Sector benchmarks: median Org-AI-R scores by sector (from industry research)
SECTOR_BENCHMARKS: Dict[str, float] = {
    "technology": 72.0,
    "financial_services": 58.0,
    "healthcare": 48.0,
    "manufacturing": 42.0,
    "retail": 45.0,
    "business_services": 50.0,
    "consumer": 38.0,
}


@dataclass
class CompanyMetric:
    """Per-company metrics for fund calculation."""
    ticker: str
    sector: str
    org_air_score: float
    ev_weight: float
    weighted_score: float
    sector_quartile: int  # 1=top, 4=bottom
    is_leader: bool
    is_laggard: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FundMetrics:
    """Fund-level aggregated metrics."""
    fund_id: str
    fund_air_score: float
    ev_weighted_score: float
    simple_avg_score: float
    company_count: int
    ai_leaders: int  # Org-AI-R >= 70
    ai_laggards: int  # Org-AI-R < 50
    sector_concentration_hhi: float
    sector_distribution: Dict[str, int] = field(default_factory=dict)
    quartile_distribution: Dict[int, int] = field(default_factory=dict)
    companies: List[CompanyMetric] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FundAIRCalculator:
    """Calculates Fund-AI-R and portfolio analytics."""

    def __init__(self, cs3_client: CS3Client):
        self.cs3 = cs3_client

    def calculate(
        self,
        fund_id: str = "PE-FUND-I",
        tickers: Optional[List[str]] = None,
    ) -> FundMetrics:
        """
        Calculate Fund-AI-R as EV-weighted average of portfolio Org-AI-R scores.

        Fund-AI-R = sum(ev_weight_i * org_air_i) for all companies i
        """
        tickers = tickers or list(CS3_PORTFOLIO)
        company_metrics: List[CompanyMetric] = []

        # Get scores and EV weights
        total_ev_weight = 0.0
        for ticker in tickers:
            assessment = self.cs3.get_assessment(ticker)
            org_air = assessment.org_air_score if assessment else 0.0
            sector = COMPANY_SECTORS.get(ticker, "technology")
            mcap = MARKET_CAP_PERCENTILES.get(ticker, 0.5)

            # Use market cap percentile as EV proxy weight
            ev_weight = mcap
            total_ev_weight += ev_weight

            company_metrics.append(CompanyMetric(
                ticker=ticker,
                sector=sector,
                org_air_score=org_air,
                ev_weight=ev_weight,
                weighted_score=0.0,  # calculated after normalization
                sector_quartile=self._sector_quartile(org_air, sector),
                is_leader=org_air >= 70,
                is_laggard=0 < org_air < 50,
            ))

        # Normalize EV weights and compute weighted scores
        if total_ev_weight > 0:
            for cm in company_metrics:
                cm.ev_weight = round(cm.ev_weight / total_ev_weight, 4)
                cm.weighted_score = round(cm.ev_weight * cm.org_air_score, 2)

        # Fund-AI-R: EV-weighted sum
        ev_weighted = sum(cm.weighted_score for cm in company_metrics)
        scores = [cm.org_air_score for cm in company_metrics if cm.org_air_score > 0]
        simple_avg = sum(scores) / len(scores) if scores else 0.0

        # Sector distribution and HHI
        sector_counts = Counter(cm.sector for cm in company_metrics)
        n = len(company_metrics) or 1
        hhi = sum((count / n) ** 2 for count in sector_counts.values())

        # Quartile distribution
        quartile_dist = Counter(cm.sector_quartile for cm in company_metrics)

        return FundMetrics(
            fund_id=fund_id,
            fund_air_score=round(ev_weighted, 2),
            ev_weighted_score=round(ev_weighted, 2),
            simple_avg_score=round(simple_avg, 2),
            company_count=len(company_metrics),
            ai_leaders=sum(1 for cm in company_metrics if cm.is_leader),
            ai_laggards=sum(1 for cm in company_metrics if cm.is_laggard),
            sector_concentration_hhi=round(hhi, 4),
            sector_distribution=dict(sector_counts),
            quartile_distribution=dict(quartile_dist),
            companies=company_metrics,
        )

    @staticmethod
    def _sector_quartile(score: float, sector: str) -> int:
        """Determine sector quartile based on benchmark."""
        benchmark = SECTOR_BENCHMARKS.get(sector, 50.0)
        diff = score - benchmark
        if diff >= 15:
            return 1  # Top quartile
        elif diff >= 0:
            return 2
        elif diff >= -15:
            return 3
        else:
            return 4  # Bottom quartile
