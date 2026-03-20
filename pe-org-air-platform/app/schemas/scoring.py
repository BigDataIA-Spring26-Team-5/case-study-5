"""Canonical scoring schemas for cross-service use.

Matches the shape CS5's CompanyAssessment dataclass expects.
"""

from typing import Dict, Optional, Tuple

from pydantic import BaseModel


class DimensionScoreRead(BaseModel):
    """A single dimension score in the assessment response."""

    dimension: str
    score: float
    level: int = 0
    level_name: str = ""
    confidence_interval: Tuple[float, float] = (0.0, 0.0)
    evidence_count: int = 0


class CompanyAssessmentRead(BaseModel):
    """Full company assessment — the shape CS5 MCP tool handlers expect."""

    company_id: str
    ticker: str
    org_air_score: float = 0.0
    vr_score: float = 0.0
    hr_score: float = 0.0
    synergy_score: float = 0.0
    talent_concentration: float = 0.0
    position_factor: float = 0.0
    dimension_scores: Dict[str, DimensionScoreRead] = {}
    scored_at: Optional[str] = None
    confidence_interval: Tuple[float, float] = (0.0, 0.0)
