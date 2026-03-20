"""
API response schemas for evidence endpoints.
app/schemas/evidence.py

Domain types (DocumentSummary, SignalEvidence, GlassdoorReview, CultureSignal,
BoardMember, GovernanceSignal) remain in app/models/evidence.py.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.evidence import DocumentSummary, SignalEvidence
from app.models.signal import CompanySignalSummary


class CompanyEvidenceResponse(BaseModel):
    """Combined evidence response for a company."""
    company_id: str
    company_name: str
    ticker: str
    document_summary: DocumentSummary = Field(default_factory=DocumentSummary)
    signals: List[SignalEvidence] = []
    signal_count: int = 0
    signal_summary: Optional[CompanySignalSummary] = None


class BackfillStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BackfillResponse(BaseModel):
    """Returned immediately when a backfill is triggered."""
    task_id: str
    status: BackfillStatus
    message: str


class CompanyBackfillResult(BaseModel):
    """Result of backfill for a single company."""
    ticker: str
    status: str
    sec_result: Optional[Dict[str, Any]] = None
    signal_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class BackfillProgress(BaseModel):
    """Progress info for a backfill task."""
    companies_completed: int = 0
    total_companies: int = 0
    current_company: Optional[str] = None
    skipped_companies: List[str] = []


class BackfillTaskStatus(BaseModel):
    """Full status response for a backfill task."""
    task_id: str
    status: BackfillStatus
    progress: BackfillProgress
    company_results: List[CompanyBackfillResult] = []
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class CompanyDocumentStat(BaseModel):
    """Document counts by filing type for one company."""
    ticker: str
    form_10k: int = 0
    form_10q: int = 0
    form_8k: int = 0
    def_14a: int = 0
    total: int = 0
    chunks: int = 0
    word_count: int = 0
    last_collected: Optional[str] = None
    last_processed: Optional[str] = None


class CompanySignalStat(BaseModel):
    """Signal summary for one company (from company_signal_summaries table)."""
    ticker: str
    technology_hiring_score: Optional[float] = None
    innovation_activity_score: Optional[float] = None
    digital_presence_score: Optional[float] = None
    leadership_signals_score: Optional[float] = None
    composite_score: Optional[float] = None
    signal_count: int = 0
    last_updated: Optional[str] = None


class SignalCategoryBreakdown(BaseModel):
    """Signal count and average confidence per category."""
    category: str
    count: int = 0
    avg_score: Optional[float] = None
    avg_confidence: Optional[float] = None


class EvidenceStatsResponse(BaseModel):
    """Overall evidence collection statistics."""
    companies_tracked: int
    total_documents: int
    total_chunks: int
    total_words: int
    total_signals: int
    documents_by_status: Dict[str, int] = {}
    signals_by_category: List[SignalCategoryBreakdown] = []
    documents_by_company: List[CompanyDocumentStat] = []
    signals_by_company: List[CompanySignalStat] = []
