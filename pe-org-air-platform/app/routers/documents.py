from fastapi import APIRouter, Depends, Query
from typing import List
import logging
from app.models.document import (
    DocumentCollectionRequest,
    DocumentCollectionResponse,
    FilingType,
    ParseByTickerResponse,
    ParseAllResponse,
)
from app.core.dependencies import (
    get_document_collector_service,
    get_document_parsing_service,
    get_document_chunking_service,
)
from app.core.errors import PlatformError, ExternalServiceError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/documents",
)



# SECTION 1: DOCUMENT COLLECTION


@router.post(
    "/collect",
    response_model=DocumentCollectionResponse,
    tags=["1. Collection"],
    summary="Collect SEC filings for a company",
    description="""
    Download SEC filings for a single company.

    **Process:**
    1. Downloads filings from SEC EDGAR (with rate limiting)
    2. Uploads raw documents to S3 (sec/raw/{ticker}/...)
    3. Saves metadata to Snowflake
    4. Deduplicates based on content hash

    **Filing Types:** 10-K, 10-Q, 8-K, DEF 14A
    """
)
async def collect_documents(
    request: DocumentCollectionRequest,
    service=Depends(get_document_collector_service),
):
    """Collect SEC filings for a company"""
    logger.info(f"Collection request for: {request.ticker}")
    try:
        return service.collect_for_company(request)
    except PlatformError:
        raise
    except Exception as e:
        logger.error(f"Collection failed: {e}")
        raise ExternalServiceError("document_collection", "Failed to collect SEC filings.")


@router.post(
    "/collect/all",
    response_model=List[DocumentCollectionResponse],
    tags=["1. Collection"],
    summary="Collect SEC filings for all 10 companies"
)
async def collect_all_documents(
    filing_types: List[FilingType] = Query(
        default=[FilingType.FORM_10K, FilingType.FORM_10Q, FilingType.FORM_8K, FilingType.DEF_14A]
    ),
    years_back: int = Query(default=3, ge=1, le=10),
    service=Depends(get_document_collector_service),
):
    """Collect documents for all 10 target companies"""
    logger.info("Batch collection for all companies")
    try:
        return service.collect_for_all_companies([ft.value for ft in filing_types], years_back)
    except Exception as e:
        logger.error(f"Batch collection failed: {e}")
        raise ExternalServiceError("document_collection", "Failed to collect SEC filings for all companies.")



# SECTION 2: DOCUMENT PARSING


@router.post(
    "/parse/{ticker}",
    response_model=ParseByTickerResponse,
    tags=["2. Parsing"],
    summary="Parse all documents for a company",
    description="""
    Parse all collected SEC filings for a company.

    **Process:**
    1. Downloads raw documents from S3
    2. Extracts text and tables (HTML/PDF)
    3. Identifies key sections (Risk Factors, MD&A, etc.)
    4. Uploads parsed JSON to S3 (sec/parsed/{ticker}/...)
    5. Updates word_count in Snowflake
    """
)
async def parse_documents_by_ticker(
    ticker: str,
    service=Depends(get_document_parsing_service),
):
    """Parse all documents for a company"""
    logger.info(f"Parse request for: {ticker}")
    try:
        return service.parse_by_ticker(ticker)
    except PlatformError:
        raise
    except Exception as e:
        logger.error(f"Parse failed for {ticker}: {e}")
        raise ExternalServiceError("document_parsing", "Failed to parse documents.")


@router.post(
    "/parse",
    response_model=ParseAllResponse,
    tags=["2. Parsing"],
    summary="Parse documents for all companies"
)
async def parse_all_documents(
    service=Depends(get_document_parsing_service),
):
    """Parse documents for all 10 target companies"""
    logger.info("Batch parsing for all companies")
    try:
        return service.parse_all_companies()
    except Exception as e:
        logger.error(f"Batch parsing failed: {e}")
        raise ExternalServiceError("document_parsing", "Failed to parse documents for all companies.")



# SECTION 3: DOCUMENT CHUNKING


@router.post(
    "/chunk/{ticker}",
    tags=["3. Chunking"],
    summary="Chunk all parsed documents for a company",
    description="""
    Split parsed documents into smaller chunks for LLM processing.

    **Process:**
    1. Downloads parsed content from S3
    2. Splits into overlapping chunks (preserves context)
    3. Uploads chunks to S3 (sec/chunks/{ticker}/...)
    4. Saves chunk metadata to Snowflake

    **Parameters:**
    - chunk_size: Target words per chunk (default: 750)
    - chunk_overlap: Overlap between chunks (default: 50)
    """
)
async def chunk_documents_by_ticker(
    ticker: str,
    chunk_size: int = Query(default=750, ge=100, le=2000, description="Words per chunk"),
    chunk_overlap: int = Query(default=50, ge=0, le=200, description="Overlap between chunks"),
    service=Depends(get_document_chunking_service),
):
    """Chunk all parsed documents for a company"""
    logger.info(f"Chunk request for: {ticker}")
    try:
        return service.chunk_by_ticker(ticker, chunk_size, chunk_overlap)
    except PlatformError:
        raise
    except Exception as e:
        logger.error(f"Chunk failed for {ticker}: {e}")
        raise ExternalServiceError("document_chunking", "Failed to chunk documents.")


@router.post(
    "/chunk",
    tags=["3. Chunking"],
    summary="Chunk documents for all companies"
)
async def chunk_all_documents(
    chunk_size: int = Query(default=750, ge=100, le=2000),
    chunk_overlap: int = Query(default=50, ge=0, le=200),
    service=Depends(get_document_chunking_service),
):
    """Chunk documents for all 10 target companies"""
    logger.info("Batch chunking for all companies")
    try:
        return service.chunk_all_companies(chunk_size, chunk_overlap)
    except Exception as e:
        logger.error(f"Batch chunking failed: {e}")
        raise ExternalServiceError("document_chunking", "Failed to chunk documents for all companies.")
