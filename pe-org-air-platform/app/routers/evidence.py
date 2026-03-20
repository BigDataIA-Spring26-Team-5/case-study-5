# app/routers/evidence.py
"""
Evidence API Router
app/routers/evidence.py

Endpoints:
- GET  /api/v1/companies/{ticker}/evidence      - Get summary evidence for a company
"""

from fastapi import APIRouter, Depends
from typing import Dict
import logging

from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.signal_repository import SignalRepository
from app.core.dependencies import (
    get_company_repository,
    get_document_repository,
    get_signal_repository,
)
from app.routers.common import get_company_or_404
from app.services.signals.evidence_service import build_document_summary
from app.models.evidence import DocumentSummary, SignalEvidence
from app.schemas.evidence import CompanyEvidenceResponse
from app.models.signal import CompanySignalSummary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Evidence"])


# GET /api/v1/companies/{ticker}/evidence


@router.get(
    "/companies/{ticker}/evidence",
    response_model=CompanyEvidenceResponse,
    summary="Get summary evidence for a company",
    description=(
        "Returns aggregated SEC filing statistics (counts by type, status, "
        "word/chunk totals, filing date range, freshness) and external signals "
        "for the given ticker. No individual document rows are returned."
    ),
)
async def get_company_evidence(
    ticker: str,
    company_repo: CompanyRepository = Depends(get_company_repository),
    doc_repo: DocumentRepository = Depends(get_document_repository),
    signal_repo: SignalRepository = Depends(get_signal_repository),
):
    """Retrieve summary-level evidence (doc stats + signals) for a company."""
    ticker = ticker.upper()
    company = get_company_or_404(ticker, company_repo)
    company_id = str(company["id"])

    # --- document summary (aggregated) ---
    documents = doc_repo.get_by_ticker(ticker)
    doc_summary = build_document_summary(documents)

    # --- signals ---
    signals = signal_repo.get_signals_by_ticker(ticker)
    summary = signal_repo.get_summary_by_ticker(ticker)

    signal_evidence = [
        SignalEvidence(
            id=sig["id"],
            category=sig.get("category", ""),
            source=sig.get("source", ""),
            signal_date=sig.get("signal_date"),
            raw_value=sig.get("raw_value"),
            normalized_score=sig.get("normalized_score"),
            confidence=sig.get("confidence"),
            metadata=sig.get("metadata"),
            created_at=sig.get("created_at"),
        )
        for sig in signals
    ]

    signal_summary = None
    if summary:
        signal_summary = CompanySignalSummary(
            company_id=company_id,
            ticker=ticker,
            technology_hiring_score=summary.get("technology_hiring_score"),
            innovation_activity_score=summary.get("innovation_activity_score"),
            digital_presence_score=summary.get("digital_presence_score"),
            leadership_signals_score=summary.get("leadership_signals_score"),
            composite_score=summary.get("composite_score"),
            signal_count=summary.get("signal_count", 0),
            last_updated=summary.get("last_updated"),
        )

    return CompanyEvidenceResponse(
        company_id=company_id,
        company_name=company.get("name", ""),
        ticker=ticker,
        document_summary=doc_summary,
        signals=signal_evidence,
        signal_count=len(signal_evidence),
        signal_summary=signal_summary,
    )
