"""Assessment History Service — tracks score snapshots and trends over time."""
from __future__ import annotations

import structlog
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

from app.services.integration.cs1_client import CS1Client
from app.services.integration.cs3_client import CS3Client, DIMENSIONS

logger = structlog.get_logger(__name__)


@dataclass
class AssessmentSnapshot:
    """Point-in-time score capture."""
    company_id: str
    portfolio_id: Optional[str] = None
    org_air: Decimal = Decimal("0.0")
    vr_score: Decimal = Decimal("0.0")
    hr_score: Decimal = Decimal("0.0")
    synergy_score: Decimal = Decimal("0.0")
    dimension_scores: Dict[str, Decimal] = field(default_factory=dict)
    confidence_interval: tuple = (0.0, 0.0)
    evidence_count: int = 0
    timestamp: str = ""
    assessor_id: str = "system"
    assessment_type: str = "full"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Convert Decimal fields to float for JSON serialization
        for k in ("org_air", "vr_score", "hr_score", "synergy_score"):
            if isinstance(d.get(k), Decimal):
                d[k] = float(d[k])
        if d.get("dimension_scores"):
            d["dimension_scores"] = {
                dim: float(v) if isinstance(v, Decimal) else v
                for dim, v in d["dimension_scores"].items()
            }
        return d


