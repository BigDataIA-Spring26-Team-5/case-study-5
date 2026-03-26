"""Investment Tracker — tracks portfolio entry prices and projects AI-driven ROI.

Maps Org-AI-R score improvement to revenue lift, EBITDA improvement,
and exit multiple expansion using simplified DCF proxy formulas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class InvestmentROI:
    """ROI projection for a single portfolio company."""
    ticker: str
    entry_price: Optional[float]
    entry_org_air: float
    current_org_air: float
    air_improvement: float
    projected_revenue_lift_pct: float    # air_improvement * 0.8%
    projected_ebitda_lift_pct: float     # air_improvement * 0.3%
    projected_exit_multiple_expansion: float  # air_improvement * 0.05x
    roi_estimate_pct: float              # simplified DCF proxy
    assessment_date: datetime

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["assessment_date"] = d["assessment_date"].isoformat()
        return d


class InvestmentTracker:
    """Maps Org-AI-R score improvement to ROI projections.

    Formulas:
        revenue_lift_pct = air_improvement * 0.8
        ebitda_lift_pct  = air_improvement * 0.3
        multiple_expansion = air_improvement * 0.05x
        roi_estimate_pct = (revenue_lift * 2.5) + (ebitda_lift * 8.0)
    """

    def compute_roi(
        self,
        ticker: str,
        current_org_air: float,
        entry_org_air: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> InvestmentROI:
        """Compute ROI projection for a single company.

        Args:
            ticker: Portfolio company ticker
            current_org_air: Latest Org-AI-R score
            entry_org_air: Score at acquisition/entry (if omitted, uses current_org_air so improvement=0)
            entry_price: Optional entry price (not required for ROI estimate)
        """
        ticker = (ticker or "").upper().strip()
        baseline = float(entry_org_air) if entry_org_air is not None else float(current_org_air or 0.0)
        improvement = float(current_org_air or 0.0) - baseline
        revenue_lift = improvement * 0.8
        ebitda_lift = improvement * 0.3
        multiple_expansion = improvement * 0.05
        roi = (revenue_lift * 2.5) + (ebitda_lift * 8.0)  # simplified DCF proxy

        logger.debug(
            "ROI computed for %s: org_air %.1f → %.1f, roi=%.1f%%",
            ticker, entry_org_air, current_org_air, roi,
        )

        return InvestmentROI(
            ticker=ticker,
            entry_price=float(entry_price) if entry_price is not None else None,
            entry_org_air=round(baseline, 2),
            current_org_air=round(current_org_air, 2),
            air_improvement=round(improvement, 2),
            projected_revenue_lift_pct=round(revenue_lift, 2),
            projected_ebitda_lift_pct=round(ebitda_lift, 2),
            projected_exit_multiple_expansion=round(multiple_expansion, 3),
            roi_estimate_pct=round(roi, 2),
            assessment_date=datetime.utcnow(),
        )

    def portfolio_roi_summary(
        self, scores: Dict[str, float]
    ) -> Dict[str, Any]:
        """Compute ROI for all companies and return portfolio summary.

        Args:
            scores: Dict[ticker, current_org_air]
        """
        results: Dict[str, InvestmentROI] = {ticker: self.compute_roi(ticker, score) for ticker, score in scores.items()}
        avg_roi = (
            sum(r.roi_estimate_pct for r in results.values()) / len(results)
            if results else 0.0
        )
        top = max(results, key=lambda t: results[t].roi_estimate_pct) if results else None

        return {
            "companies": {t: r.to_dict() for t, r in results.items()},
            "portfolio_avg_roi_pct": round(avg_roi, 2),
            "top_performer": top,
        }


# Module-level singleton
investment_tracker = InvestmentTracker()
