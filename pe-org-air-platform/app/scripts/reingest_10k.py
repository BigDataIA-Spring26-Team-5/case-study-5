"""
Re-ingest SEC 10-K filings for a specific company.

Usage:
    python -m app.scripts.reingest_10k GE --dry-run
    python -m app.scripts.reingest_10k GE
    python -m app.scripts.reingest_10k GE NVDA JPM
"""

import sys
import json
import logging
import argparse
from uuid import uuid4
from dataclasses import asdict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def reingest_company(ticker: str, dry_run: bool = False, years_back: int = 3):
    """Re-ingest 10-K filings for a single company."""

    # ── Lazy imports to avoid circular import chains ──
    from app.pipelines.sec_edgar import SECEdgarCollector
    from app.pipelines.document_parser import DocumentParser
    from app.pipelines.chunking import SemanticChunker

    ticker = ticker.upper()
    logger.info(f"{'='*60}")
    logger.info(f"🔄 RE-INGESTING 10-K FILINGS: {ticker}")
    logger.info(f"{'='*60}")

    # Initialize components directly (no singletons that trigger __init__.py)
    collector = SECEdgarCollector()
    parser = DocumentParser()
    chunker = SemanticChunker(chunk_size=750, chunk_overlap=50, min_chunk_size=100)

    # Step 1: Download 10-K filings
    logger.info(f"\n📥 Step 1: Downloading 10-K filings from SEC EDGAR...")
    filings = list(collector.get_company_filings(
        ticker=ticker,
        filing_types=["10-K"],
        years_back=years_back,
    ))
    logger.info(f"   Found {len(filings)} 10-K filings")

    if not filings:
        logger.warning(f"   ⚠️  No 10-K filings found for {ticker}")
        return False

    total_chunks_created = 0

    for filing in filings:
        logger.info(f"\n{'─'*40}")
        logger.info(f"📄 Processing: {filing.filing_type} filed {filing.filing_date}")
        logger.info(f"   URL: {filing.primary_doc_url}")

        # Step 2: Download filing content
        content = collector.download_filing(filing)
        if not content:
            logger.error(f"   ❌ Failed to download filing")
            continue

        logger.info(f"   Downloaded {len(content):,} bytes")

        # Step 3: Parse
        doc_id = str(uuid4())
        parsed = parser.parse(
            content=content,
            document_id=doc_id,
            ticker=ticker,
            filing_type=filing.filing_type,
            filing_date=filing.filing_date,
            filename=filing.primary_document,
        )

        logger.info(f"   Parsed: {parsed.word_count:,} words, {len(parsed.sections)} sections")
        for sec_name, sec_text in parsed.sections.items():
            logger.info(f"      • {sec_name}: {len(sec_text.split()):,} words")

        if parsed.word_count < 500:
            logger.warning(f"   ⚠️  Very low word count ({parsed.word_count}), check filing format")

        # Step 4: Chunk
        chunks = chunker.chunk_document(
            document_id=doc_id,
            content=parsed.text_content,
            sections=parsed.sections,
        )
        logger.info(f"   Created {len(chunks)} chunks")

        if dry_run:
            logger.info(f"   🏜️  DRY RUN — showing first 3 chunks per section:")
            shown_sections = set()
            for chunk in chunks:
                if chunk.section not in shown_sections:
                    preview = chunk.content[:250].replace('\n', ' ')
                    logger.info(f"      [{chunk.section}] chunk {chunk.chunk_index} ({chunk.word_count}w): {preview}...")
                    shown_sections.add(chunk.section)
            total_chunks_created += len(chunks)
            continue

        # ── S3 and Snowflake via centralized factories ──
        from app.services.s3_storage import get_s3_service
        from app.repositories.base import get_snowflake_connection
        from app.core.settings import settings

        s3_service = get_s3_service()
        s3_client = s3_service.s3_client
        bucket = settings.S3_BUCKET

        conn = get_snowflake_connection()

        # Get company_id from DB
        cur = conn.cursor()
        cur.execute("SELECT id FROM companies WHERE ticker = %s", [ticker])
        row = cur.fetchone()
        if not row:
            logger.error(f"   ❌ Company {ticker} not found in Snowflake")
            cur.close()
            continue
        company_id = row[0]
        cur.close()

        # Step 5: Upload chunks to S3
        s3_key = f"sec/chunks/{ticker}/10-K/{filing.filing_date}_chunks.json"
        chunk_data = [asdict(c) for c in chunks]
        chunk_json = json.dumps(chunk_data, indent=2, default=str)

        try:
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=chunk_json.encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"   ✅ Uploaded to S3: s3://{bucket}/{s3_key}")
        except Exception as e:
            logger.error(f"   ❌ S3 upload failed: {e}")
            continue

        # Step 6: Update Snowflake metadata
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT id FROM documents
                WHERE ticker = %s AND filing_type = '10-K'
                AND filing_date = %s
            """, [ticker, filing.filing_date])
            existing = cur.fetchone()

            if existing:
                existing_doc_id = existing[0]
                logger.info(f"   Updating existing document: {existing_doc_id}")
                cur.execute("DELETE FROM document_chunks WHERE document_id = %s", [existing_doc_id])
                cur.execute("""
                    UPDATE documents SET
                        word_count = %s, chunk_count = %s, content_hash = %s,
                        status = 'chunked', processed_at = CURRENT_TIMESTAMP()
                    WHERE id = %s
                """, [parsed.word_count, len(chunks), parsed.content_hash, existing_doc_id])
                doc_id_to_use = existing_doc_id
            else:
                doc_id_to_use = doc_id
                cur.execute("""
                    INSERT INTO documents (id, company_id, ticker, filing_type, filing_date,
                        source_url, s3_key, content_hash, word_count, chunk_count, status, processed_at)
                    VALUES (%s, %s, %s, '10-K', %s, %s, %s, %s, %s, %s, 'chunked', CURRENT_TIMESTAMP())
                """, [
                    doc_id_to_use, company_id, ticker, filing.filing_date,
                    filing.primary_doc_url, s3_key, parsed.content_hash,
                    parsed.word_count, len(chunks),
                ])

            for chunk in chunks:
                chunk_id = str(uuid4())
                cur.execute("""
                    INSERT INTO document_chunks (id, document_id, chunk_index, section,
                        start_char, end_char, word_count, s3_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, [
                    chunk_id, doc_id_to_use, chunk.chunk_index, chunk.section,
                    chunk.start_char, chunk.end_char, chunk.word_count, s3_key,
                ])

            conn.commit()
            logger.info(f"   ✅ Snowflake updated: {len(chunks)} chunk records")
            total_chunks_created += len(chunks)

        except Exception as e:
            logger.error(f"   ❌ Snowflake update failed: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            cur.close()

    logger.info(f"\n{'='*60}")
    logger.info(f"✅ RE-INGESTION COMPLETE: {ticker}")
    logger.info(f"   Filings processed: {len(filings)}")
    logger.info(f"   Total chunks created: {total_chunks_created}")
    logger.info(f"{'='*60}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Re-ingest 10-K filings")
    ap.add_argument("tickers", nargs="+", help="Ticker symbols (e.g., GE NVDA)")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, don't upload")
    ap.add_argument("--years", type=int, default=3, help="Years of filings to fetch")
    args = ap.parse_args()

    results = {}
    for ticker in args.tickers:
        success = reingest_company(ticker.upper(), dry_run=args.dry_run, years_back=args.years)
        results[ticker.upper()] = "✅" if success else "❌"

    logger.info(f"\n📋 SUMMARY:")
    for t, s in results.items():
        logger.info(f"   {t}: {s}")


if __name__ == "__main__":
    main()