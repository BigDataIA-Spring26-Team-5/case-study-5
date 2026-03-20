# app/models/evidence.py
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# Document Summary (replaces per-document listing)

class DocumentSummary(BaseModel):
    """Aggregated document stats for a company — no individual doc rows."""
    total_documents: int = 0
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_filing_type: Dict[str, int] = Field(default_factory=dict)
    total_chunks: int = 0
    total_words: int = 0
    earliest_filing: Optional[str] = None
    latest_filing: Optional[str] = None
    last_collected: Optional[str] = None
    last_processed: Optional[str] = None


# Signal Models

class SignalEvidence(BaseModel):
    """A single external signal observation."""
    id: str
    category: str
    source: str
    signal_date: Optional[datetime] = None
    raw_value: Optional[str] = None
    normalized_score: Optional[float] = None
    confidence: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


# ─── Glassdoor Culture Models ───

class GlassdoorReview(BaseModel):
    """A single Glassdoor review."""
    review_id: str
    rating: float  # 1-5 stars
    title: str
    pros: str
    cons: str
    advice_to_management: Optional[str] = None
    is_current_employee: bool = False
    job_title: str = ""
    review_date: Optional[str] = None  # date string from scrape


class CultureSignal(BaseModel):
    """Aggregated culture signal from Glassdoor."""
    company_id: str
    ticker: str

    # Component scores (0-100)
    innovation_score: float = 0.0
    data_driven_score: float = 0.0
    change_readiness_score: float = 0.0
    ai_awareness_score: float = 0.0

    # Aggregate
    overall_score: float = 0.0

    # Metadata
    review_count: int = 0
    avg_rating: float = 0.0
    current_employee_ratio: float = 0.0
    confidence: float = 0.0

    # Evidence
    positive_keywords_found: List[str] = []
    negative_keywords_found: List[str] = []
    sample_reviews: List[Dict[str, Any]] = []   # first few reviews for audit


# ─── NEW: Board Governance Models ───

class BoardMember(BaseModel):
    """A board member or executive."""
    name: str
    title: str
    committees: List[str] = []
    bio: str = ""
    is_independent: bool = False
    tenure_years: int = 0


class GovernanceSignal(BaseModel):
    """Board-derived governance signal."""
    company_id: str
    ticker: str

    # Boolean indicators
    has_tech_committee: bool = False
    has_ai_expertise: bool = False
    has_data_officer: bool = False
    has_risk_tech_oversight: bool = False
    has_ai_in_strategy: bool = False

    # Metrics
    tech_expertise_count: int = 0
    independent_ratio: float = 0.0

    # Final score
    governance_score: float = 20.0   # base score
    confidence: float = 0.0

    # Evidence
    ai_experts: List[str] = []
    relevant_committees: List[str] = []
    board_members: List[Dict[str, Any]] = []
