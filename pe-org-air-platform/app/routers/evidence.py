# app/routers/evidence.py
"""
Evidence API Router
app/routers/evidence.py

Endpoints:
- GET  /api/v1/companies/{ticker}/evidence      - Get summary evidence for a company
"""

from fastapi import APIRouter, HTTPException
from typing import Dict
import logging

from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import get_document_repository
from app.repositories.signal_repository import get_signal_repository
from app.models.evidence import (
    DocumentSummary,
    CompanyEvidenceResponse,
    SignalEvidence,
    SignalSummary,
)

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
async def get_company_evidence(ticker: str):
    """Retrieve summary-level evidence (doc stats + signals) for a company."""
    ticker = ticker.upper()

    company_repo = CompanyRepository()
    company = company_repo.get_by_ticker(ticker)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company not found for ticker: {ticker}")

    company_id = str(company["id"])

    doc_repo = get_document_repository()
    signal_repo = get_signal_repository()

    # --- document summary (aggregated) ---
    documents = doc_repo.get_by_ticker(ticker)

    by_status: Dict[str, int] = {}
    by_filing_type: Dict[str, int] = {}
    total_chunks = 0
    total_words = 0
    filing_dates = []
    collected_dates = []
    processed_dates = []

    for doc in documents:
        st = doc.get("status", "unknown")
        by_status[st] = by_status.get(st, 0) + 1

        ft = doc.get("filing_type", "unknown")
        by_filing_type[ft] = by_filing_type.get(ft, 0) + 1

        total_chunks += doc.get("chunk_count") or 0
        total_words += doc.get("word_count") or 0

        if doc.get("filing_date"):
            filing_dates.append(doc["filing_date"])
        if doc.get("created_at"):
            collected_dates.append(doc["created_at"])
        if doc.get("processed_at"):
            processed_dates.append(doc["processed_at"])

    doc_summary = DocumentSummary(
        total_documents=len(documents),
        by_status=by_status,
        by_filing_type=by_filing_type,
        total_chunks=total_chunks,
        total_words=total_words,
        earliest_filing=str(min(filing_dates)) if filing_dates else None,
        latest_filing=str(max(filing_dates)) if filing_dates else None,
        last_collected=str(max(collected_dates)) if collected_dates else None,
        last_processed=str(max(processed_dates)) if processed_dates else None,
    )

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
        signal_summary = SignalSummary(
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
