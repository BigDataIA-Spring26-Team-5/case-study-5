"""Portfolio scoring schemas — request/response models for CS5 portfolio endpoints.

Moved from routers/orgair_scoring.py to follow the project convention of keeping
Pydantic schemas in app/schemas/.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.services.composite_scoring_service import OrgAIRResponse

# Named constants (previously hardcoded in orgair_scoring.py)
DEFAULT_RANGE_STRATEGY = "groq"
MAX_TICKERS_FOR_RANGE_ESTIMATION = 10


class PortfolioOrgAIRResponse(BaseModel):
    status: str
    companies_scored: int
    companies_failed: int
    results: List[OrgAIRResponse]
    summary_table: List[Dict[str, Any]]
    duration_seconds: float


class ResultsGenerationResponse(BaseModel):
    status: str
    files_generated: int
    local_files: List[str]
    s3_files: List[str]
    summary: List[Dict[str, Any]]
    duration_seconds: float


class PortfolioOrgAIRRequest(BaseModel):
    tickers: Optional[List[str]] = None
    fund_id: Optional[str] = None
    company_ids: Optional[List[str]] = None
    limit: Optional[int] = None
    offset: int = 0
    prepare_if_missing: bool = True
    estimate_ranges: bool = True
    range_strategy: str = DEFAULT_RANGE_STRATEGY  # "groq" | "ci" | "none"
