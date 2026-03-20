import json
import structlog
from typing import List, Dict, Optional
from dataclasses import asdict
from app.pipelines.document_parser import get_document_parser, ParsedDocument
from app.services.s3_storage import get_s3_service
from app.repositories.document_repository import DocumentRepository
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError, ExternalServiceError

logger = structlog.get_logger()


class DocumentParsingService:
    """Service to orchestrate document parsing"""

    def __init__(self, document_repo=None):
        self.parser = get_document_parser()
        self.s3_service = get_s3_service()
        self.doc_repo = document_repo or DocumentRepository()
    
    def _generate_parsed_s3_key(self, ticker: str, filing_type: str, 
                                 filing_date: str, doc_type: str) -> str:
        """
        Generate S3 key for parsed content
        Structure: sec/parsed/{ticker}/{filing_type}/{filing_date}_{doc_type}.json
        """
        clean_filing_type = filing_type.replace(" ", "")
        return f"sec/parsed/{ticker}/{clean_filing_type}/{filing_date}_{doc_type}.json"
    
    def parse_document(self, document_id: str) -> Dict:
        """Parse a single document by ID"""
        logger.info(f"📄 Parsing document: {document_id}")
        
        # Get document metadata from Snowflake
        doc = self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("document", document_id)
        
        ticker = doc['ticker']
        filing_type = doc['filing_type']
        filing_date = str(doc['filing_date'])
        s3_key = doc['s3_key']
        
        logger.info(f"  📋 {ticker} | {filing_type} | {filing_date}")
        
        # Download raw content from S3
        logger.info(f"  ⬇️  Downloading from S3: {s3_key}")
        content = self.s3_service.get_file(s3_key)
        if not content:
            raise ExternalServiceError("s3", f"Could not download file from S3: {s3_key}")
        
        logger.info(f"  ✅ Downloaded {len(content):,} bytes")
        
        # Parse the document
        parsed = self.parser.parse(
            content=content,
            document_id=document_id,
            ticker=ticker,
            filing_type=filing_type,
            filing_date=filing_date,
            filename=s3_key
        )
        
        # Upload parsed content to S3
        parsed_dict = asdict(parsed)
        
        # Save full parsed content
        full_s3_key = self._generate_parsed_s3_key(ticker, filing_type, filing_date, "full")
        logger.info(f"  📤 Uploading parsed content to: {full_s3_key}")
        self.s3_service.upload_filing(
            ticker=ticker,
            filing_type=f"parsed/{filing_type.replace(' ', '')}",
            filing_date=filing_date,
            filename="full.json",
            content=json.dumps(parsed_dict, indent=2, default=str).encode('utf-8'),
            content_type="application/json",
            accession_number=""
        )
        
        # Save tables separately if any
        if parsed.tables:
            tables_s3_key = self._generate_parsed_s3_key(ticker, filing_type, filing_date, "tables")
            logger.info(f"  📤 Uploading {len(parsed.tables)} tables to: {tables_s3_key}")
            self.s3_service.upload_filing(
                ticker=ticker,
                filing_type=f"parsed/{filing_type.replace(' ', '')}",
                filing_date=filing_date,
                filename="tables.json",
                content=json.dumps(parsed.tables, indent=2, default=str).encode('utf-8'),
                content_type="application/json",
                accession_number=""
            )
        
        # Update document in Snowflake (status + word_count)
        self.doc_repo.update_after_parsing(document_id, parsed.word_count, "parsed")
        logger.info(f"  ✅ Document parsed successfully!")
        
        return {
            "document_id": document_id,
            "ticker": ticker,
            "filing_type": filing_type,
            "filing_date": filing_date,
            "source_format": parsed.source_format,
            "word_count": parsed.word_count,
            "table_count": parsed.table_count,
            "sections_found": list(parsed.sections.keys()),
            "parse_errors": parsed.parse_errors,
            "s3_parsed_key": full_s3_key
        }
    
    def parse_by_ticker(self, ticker: str) -> Dict:
        """Parse all documents for a company"""
        ticker = ticker.upper()
        logger.info("=" * 60)
        logger.info(f"🚀 STARTING PARSING FOR: {ticker}")
        logger.info("=" * 60)
        
        # Get all documents for this ticker
        docs = self.doc_repo.get_by_ticker(ticker)
        
        if not docs:
            logger.warning(f"❌ No documents found for ticker: {ticker}")
            raise NotFoundError("documents", ticker)
        
        logger.info(f"📚 Found {len(docs)} documents to parse")
        
        parsed_count = 0
        failed_count = 0
        skipped_count = 0
        results = []
        
        for idx, doc in enumerate(docs, 1):
            doc_id = doc['id']
            status = doc.get('status', '')
            s3_key = doc.get('s3_key', '')
            
            logger.info("-" * 40)
            logger.info(f"📄 [{idx}/{len(docs)}] {doc['filing_type']} | {doc['filing_date']}")
            
            # Skip already parsed documents
            if status == 'parsed':
                logger.info(f"  ⏭️  SKIPPING: Already parsed")
                skipped_count += 1
                continue
            
            # Skip if s3_key is missing or invalid
            if not s3_key or not s3_key.startswith('sec/'):
                logger.warning(f"  ⏭️  SKIPPING: Invalid S3 key format: {s3_key}")
                skipped_count += 1
                continue
            
            try:
                result = self.parse_document(doc_id)
                results.append(result)
                parsed_count += 1
            except Exception as e:
                logger.error(f"  ❌ FAILED: {str(e)}")
                failed_count += 1
                self.doc_repo.update_status(doc_id, "failed", str(e))
        
        # Summary
        logger.info("=" * 60)
        logger.info(f"📊 PARSING COMPLETE FOR: {ticker}")
        logger.info(f"   Total documents: {len(docs)}")
        logger.info(f"   Parsed: {parsed_count}")
        logger.info(f"   Skipped (already parsed): {skipped_count}")
        logger.info(f"   Failed: {failed_count}")
        logger.info("=" * 60)
        
        return {
            "ticker": ticker,
            "total_documents": len(docs),
            "parsed": parsed_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "results": results
        }
    
    def parse_all_companies(self) -> Dict:
        """Parse documents for all 10 target companies"""
        target_tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]
        
        logger.info("=" * 60)
        logger.info("🚀 STARTING PARSING FOR ALL COMPANIES")
        logger.info(f"   Companies: {', '.join(target_tickers)}")
        logger.info("=" * 60)
        
        all_results = []
        total_parsed = 0
        total_failed = 0
        total_skipped = 0
        
        for ticker in target_tickers:
            try:
                result = self.parse_by_ticker(ticker)
                all_results.append({
                    "ticker": ticker,
                    "parsed": result["parsed"],
                    "skipped": result["skipped"],
                    "failed": result["failed"]
                })
                total_parsed += result["parsed"]
                total_failed += result["failed"]
                total_skipped += result["skipped"]
            except Exception as e:
                logger.error(f"❌ Failed to parse {ticker}: {e}")
                all_results.append({
                    "ticker": ticker,
                    "error": str(e)
                })
        
        logger.info("=" * 60)
        logger.info("📊 ALL COMPANIES PARSING COMPLETE")
        logger.info(f"   Total parsed: {total_parsed}")
        logger.info(f"   Total skipped: {total_skipped}")
        logger.info(f"   Total failed: {total_failed}")
        logger.info("=" * 60)
        
        return {
            "total_parsed": total_parsed,
            "total_skipped": total_skipped,
            "total_failed": total_failed,
            "by_company": all_results
        }


get_document_parsing_service = make_singleton_factory(DocumentParsingService)
