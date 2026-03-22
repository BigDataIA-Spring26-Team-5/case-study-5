"""Assessment History Service — tracks score snapshots and trends over time."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from app.services.integration.cs1_client import CS1Client
from app.services.integration.cs3_client import CS3Client, DIMENSIONS

logger = logging.getLogger(__name__)


@dataclass
class AssessmentSnapshot:
    """Point-in-time score capture."""
    company_id: str
    org_air_score: float
    vr_score: float
    hr_score: float
    synergy: float
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    timestamp: str = ""
    assessor_id: str = "system"
    assessment_type: str = "automated"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AssessmentTrend:
    """Computed trend from assessment history."""
    company_id: str
    current_score: float
    entry_score: float
    delta_30d: float
    delta_90d: float
    direction: str  # "improving", "stable", "declining"
    snapshot_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AssessmentHistoryService:
    """Tracks assessment history with in-memory cache."""

    def __init__(self, cs1_client: CS1Client, cs3_client: CS3Client):
        self.cs1 = cs1_client
        self.cs3 = cs3_client
        self._history: Dict[str, List[AssessmentSnapshot]] = defaultdict(list)

    def record_assessment(
        self,
        company_id: str,
        assessor_id: str = "system",
        assessment_type: str = "automated",
    ) -> AssessmentSnapshot:
        """Record current scores as a snapshot."""
        ticker = company_id.upper()
        assessment = self.cs3.get_assessment(ticker)

        dim_scores: Dict[str, float] = {}
        org_air = 0.0
        vr = 0.0
        hr = 0.0
        synergy = 0.0

        if assessment:
            org_air = assessment.org_air_score
            vr = assessment.valuation_risk
            hr = assessment.human_capital_risk
            synergy = assessment.synergy
            dim_scores = {
                dim: ds.score
                for dim, ds in assessment.dimension_scores.items()
            }

        snapshot = AssessmentSnapshot(
            company_id=ticker,
            org_air_score=org_air,
            vr_score=vr,
            hr_score=hr,
            synergy=synergy,
            dimension_scores=dim_scores,
            assessor_id=assessor_id,
            assessment_type=assessment_type,
        )

        self._history[ticker].append(snapshot)
        logger.info(
            "assessment_recorded ticker=%s org_air=%.2f snapshots=%d",
            ticker, org_air, len(self._history[ticker]),
        )
        return snapshot

    def get_history(
        self, company_id: str, days: int = 90
    ) -> List[AssessmentSnapshot]:
        """Retrieve snapshots for a company within the given time window."""
        ticker = company_id.upper()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        snapshots = self._history.get(ticker, [])
        return [
            s for s in snapshots
            if datetime.fromisoformat(s.timestamp) >= cutoff
        ]

    def calculate_trend(self, company_id: str) -> AssessmentTrend:
        """Compute score trend from history."""
        ticker = company_id.upper()
        snapshots = self._history.get(ticker, [])

        if not snapshots:
            return AssessmentTrend(
                company_id=ticker,
                current_score=0.0,
                entry_score=0.0,
                delta_30d=0.0,
                delta_90d=0.0,
                direction="stable",
                snapshot_count=0,
            )

        current = snapshots[-1].org_air_score
        entry = snapshots[0].org_air_score

        now = datetime.now(timezone.utc)
        scores_30d = [
            s.org_air_score for s in snapshots
            if datetime.fromisoformat(s.timestamp) >= now - timedelta(days=30)
        ]
        scores_90d = [
            s.org_air_score for s in snapshots
            if datetime.fromisoformat(s.timestamp) >= now - timedelta(days=90)
        ]

        delta_30d = current - scores_30d[0] if scores_30d else 0.0
        delta_90d = current - scores_90d[0] if scores_90d else 0.0

        if delta_30d > 2:
            direction = "improving"
        elif delta_30d < -2:
            direction = "declining"
        else:
            direction = "stable"

        return AssessmentTrend(
            company_id=ticker,
            current_score=current,
            entry_score=entry,
            delta_30d=round(delta_30d, 2),
            delta_90d=round(delta_90d, 2),
            direction=direction,
            snapshot_count=len(snapshots),
        )


def create_history_service(cs1: CS1Client, cs3: CS3Client) -> AssessmentHistoryService:
    """Factory function for AssessmentHistoryService."""
    return AssessmentHistoryService(cs1_client=cs1, cs3_client=cs3)