@dataclass
class AssessmentTrend:
    """Computed trend from assessment history."""
    company_id: str
    current_org_air: float = 0.0
    entry_org_air: float = 0.0
    delta_since_entry: float = 0.0
    delta_30d: Optional[float] = None
    delta_90d: Optional[float] = None
    trend_direction: str = "stable"  # "improving", "stable", "declining"
    snapshot_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AssessmentHistoryService:
    """Tracks assessment history with in-memory cache."""

    _MAX_CACHE_ENTRIES = 100
    _MAX_SNAPSHOTS_PER_TICKER = 50

    def __init__(self, cs1_client: CS1Client, cs3_client: CS3Client, snapshot_repo=None):
        self.cs1 = cs1_client
        self.cs3 = cs3_client
        self._cache: Dict[str, List[AssessmentSnapshot]] = defaultdict(list)
        self._snapshot_repo = snapshot_repo

    def _get_snapshot_repo(self):
        if self._snapshot_repo is None:
            from app.repositories.assessment_snapshot_repository import AssessmentSnapshotRepository
            self._snapshot_repo = AssessmentSnapshotRepository()
        return self._snapshot_repo

    async def record_assessment(
        self,
        company_id: str,
        assessor_id: str = "system",
        assessment_type: str = "full",
        portfolio_id: Optional[str] = None,
    ) -> AssessmentSnapshot:
        """Record current scores as a snapshot."""
        ticker = company_id.upper()
        assessment = self.cs3.get_assessment(ticker)

        dim_scores: Dict[str, Decimal] = {}
        org_air = Decimal("0.0")
        vr = Decimal("0.0")
        hr = Decimal("0.0")
        synergy = Decimal("0.0")

        if assessment:
            org_air = Decimal(str(assessment.org_air_score))
            vr = Decimal(str(assessment.valuation_risk))
            hr = Decimal(str(assessment.human_capital_risk))
            synergy = Decimal(str(assessment.synergy))
            dim_scores = {
                dim: Decimal(str(ds.score))
                for dim, ds in assessment.dimension_scores.items()
            }

        snapshot = AssessmentSnapshot(
            portfolio_id=portfolio_id,
            company_id=ticker,
            org_air=org_air,
            vr_score=vr,
            hr_score=hr,
            synergy_score=synergy,
            dimension_scores=dim_scores,
            assessor_id=assessor_id,
            assessment_type=assessment_type,
        )

        await self._store_snapshot(snapshot)
        self._cache[ticker].append(snapshot)
        if len(self._cache[ticker]) > self._MAX_SNAPSHOTS_PER_TICKER:
            self._cache[ticker] = self._cache[ticker][-self._MAX_SNAPSHOTS_PER_TICKER:]
        if len(self._cache) > self._MAX_CACHE_ENTRIES:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        logger.info(
            "assessment_recorded",
            ticker=ticker,
            org_air=float(org_air),
            snapshots=len(self._cache[ticker]),
        )
        return snapshot

    async def _store_snapshot(self, snapshot: AssessmentSnapshot) -> None:
        """Persist snapshot to Snowflake (best-effort).

        If Snowflake is not reachable/configured, this becomes a no-op and the
        in-memory cache still provides basic history/trend functionality.
        """
        try:
            from datetime import datetime
            captured_at = datetime.fromisoformat(snapshot.timestamp)
            ci_lower = float(snapshot.confidence_interval[0] or 0.0)
            ci_upper = float(snapshot.confidence_interval[1] or 0.0)
            self._get_snapshot_repo().insert_snapshot(
                ticker=snapshot.company_id,
                portfolio_id=snapshot.portfolio_id,
                assessment_type=snapshot.assessment_type,
                assessor_id=snapshot.assessor_id,
                captured_at=captured_at,
                org_air=float(snapshot.org_air),
                vr_score=float(snapshot.vr_score),
                hr_score=float(snapshot.hr_score),
                synergy_score=float(snapshot.synergy_score),
                confidence_lower=ci_lower,
                confidence_upper=ci_upper,
                evidence_count=int(snapshot.evidence_count or 0),
                dimension_scores={k: float(v) for k, v in (snapshot.dimension_scores or {}).items()},
            )
        except Exception as exc:
            logger.warning("snapshot_persist_failed", company_id=snapshot.company_id, error=str(exc))

    async def get_history(
        self, company_id: str, days: int = 365, portfolio_id: Optional[str] = None
    ) -> List[AssessmentSnapshot]:
        """Retrieve snapshots for a company within the given time window."""
        ticker = company_id.upper()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Prefer Snowflake-backed history if available
        try:
            rows = self._get_snapshot_repo().list_snapshots(
                ticker=ticker,
                portfolio_id=portfolio_id,
                days=days,
            )
            out: List[AssessmentSnapshot] = []
            for r in rows:
                captured_at = r.get("captured_at")
                ts = captured_at.isoformat() if hasattr(captured_at, "isoformat") else str(captured_at)
                out.append(
                    AssessmentSnapshot(
                        portfolio_id=r.get("portfolio_id") or portfolio_id,
                        company_id=ticker,
                        org_air=Decimal(str(r.get("org_air") or 0.0)),
                        vr_score=Decimal(str(r.get("vr_score") or 0.0)),
                        hr_score=Decimal(str(r.get("hr_score") or 0.0)),
                        synergy_score=Decimal(str(r.get("synergy_score") or 0.0)),
                        dimension_scores={
                            k: Decimal(str(v))
                            for k, v in (r.get("dimension_scores") or {}).items()
                        },
                        confidence_interval=(
                            float(r.get("confidence_lower") or 0.0),
                            float(r.get("confidence_upper") or 0.0),
                        ),
                        evidence_count=int(r.get("evidence_count") or 0),
                        timestamp=ts,
                        assessor_id=r.get("assessor_id") or "system",
                        assessment_type=r.get("assessment_type") or "full",
                    )
                )
            if out:
                return out
        except Exception as exc:
            logger.warning("snapshot_db_fetch_failed", ticker=ticker, error=str(exc))

        snapshots = self._cache.get(ticker, [])
        return [s for s in snapshots if datetime.fromisoformat(s.timestamp) >= cutoff]

    async def calculate_trend(self, company_id: str, portfolio_id: Optional[str] = None) -> AssessmentTrend:
        """Compute score trend from history."""
        ticker = company_id.upper()
        history = await self.get_history(ticker, days=365, portfolio_id=portfolio_id)

        if not history:
            # Try current assessment only
            assessment = self.cs3.get_assessment(ticker)
            current = float(assessment.org_air_score) if assessment else 0.0
            return AssessmentTrend(
                company_id=ticker,
                current_org_air=current,
                entry_org_air=current,
                delta_since_entry=0.0,
                delta_30d=None,
                delta_90d=None,
                trend_direction="stable",
                snapshot_count=0,
            )

        # Sort by timestamp
        sorted_history = sorted(history, key=lambda s: s.timestamp)

        current = float(sorted_history[-1].org_air)
        entry = float(sorted_history[0].org_air)

        now = datetime.now(timezone.utc)

        # Find first snapshot >= 30 days old
        cutoff_30 = now - timedelta(days=30)
        scores_30d = [
            float(s.org_air) for s in sorted_history
            if datetime.fromisoformat(s.timestamp) >= cutoff_30
        ]

        cutoff_90 = now - timedelta(days=90)
        scores_90d = [
            float(s.org_air) for s in sorted_history
            if datetime.fromisoformat(s.timestamp) >= cutoff_90
        ]

        delta_30d = round(current - scores_30d[0], 2) if scores_30d else None
        delta_90d = round(current - scores_90d[0], 2) if scores_90d else None

        # Direction based on delta (threshold: ±5)
        delta = delta_30d if delta_30d is not None else 0.0
        if delta > 5:
            direction = "improving"
        elif delta < -5:
            direction = "declining"
        else:
            direction = "stable"

        return AssessmentTrend(
            company_id=ticker,
            current_org_air=current,
            entry_org_air=entry,
            delta_since_entry=round(current - entry, 2),
            delta_30d=delta_30d,
            delta_90d=delta_90d,
            trend_direction=direction,
            snapshot_count=len(sorted_history),
        )


def create_history_service(cs1: CS1Client, cs3: CS3Client) -> AssessmentHistoryService:
    """Factory function for AssessmentHistoryService."""
    return AssessmentHistoryService(cs1_client=cs1, cs3_client=cs3)
