# app/services/evidence_service.py
"""Evidence aggregation helpers extracted from the evidence router."""

from typing import Dict, List, Optional

from app.models.evidence import DocumentSummary


def build_document_summary(documents: List[dict]) -> DocumentSummary:
    """Aggregate a list of document dicts into a DocumentSummary."""
    by_status: Dict[str, int] = {}
    by_filing_type: Dict[str, int] = {}
    total_chunks = 0
    total_words = 0
    filing_dates: List = []
    collected_dates: List = []
    processed_dates: List = []

    for doc in documents:
        st = doc.get("status", "unknown")
        by_status[st] = by_status.get(st, 0) + 1

        ft = doc.get("filing_type", "unknown")
        by_filing_type[ft] = by_filing_type.get(ft, 0) + 1

        total_chunks += doc.get("chunk_count") or 0
        total_words += doc.get("word_count") or 0

        if doc.get("filing_date"):
            filing_dates.append(str(doc["filing_date"]))
        if doc.get("created_at"):
            collected_dates.append(str(doc["created_at"]))
        if doc.get("processed_at"):
            processed_dates.append(str(doc["processed_at"]))

    return DocumentSummary(
        total_documents=len(documents),
        by_status=by_status,
        by_filing_type=by_filing_type,
        total_chunks=total_chunks,
        total_words=total_words,
        earliest_filing=min(filing_dates) if filing_dates else None,
        latest_filing=max(filing_dates) if filing_dates else None,
        last_collected=max(collected_dates) if collected_dates else None,
        last_processed=max(processed_dates) if processed_dates else None,
    )
