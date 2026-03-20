#!/usr/bin/env python
"""
Collect evidence for all target companies.

Runs the full pipeline sequentially per company:
  1. SEC document collection (download → S3 → Snowflake)
  2. Document parsing (raw HTML → parsed JSON on S3)
  3. Document chunking (parsed JSON → semantic chunks on S3 + Snowflake)
  4. Signal collection (jobs, patents, tech stack, leadership)

Companies are processed one-at-a-time to respect API rate limits.
SEC + signal pipelines run in parallel within each company.

Usage:
    python -m app.Scripts.collect_evidence --companies all
    python -m app.Scripts.collect_evidence --companies CAT,DE,UNH
    python -m app.Scripts.collect_evidence --companies CAT --skip-signals
    python -m app.Scripts.collect_evidence --companies all --skip-documents
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List

from app.services.document_collector import get_document_collector_service
from app.services.document_parsing_service import get_document_parsing_service
from app.services.document_chunking_service import get_document_chunking_service
from app.services.signals.job_signal_service import get_job_signal_service
from app.services.signals.patent_signal_service import get_patent_signal_service
from app.services.signals.tech_signal_service import get_tech_signal_service
from app.services.signals.leadership_service import get_leadership_service
from app.repositories.company_repository import CompanyRepository
from app.models.document import DocumentCollectionRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

TARGET_COMPANIES = {
    "CAT": {"name": "Caterpillar Inc.", "sector": "Manufacturing"},
    "DE": {"name": "Deere & Company", "sector": "Manufacturing"},
    "UNH": {"name": "UnitedHealth Group", "sector": "Healthcare"},
    "HCA": {"name": "HCA Healthcare", "sector": "Healthcare"},
    "ADP": {"name": "Automatic Data Processing", "sector": "Services"},
    "PAYX": {"name": "Paychex Inc.", "sector": "Services"},
    "WMT": {"name": "Walmart Inc.", "sector": "Retail"},
    "TGT": {"name": "Target Corporation", "sector": "Retail"},
    "JPM": {"name": "JPMorgan Chase", "sector": "Financial"},
    "GS": {"name": "Goldman Sachs", "sector": "Financial"},
}


def collect_documents(ticker: str) -> Dict:
    """
    Collect SEC documents for a company.
    Pipeline: SEC EDGAR → S3 upload → Snowflake metadata.
    """
    logger.info(f"{'='*60}")
    logger.info(f"DOCUMENTS: Collecting SEC filings for {ticker}")
    logger.info(f"{'='*60}")

    service = get_document_collector_service()
    request = DocumentCollectionRequest(ticker=ticker)
    result = service.collect_for_company(request)

    return {
        "documents_found": result.documents_found,
        "documents_uploaded": result.documents_uploaded,
        "documents_skipped": result.documents_skipped,
        "documents_failed": result.documents_failed,
        "summary": result.summary,
    }


def parse_documents(ticker: str) -> Dict:
    """
    Parse all uploaded documents for a company.
    Pipeline: S3 raw HTML → parse → S3 parsed JSON.
    """
    logger.info(f"{'='*60}")
    logger.info(f"PARSING: Parsing documents for {ticker}")
    logger.info(f"{'='*60}")

    service = get_document_parsing_service()
    try:
        result = service.parse_by_ticker(ticker)
    except ValueError as e:
        logger.warning(f"Parsing skipped for {ticker}: {e}")
        return {"ticker": ticker, "parsed": 0, "skipped": 0, "failed": 0}

    return {
        "ticker": result["ticker"],
        "total_documents": result["total_documents"],
        "parsed": result["parsed"],
        "skipped": result["skipped"],
        "failed": result["failed"],
    }


def chunk_documents(ticker: str) -> Dict:
    """
    Chunk all parsed documents for a company.
    Pipeline: S3 parsed JSON → semantic chunks → S3 + Snowflake.
    """
    logger.info(f"{'='*60}")
    logger.info(f"CHUNKING: Chunking documents for {ticker}")
    logger.info(f"{'='*60}")

    service = get_document_chunking_service()
    try:
        result = service.chunk_by_ticker(ticker)
    except ValueError as e:
        logger.warning(f"Chunking skipped for {ticker}: {e}")
        return {"ticker": ticker, "chunked": 0, "total_chunks": 0, "failed": 0}

    return {
        "ticker": result["ticker"],
        "total_documents": result["total_documents"],
        "chunked": result["chunked"],
        "skipped": result["skipped"],
        "failed": result["failed"],
        "total_chunks": result["total_chunks"],
    }


async def collect_signals(ticker: str) -> Dict:
    """
    Collect all 4 signal categories for a company.
    Categories: technology_hiring, innovation_activity, digital_presence, leadership_signals.
    """
    logger.info(f"{'='*60}")
    logger.info(f"SIGNALS: Collecting signals for {ticker}")
    logger.info(f"{'='*60}")

    results = {}
    errors = []

    categories = [
        ("technology_hiring", lambda: get_job_signal_service().analyze_company(ticker, force_refresh=True)),
        ("innovation_activity", lambda: get_patent_signal_service().analyze_company(ticker, years_back=5)),
        ("digital_presence", lambda: get_tech_signal_service().analyze_company(ticker, force_refresh=True)),
        ("leadership_signals", lambda: get_leadership_service().analyze_company(ticker)),
    ]

    for category, service_call in categories:
        try:
            result = await service_call()
            score = result.get("normalized_score") if isinstance(result, dict) else None
            results[category] = {"status": "success", "score": score}
            logger.info(f"  {category}: score={score}")
        except Exception as e:
            logger.error(f"  {category}: FAILED - {e}")
            results[category] = {"status": "failed", "error": str(e)}
            errors.append(f"{category}: {str(e)}")

    return {"signals": results, "errors": errors}


async def process_company(
    ticker: str,
    skip_documents: bool = False,
    skip_signals: bool = False,
) -> Dict:
    """
    Run the full evidence collection pipeline for one company.

    SEC pipeline (sync) and signal pipeline (async) run in parallel.
    Parsing and chunking run after SEC collection completes.
    """
    company_result = {
        "ticker": ticker,
        "status": "success",
        "documents": None,
        "parsing": None,
        "chunking": None,
        "signals": None,
        "error": None,
    }

    try:
        if skip_documents and skip_signals:
            logger.warning(f"Both --skip-documents and --skip-signals set for {ticker}, nothing to do")
            return company_result

        # Run SEC collection (sync, in thread) and signals (async) in parallel
        tasks = []

        if not skip_documents:
            async def _run_document_pipeline():
                # Step 1: Collect
                doc_result = await asyncio.to_thread(collect_documents, ticker)
                company_result["documents"] = doc_result

                # Step 2: Parse
                parse_result = await asyncio.to_thread(parse_documents, ticker)
                company_result["parsing"] = parse_result

                # Step 3: Chunk
                chunk_result = await asyncio.to_thread(chunk_documents, ticker)
                company_result["chunking"] = chunk_result

            tasks.append(_run_document_pipeline())

        if not skip_signals:
            async def _run_signal_pipeline():
                signal_result = await collect_signals(ticker)
                company_result["signals"] = signal_result
                if signal_result.get("errors"):
                    company_result["status"] = "completed_with_errors"

            tasks.append(_run_signal_pipeline())

        await asyncio.gather(*tasks, return_exceptions=False)

    except Exception as e:
        logger.error(f"FAILED processing {ticker}: {e}")
        company_result["status"] = "failed"
        company_result["error"] = str(e)

    return company_result


async def main(
    companies: List[str],
    skip_documents: bool = False,
    skip_signals: bool = False,
) -> Dict:
    """Main collection routine. Processes companies sequentially."""
    start_time = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("EVIDENCE COLLECTION STARTED")
    logger.info(f"  Companies: {', '.join(companies)}")
    logger.info(f"  Skip documents: {skip_documents}")
    logger.info(f"  Skip signals:   {skip_signals}")
    logger.info(f"  Started at:     {start_time.isoformat()}")
    logger.info("=" * 60)

    stats = {
        "companies_processed": 0,
        "companies_failed": 0,
        "total_documents_uploaded": 0,
        "total_documents_parsed": 0,
        "total_chunks_created": 0,
        "total_signals_collected": 0,
        "total_signal_errors": 0,
        "company_results": [],
    }

    # Verify tickers exist in database
    company_repo = CompanyRepository()

    for ticker in companies:
        if ticker not in TARGET_COMPANIES:
            logger.warning(f"Unknown ticker: {ticker} — skipping")
            continue

        company = company_repo.get_by_ticker(ticker)
        if not company:
            logger.error(f"Company not found in database: {ticker} — skipping")
            stats["companies_failed"] += 1
            continue

        logger.info("")
        logger.info(f"{'#'*60}")
        logger.info(f"  [{stats['companies_processed']+1}/{len(companies)}] "
                     f"Processing: {ticker} ({company.get('name', '')})")
        logger.info(f"{'#'*60}")

        result = await process_company(ticker, skip_documents, skip_signals)
        stats["company_results"].append(result)

        if result["status"] == "failed":
            stats["companies_failed"] += 1
        else:
            stats["companies_processed"] += 1

        # Accumulate totals
        if result.get("documents"):
            stats["total_documents_uploaded"] += result["documents"].get("documents_uploaded", 0)
        if result.get("parsing"):
            stats["total_documents_parsed"] += result["parsing"].get("parsed", 0)
        if result.get("chunking"):
            stats["total_chunks_created"] += result["chunking"].get("total_chunks", 0)
        if result.get("signals"):
            signals = result["signals"].get("signals", {})
            stats["total_signals_collected"] += sum(
                1 for s in signals.values() if s.get("status") == "success"
            )
            stats["total_signal_errors"] += len(result["signals"].get("errors", []))

    end_time = datetime.now(timezone.utc)
    elapsed = (end_time - start_time).total_seconds()

    logger.info("")
    logger.info("=" * 60)
    logger.info("EVIDENCE COLLECTION COMPLETE")
    logger.info(f"  Duration:             {elapsed:.1f}s")
    logger.info(f"  Companies processed:  {stats['companies_processed']}")
    logger.info(f"  Companies failed:     {stats['companies_failed']}")
    logger.info(f"  Documents uploaded:   {stats['total_documents_uploaded']}")
    logger.info(f"  Documents parsed:     {stats['total_documents_parsed']}")
    logger.info(f"  Chunks created:       {stats['total_chunks_created']}")
    logger.info(f"  Signals collected:    {stats['total_signals_collected']}")
    logger.info(f"  Signal errors:        {stats['total_signal_errors']}")
    logger.info("=" * 60)

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collect evidence for target companies"
    )
    parser.add_argument(
        "--companies",
        default="all",
        help="Comma-separated tickers or 'all' (default: all)",
    )
    parser.add_argument(
        "--skip-documents",
        action="store_true",
        help="Skip SEC document collection, parsing, and chunking",
    )
    parser.add_argument(
        "--skip-signals",
        action="store_true",
        help="Skip signal collection (jobs, patents, tech, leadership)",
    )
    args = parser.parse_args()

    if args.companies == "all":
        companies = list(TARGET_COMPANIES.keys())
    else:
        companies = [t.strip().upper() for t in args.companies.split(",")]

    asyncio.run(main(companies, args.skip_documents, args.skip_signals))
