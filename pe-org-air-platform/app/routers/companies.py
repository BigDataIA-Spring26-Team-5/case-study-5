"""
Company Router - PE Org-AI-R Platform
app/routers/companies.py

Handles company CRUD operations with Redis caching.
"""

import structlog
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.dependencies import get_company_repository, get_industry_repository
from app.core.errors import NotFoundError, ConflictError
from app.repositories.company_repository import CompanyRepository
from app.repositories.industry_repository import IndustryRepository
from app.services.cache import (
    CacheInfo,
    TTL_COMPANY,
    cached_query,
    create_cache_info,
    get_cache,
)
from app.services.groq_enrichment import enrich_company_metadata, enrich_portfolio_metadata, get_dimension_keywords

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["Companies"])



#  Schemas


class CompanyBase(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    ticker: Optional[str] = Field(None, max_length=10)
    industry_id: Optional[UUID] = None
    position_factor: float = Field(default=0.0, ge=-1.0, le=1.0)

    @model_validator(mode='before')
    @classmethod
    def uppercase_ticker(cls, values):
        if 'ticker' in values and values['ticker']:
            values['ticker'] = values['ticker'].upper()
        return values


class CompanyCreate(CompanyBase):
    name: str = Field(..., min_length=1, max_length=255)
    industry_id: UUID


class CompanyResponse(BaseModel):
    id: UUID
    name: str
    ticker: Optional[str] = None
    industry_id: UUID
    position_factor: float
    # CS4 enriched fields
    sector: Optional[str] = None
    sub_sector: Optional[str] = None
    market_cap_percentile: Optional[float] = None
    revenue_millions: Optional[float] = None
    employee_count: Optional[int] = None
    fiscal_year_end: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    cache: Optional[CacheInfo] = None

    @computed_field  # type: ignore[misc]
    @property
    def company_id(self) -> str:
        """String company_id for CS5 client compatibility."""
        return str(self.id)

    class Config:
        from_attributes = True


class DimensionKeywordsResponse(BaseModel):
    ticker: str
    dimension: str
    keywords: List[str]


class CompanyListResponse(BaseModel):
    """Response for get all companies (no pagination)."""
    items: list[CompanyResponse]
    total: int
    cache: Optional[CacheInfo] = None


class PaginatedCompanyResponse(BaseModel):
    items: list[CompanyResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    cache: Optional[CacheInfo] = None


#  Exception Helpers


def raise_industry_not_found():
    raise NotFoundError("industry", "unknown")

def raise_duplicate_company():
    raise ConflictError("Company already exists in this industry", error_code="DUPLICATE_COMPANY")



#  Cache Helpers


CACHE_KEY_COMPANY_PREFIX = "company:"
CACHE_KEY_COMPANIES_LIST_PREFIX = "companies:list:"
CACHE_KEY_COMPANIES_ALL = "companies:all"
CACHE_KEY_COMPANIES_BY_INDUSTRY = "companies:industry:"


def get_company_cache_key(company_id: UUID) -> str:
    return f"{CACHE_KEY_COMPANY_PREFIX}{company_id}"


def get_companies_list_cache_key(page: int, page_size: int, industry_id: Optional[UUID], min_revenue: Optional[float] = None) -> str:
    return f"{CACHE_KEY_COMPANIES_LIST_PREFIX}page:{page}:size:{page_size}:industry:{industry_id}:min_revenue:{min_revenue}"


def invalidate_company_cache(company_id: Optional[UUID] = None) -> None:
    """Invalidate company cache entries in Redis."""
    cache = get_cache()
    if cache:
        try:
            if company_id:
                cache.delete(get_company_cache_key(company_id))
            cache.delete_pattern(f"{CACHE_KEY_COMPANIES_LIST_PREFIX}*")
            cache.delete(CACHE_KEY_COMPANIES_ALL)
            cache.delete_pattern(f"{CACHE_KEY_COMPANIES_BY_INDUSTRY}*")
        except Exception:
            pass



#  Helper Functions


def row_to_response(row: dict, cache_info: Optional[CacheInfo] = None) -> CompanyResponse:
    return CompanyResponse(
        id=UUID(row["id"]),
        name=row["name"],
        ticker=row["ticker"],
        industry_id=UUID(row["industry_id"]),
        position_factor=float(row["position_factor"]),
        sector=row.get("sector"),
        sub_sector=row.get("sub_sector"),
        market_cap_percentile=float(row["market_cap_percentile"]) if row.get("market_cap_percentile") is not None else None,
        revenue_millions=float(row["revenue_millions"]) if row.get("revenue_millions") is not None else None,
        employee_count=int(row["employee_count"]) if row.get("employee_count") is not None else None,
        fiscal_year_end=row.get("fiscal_year_end"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        cache=cache_info,
    )



#  Identifier Resolver


def resolve_company_identifier(
    ticker: str, company_repo: CompanyRepository
) -> dict:
    """Accept either a UUID string or a ticker; return the company row or raise 404."""
    import uuid as _uuid
    try:
        company_id = _uuid.UUID(ticker)
        company = company_repo.get_by_id(company_id)
    except ValueError:
        company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise NotFoundError("company", ticker)
    return company


#  Routes


def _enrich_company_in_background(company_id: UUID, ticker: str, name: str, company_repo: CompanyRepository) -> None:
    """Background task: call Groq to fill in enriched fields, create portfolio, and persist."""
    try:
        # 1. Enrich company metadata fields
        enriched = enrich_company_metadata(ticker, name)
        if enriched:
            company_repo.update_enriched_fields(company_id, **enriched)
            logger.info("Groq company enrichment complete for %s", ticker)

        # 2. Enrich portfolio metadata and create portfolio entry
        portfolio_data = enrich_portfolio_metadata(ticker, name)
        portfolio_id = company_repo.create_portfolio(
            name=portfolio_data["portfolio_name"],
            fund_vintage=portfolio_data.get("fund_vintage"),
        )
        company_repo.add_company_to_portfolio(portfolio_id, str(company_id))
        logger.info("Portfolio created for %s: id=%s name='%s'", ticker, portfolio_id, portfolio_data["portfolio_name"])
    except Exception as exc:
        logger.warning("Groq enrichment background task failed for %s: %s", ticker, exc)


@router.post(
    "/companies",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new company",
    description=(
        "Creates a new company. Validates schema, checks industry existence, and enforces uniqueness. "
        "Kicks off a background Groq enrichment to fill sub_sector, market_cap_percentile, "
        "revenue_millions, employee_count, and fiscal_year_end for any ticker."
    ),
)
async def create_company(
    company: CompanyCreate,
    background_tasks: BackgroundTasks,
    company_repo: CompanyRepository = Depends(get_company_repository),
    industry_repo: IndustryRepository = Depends(get_industry_repository),
) -> CompanyResponse:
    if not industry_repo.exists(company.industry_id):
        raise_industry_not_found()

    if company_repo.check_duplicate(company.name, company.industry_id):
        raise_duplicate_company()

    company_data = company_repo.create(
        name=company.name,
        industry_id=company.industry_id,
        ticker=company.ticker,
        position_factor=company.position_factor,
    )

    # Kick off Groq enrichment in the background so the response returns immediately
    if company.ticker:
        background_tasks.add_task(
            _enrich_company_in_background,
            UUID(str(company_data["id"])),
            company.ticker,
            company.name,
            company_repo,
        )

    invalidate_company_cache()

    return row_to_response(company_data)


@router.get(
    "/companies/all",
    response_model=CompanyListResponse,
    summary="Get all companies",
    description="Returns all companies without pagination. Cached for 5 minutes.",
)
async def get_all_companies(
    company_repo: CompanyRepository = Depends(get_company_repository),
) -> CompanyListResponse:
    cache_key = CACHE_KEY_COMPANIES_ALL

    def _fetch():
        companies = company_repo.get_all()
        return CompanyListResponse(items=[row_to_response(c) for c in companies], total=len(companies))

    result, hit, latency = cached_query(cache_key, TTL_COMPANY, CompanyListResponse, _fetch)
    result.cache = create_cache_info(hit, cache_key, latency, TTL_COMPANY)
    return result


@router.get(
    "/companies",
    response_model=PaginatedCompanyResponse,
    summary="List companies (paginated)",
    description="Returns a paginated list of companies. Optionally filter by industry and/or minimum revenue. Cached for 5 minutes.",
)
async def list_companies(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    industry_id: Optional[UUID] = Query(default=None),
    min_revenue: Optional[float] = Query(default=None, description="Minimum annual revenue in USD millions"),
    company_repo: CompanyRepository = Depends(get_company_repository),
) -> PaginatedCompanyResponse:
    cache_key = get_companies_list_cache_key(page, page_size, industry_id, min_revenue)

    def _fetch():
        all_companies = company_repo.get_by_industry(industry_id) if industry_id else company_repo.get_all()
        if min_revenue is not None:
            all_companies = [
                c for c in all_companies
                if c.get("revenue_millions") is not None and float(c["revenue_millions"]) >= min_revenue
            ]
        total = len(all_companies)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        start_idx = (page - 1) * page_size
        companies = all_companies[start_idx:start_idx + page_size]
        return PaginatedCompanyResponse(
            items=[row_to_response(c) for c in companies],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    result, hit, latency = cached_query(cache_key, TTL_COMPANY, PaginatedCompanyResponse, _fetch)
    result.cache = create_cache_info(hit, cache_key, latency, TTL_COMPANY)
    return result


@router.get(
    "/companies/{ticker}",
    response_model=CompanyResponse,
    summary="Get company by ID or ticker",
    description="Retrieves a company by UUID or ticker symbol (case-insensitive). Cached for 5 minutes.",
)
async def get_company(
    ticker: str,
    company_repo: CompanyRepository = Depends(get_company_repository),
) -> CompanyResponse:
    company = resolve_company_identifier(ticker, company_repo)
    company_id = UUID(str(company["id"]))

    cache_key = get_company_cache_key(company_id)

    def _fetch():
        return row_to_response(company_repo.get_by_id(company_id))

    result, hit, latency = cached_query(cache_key, TTL_COMPANY, CompanyResponse, _fetch)
    result.cache = create_cache_info(hit, cache_key, latency, TTL_COMPANY)
    return result


# Default rubric keywords per dimension — expanded dynamically by Groq
_BASE_DIMENSION_KEYWORDS = {
    "data_infrastructure": ["data lake", "data warehouse", "ETL", "data pipeline", "real-time data", "cloud storage"],
    "ai_governance": ["AI ethics", "model governance", "responsible AI", "bias detection", "explainability", "AI policy"],
    "technology_stack": ["machine learning platform", "MLOps", "Kubernetes", "cloud-native", "microservices", "API gateway"],
    "talent": ["machine learning engineer", "data scientist", "AI researcher", "NLP", "computer vision", "deep learning"],
    "leadership": ["Chief AI Officer", "AI strategy", "digital transformation", "technology roadmap", "innovation lab"],
    "use_case_portfolio": ["AI use case", "automation", "predictive analytics", "recommendation system", "computer vision"],
    "culture": ["data-driven", "experimentation", "agile", "innovation culture", "AI adoption", "continuous learning"],
}


@router.get(
    "/companies/{ticker}/dimension-keywords",
    response_model=DimensionKeywordsResponse,
    summary="Get Groq-expanded scoring keywords for a company and dimension",
    description=(
        "Returns base rubric keywords expanded with company-specific synonyms via Groq. "
        "Used by the CS4 RAG search to improve evidence retrieval quality."
    ),
)
async def get_dimension_keywords_endpoint(
    ticker: str,
    dimension: str = Query(..., description="One of the 7 V^R dimensions, e.g. data_infrastructure"),
    company_repo: CompanyRepository = Depends(get_company_repository),
) -> DimensionKeywordsResponse:
    ticker = ticker.upper()
    base_keywords = _BASE_DIMENSION_KEYWORDS.get(dimension, [])

    company = company_repo.get_by_ticker(ticker)
    if not company:
        # Return base keywords even if company not yet in DB (e.g. during onboarding)
        return DimensionKeywordsResponse(ticker=ticker, dimension=dimension, keywords=base_keywords)

    expanded = get_dimension_keywords(
        ticker=ticker,
        company_name=company["name"],
        dimension=dimension,
        base_keywords=base_keywords,
    )
    return DimensionKeywordsResponse(ticker=ticker, dimension=dimension, keywords=expanded)
