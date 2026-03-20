import json
import structlog
from typing import List, Dict, Optional
from dataclasses import asdict
from uuid import uuid4
from app.pipelines.chunking import create_chunker, DocumentChunk
from app.services.s3_storage import get_s3_service
from app.repositories.document_repository import DocumentRepository
from app.repositories.chunk_repository import ChunkRepository
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError

logger = structlog.get_logger()


class DocumentChunkingService:
    """Service to orchestrate document chunking"""

    def __init__(self, document_repo=None, chunk_repo=None):
        self.s3_service = get_s3_service()
        self.doc_repo = document_repo or DocumentRepository()
        self.chunk_repo = chunk_repo or ChunkRepository()
    
    def _get_parsed_s3_key(self, ticker: str, filing_type: str, filing_date: str) -> str:
        """Get S3 key for parsed content"""
        clean_filing_type = filing_type.replace(" ", "")
        return f"sec/parsed/{ticker}/{clean_filing_type}/{filing_date}_full.json"
    
    def _generate_chunks_s3_key(self, ticker: str, filing_type: str, filing_date: str) -> str:
        """Generate S3 key for chunks"""
        clean_filing_type = filing_type.replace(" ", "")
        return f"sec/chunks/{ticker}/{clean_filing_type}/{filing_date}_chunks.json"
    
    def chunk_document(
        self, 
        document_id: str,
        chunk_size: int = 750,
        chunk_overlap: int = 50
    ) -> Dict:
        """Chunk a single parsed document"""
        logger.info(f"📦 Chunking document: {document_id}")
        
        # Get document metadata
        doc = self.doc_repo.get_by_id(document_id)
        if not doc:
            raise NotFoundError("document", document_id)
        
        ticker = doc['ticker']
        filing_type = doc['filing_type']
        filing_date = str(doc['filing_date'])
        status = doc.get('status', '')
        
        logger.info(f"  📋 {ticker} | {filing_type} | {filing_date}")
        
        # Check if already chunked
        if status == 'chunked':
            logger.info(f"  ⏭️  Already chunked, skipping")
            return {"document_id": document_id, "status": "skipped", "reason": "already chunked"}
        
        # Get parsed content from S3
        parsed_s3_key = self._get_parsed_s3_key(ticker, filing_type, filing_date)
        logger.info(f"  ⬇️  Downloading parsed content: {parsed_s3_key}")
        
        parsed_content = self.s3_service.get_file(parsed_s3_key)
        if not parsed_content:
            raise NotFoundError("parsed_content", parsed_s3_key)
        
        parsed_data = json.loads(parsed_content.decode('utf-8'))
        text_content = parsed_data.get('text_content', '')
        sections = parsed_data.get('sections', {})
        
        logger.info(f"  ✅ Loaded {len(text_content):,} chars, {len(sections)} sections")
        
        # Create chunker and chunk the document
        chunker = create_chunker(chunk_size, chunk_overlap)
        chunks = chunker.chunk_document(document_id, text_content, sections)
        
        if not chunks:
            logger.warning(f"  ⚠️  No chunks created")
            return {"document_id": document_id, "status": "error", "reason": "no chunks created"}
        
        # Save chunks to S3
        chunks_s3_key = self._generate_chunks_s3_key(ticker, filing_type, filing_date)
        chunks_data = [asdict(c) for c in chunks]
        
        logger.info(f"  📤 Uploading {len(chunks)} chunks to S3: {chunks_s3_key}")
        self.s3_service.s3_client.put_object(
            Bucket=self.s3_service.bucket_name,
            Key=chunks_s3_key,
            Body=json.dumps(chunks_data, indent=2).encode('utf-8'),
            ContentType="application/json",
            Metadata={
                'ticker': ticker,
                'filing_type': filing_type,
                'chunk_count': str(len(chunks))
            }
        )
        
        # Save chunk METADATA to Snowflake (BATCH INSERT - much faster)
        logger.info(f"  💾 Batch inserting {len(chunks)} chunk metadata to Snowflake...")
        self.chunk_repo.create_batch(document_id, chunks, chunks_s3_key)
        
        # Update document status and chunk count
        self.doc_repo.update_status(document_id, "chunked")
        self.doc_repo.update_chunk_count(document_id, len(chunks))
        
        logger.info(f"  ✅ Document chunked successfully!")
        
        return {
            "document_id": document_id,
            "ticker": ticker,
            "filing_type": filing_type,
            "filing_date": filing_date,
            "chunk_count": len(chunks),
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "s3_chunks_key": chunks_s3_key,
            "status": "chunked"
        }
    
    def chunk_by_ticker(
        self, 
        ticker: str,
        chunk_size: int = 750,
        chunk_overlap: int = 50
    ) -> Dict:
        """Chunk all parsed documents for a company"""
        ticker = ticker.upper()
        logger.info("=" * 60)
        logger.info(f"🚀 STARTING CHUNKING FOR: {ticker}")
        logger.info(f"   Chunk size: {chunk_size}, Overlap: {chunk_overlap}")
        logger.info("=" * 60)
        
        # Get all parsed documents for this ticker
        docs = self.doc_repo.get_by_ticker(ticker)
        parsed_docs = [d for d in docs if d.get('status') == 'parsed']
        
        if not parsed_docs:
            logger.warning(f"❌ No parsed documents found for: {ticker}")
            raise NotFoundError("parsed_documents", ticker)
        
        logger.info(f"📚 Found {len(parsed_docs)} parsed documents to chunk")
        
        chunked_count = 0
        failed_count = 0
        skipped_count = 0
        total_chunks = 0
        results = []
        
        for idx, doc in enumerate(parsed_docs, 1):
            doc_id = doc['id']
            
            logger.info("-" * 40)
            logger.info(f"📦 [{idx}/{len(parsed_docs)}] {doc['filing_type']} | {doc['filing_date']}")
            
            try:
                result = self.chunk_document(doc_id, chunk_size, chunk_overlap)
                if result.get('status') == 'skipped':
                    skipped_count += 1
                else:
                    chunked_count += 1
                    total_chunks += result.get('chunk_count', 0)
                results.append(result)
            except Exception as e:
                logger.error(f"  ❌ FAILED: {str(e)}")
                failed_count += 1
                self.doc_repo.update_status(doc_id, "failed", str(e))
        
        logger.info("=" * 60)
        logger.info(f"📊 CHUNKING COMPLETE FOR: {ticker}")
        logger.info(f"   Documents chunked: {chunked_count}")
        logger.info(f"   Documents skipped: {skipped_count}")
        logger.info(f"   Documents failed: {failed_count}")
        logger.info(f"   Total chunks created: {total_chunks}")
        logger.info("=" * 60)
        
        return {
            "ticker": ticker,
            "total_documents": len(parsed_docs),
            "chunked": chunked_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap
        }
    
    def chunk_all_companies(
        self,
        chunk_size: int = 750,
        chunk_overlap: int = 50
    ) -> Dict:
        """Chunk documents for all companies"""
        target_tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]
        
        logger.info("=" * 60)
        logger.info("🚀 STARTING CHUNKING FOR ALL COMPANIES")
        logger.info(f"   Chunk size: {chunk_size}, Overlap: {chunk_overlap}")
        logger.info("=" * 60)
        
        all_results = []
        total_chunked = 0
        total_chunks = 0
        
        for ticker in target_tickers:
            try:
                result = self.chunk_by_ticker(ticker, chunk_size, chunk_overlap)
                all_results.append(result)
                total_chunked += result["chunked"]
                total_chunks += result["total_chunks"]
            except Exception as e:
                logger.error(f"❌ Failed to chunk {ticker}: {e}")
                all_results.append({"ticker": ticker, "error": str(e)})
        
        logger.info("=" * 60)
        logger.info("📊 ALL COMPANIES CHUNKING COMPLETE")
        logger.info(f"   Total documents chunked: {total_chunked}")
        logger.info(f"   Total chunks created: {total_chunks}")
        logger.info("=" * 60)
        
        return {
            "total_documents_chunked": total_chunked,
            "total_chunks_created": total_chunks,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "by_company": all_results
        }


get_document_chunking_service = make_singleton_factory(DocumentChunkingService)
