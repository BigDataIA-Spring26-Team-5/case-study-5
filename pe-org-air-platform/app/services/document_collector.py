import structlog
import hashlib
from typing import List, Dict, Optional
from datetime import datetime
from app.pipelines.sec_edgar import get_sec_collector, SECFiling
from app.services.s3_storage import get_s3_service
from app.repositories.document_repository import DocumentRepository
from app.repositories.company_repository import CompanyRepository
from app.models.document import (
    DocumentCollectionRequest,
    DocumentCollectionResponse,
    DocumentMetadata,
    DocumentStatus
)
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError

logger = structlog.get_logger()

class DocumentCollectorService:
    """Service to orchestrate SEC filing collection"""

    def __init__(self, company_repo=None, document_repo=None):
        self.sec_collector = get_sec_collector()
        self.s3_service = get_s3_service()
        self.doc_repo = document_repo or DocumentRepository()
        self.company_repo = company_repo or CompanyRepository()

    def collect_for_company(self, request: DocumentCollectionRequest) -> DocumentCollectionResponse:
        """
        Collect SEC filings for a single company.
        Downloads from SEC, uploads to S3, saves metadata to Snowflake.
        """
        ticker = request.ticker.upper()
        filing_types = [ft.value for ft in request.filing_types]
        years_back = request.years_back
        
        logger.info("=" * 60)
        logger.info(f"🚀 STARTING COLLECTION FOR: {ticker}")
        logger.info(f"   Filing types: {filing_types}")
        logger.info(f"   Years back: {years_back}")
        logger.info("=" * 60)
        
        # Get company from database
        company = self.company_repo.get_by_ticker(ticker)
        if not company:
            logger.error(f"❌ Company not found for ticker: {ticker}")
            raise NotFoundError("company", ticker)
        
        company_id = str(company['id'])
        company_name = company['name']
        logger.info(f"✅ Found company: {company_name} (ID: {company_id})")

        # Verify CIK is resolvable before starting collection
        cik = self.sec_collector.get_cik(ticker)
        if not cik:
            logger.error(f"❌ Could not resolve CIK for ticker: {ticker}")
            raise NotFoundError("cik", ticker)

        # Track results
        documents_found = 0
        documents_uploaded = 0
        documents_skipped = 0
        documents_failed = 0
        summary_by_type: Dict[str, int] = {}  # Count by filing type
        
        # Collect filings
        for filing in self.sec_collector.get_company_filings(ticker, filing_types, years_back):
            documents_found += 1
            logger.info("-" * 40)
            logger.info(f"📄 Processing: {filing.filing_type} | {filing.filing_date}")
            logger.info(f"   Accession: {filing.accession_number}")
            
            try:
                # Check if already exists (deduplication)
                if self.doc_repo.exists_by_filing(ticker, filing.filing_type, filing.filing_date):
                    logger.info(f"   ⏭️  SKIPPING: Already exists in database")
                    documents_skipped += 1
                    continue
                
                # Download filing
                content = self.sec_collector.download_filing(filing)
                if not content:
                    logger.error(f"   ❌ Failed to download filing")
                    documents_failed += 1
                    continue
                
                # Calculate hash for deduplication
                content_hash = hashlib.sha256(content).hexdigest()
                
                if self.doc_repo.exists_by_hash(content_hash):
                    logger.info(f"   ⏭️  SKIPPING: Duplicate content (hash match)")
                    documents_skipped += 1
                    continue
                
                # Upload to S3: sec/raw/{ticker}/{filing_type}/{date}_{accession}.html
                s3_key, _ = self.s3_service.upload_filing(
                    ticker=ticker,
                    filing_type=filing.filing_type,
                    filing_date=filing.filing_date,
                    filename=filing.primary_document,
                    content=content,
                    content_type="text/html",
                    accession_number=filing.accession_number
                )
                
                # Calculate word count (rough estimate)
                word_count = len(content.decode('utf-8', errors='ignore').split())
                
                # Save metadata to Snowflake
                doc_record = self.doc_repo.create(
                    company_id=company_id,
                    ticker=ticker,
                    filing_type=filing.filing_type,
                    filing_date=filing.filing_date,
                    source_url=filing.primary_doc_url,
                    s3_key=s3_key,
                    content_hash=content_hash,
                    word_count=word_count,
                    status="uploaded"
                )
                
                documents_uploaded += 1
                
                # Track by filing type
                filing_type_key = filing.filing_type
                summary_by_type[filing_type_key] = summary_by_type.get(filing_type_key, 0) + 1
                
                logger.info(f"   ✅ SUCCESS: Uploaded and saved!")
                
            except Exception as e:
                logger.error(f"   ❌ ERROR: {str(e)}")
                documents_failed += 1
                continue
        
        # Summary
        logger.info("=" * 60)
        logger.info(f"📊 COLLECTION COMPLETE FOR: {ticker}")
        logger.info(f"   Documents found: {documents_found}")
        logger.info(f"   Documents uploaded: {documents_uploaded}")
        logger.info(f"   Documents skipped: {documents_skipped}")
        logger.info(f"   Documents failed: {documents_failed}")
        if summary_by_type:
            logger.info(f"   By type: {summary_by_type}")
        logger.info("=" * 60)
        
        return DocumentCollectionResponse(
            ticker=ticker,
            company_id=company_id,
            company_name=company_name,
            filing_types=filing_types,
            years_back=years_back,
            documents_found=documents_found,
            documents_uploaded=documents_uploaded,
            documents_skipped=documents_skipped,
            documents_failed=documents_failed,
            summary=summary_by_type
        )

    def collect_for_all_companies(self, filing_types: List[str], years_back: int = 3) -> List[DocumentCollectionResponse]:
        """Collect filings for all 10 target companies"""
        target_tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]
        
        results = []
        for ticker in target_tickers:
            try:
                request = DocumentCollectionRequest(
                    ticker=ticker,
                    filing_types=filing_types,
                    years_back=years_back
                )
                result = self.collect_for_company(request)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to collect for {ticker}: {e}")
                continue
        
        return results


get_document_collector_service = make_singleton_factory(DocumentCollectorService)
