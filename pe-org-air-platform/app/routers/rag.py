# app/routers/rag.py
"""RAG Router — FastAPI endpoints for CS4 RAG search and justification."""
from __future__ import annotations

import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.services.integration.cs2_client import CS2Client
from app.services.search.vector_store import VectorStore, EMBEDDING_MODEL
from app.services.retrieval.hybrid import HybridRetriever, RetrievedDocument
from app.services.retrieval.dimension_mapper import DimensionMapper
from app.services.retrieval.hyde import HyDERetriever
from app.services.justification.generator import JustificationGenerator
from app.services.workflows.ic_prep import ICPrepWorkflow
from app.services.llm.router import ModelRouter
from app.prompts.rag_prompts import (
    DIM_DETECTION_SYSTEM,
    DIM_DETECTION_USER,
    CHATBOT_SYSTEM,
    CHATBOT_USER,
)
import structlog
from app.guardrails.input_guards import validate_ticker, validate_question, validate_dimension
from app.guardrails.output_guards import check_answer_length, check_answer_grounded, check_no_refusal
from app.models.enumerations import DIMENSION_ALIAS_MAP
from app.core.dependencies import (
    get_vector_store as _get_vector_store,
    get_hybrid_retriever as _get_retriever,
    get_model_router as _get_router,
    get_dimension_mapper as _get_mapper,
    get_cs2_client as _get_cs2,
    get_ic_prep_workflow as _get_ic_prep,
    get_scoring_repository as _get_scoring_repo,
    get_signal_repository as _get_signal_repo,
)
from app.core.errors import ValidationError as PlatformValidationError, ExternalServiceError
from app.config.retrieval_settings import RETRIEVAL_SETTINGS

logger = structlog.get_logger()

MAX_CONTEXT_CHARS = RETRIEVAL_SETTINGS.max_context_chars

router = APIRouter(prefix="/api/v1/rag", tags=["CS4 RAG"])


# ── Dimension → keyword query map ─────────────────────────────────────────────
DIMENSION_QUERY_MAP: Dict[str, str] = {
    "data_infrastructure": (
        "data platform cloud infrastructure pipeline snowflake databricks "
        "data quality lakehouse real-time API data catalog"
    ),
    "ai_governance": (
        "AI policy governance risk management compliance board committee "
        "CAIO CDO chief data officer model risk oversight"
    ),
    "technology_stack": (
        "machine learning MLOps GPU generative AI SageMaker MLflow "
        "deep learning PyTorch TensorFlow feature store model registry"
    ),
    "talent": (
        "AI engineers hiring machine learning data scientists talent "
        "ML platform team AI research staff retention"
    ),
    "leadership": (
        "CEO AI strategy executive AI investment roadmap CTO CDO "
        "board AI committee strategic priorities digital transformation"
    ),
    "use_case_portfolio": (
        "AI use cases production deployment ROI revenue AI products "
        "pilots proof of concept automation predictive analytics"
    ),
    "culture": (
        "innovation data-driven culture experimentation fail-fast "
        "agile learning change readiness digital culture"
    ),
}

DIMENSION_SOURCE_AFFINITY: Dict[str, List[str]] = {
    "data_infrastructure": ["sec_10k_item_1", "sec_10k_item_7"],
    "ai_governance":       ["sec_10k_item_1a", "board_proxy_def14a"],
    "technology_stack":    ["sec_10k_item_1", "sec_10k_item_7", "patent_uspto"],
    "talent":              ["job_posting_indeed", "job_posting_linkedin", "glassdoor_review"],
    "leadership":          ["sec_10k_item_7", "sec_10k_item_1a", "sec_10k_item_1", "board_proxy_def14a"],
    "use_case_portfolio":  ["sec_10k_item_1", "sec_10k_item_7"],
    "culture":             ["glassdoor_review", "sec_10k_item_1"],
}

# ── Dimension detection ────────────────────────────────────────────────────────
_DIMENSION_DISCRIMINATORS: Dict[str, set] = {
    "data_infrastructure": {"snowflake", "databricks", "lakehouse", "pipeline", "catalog", "ingestion"},
    "ai_governance":       {"governance", "compliance", "oversight", "policy", "caio", "cdo"},
    "technology_stack":    {"gpu", "mlops", "sagemaker", "mlflow", "pytorch", "tensorflow"},
    "talent":              {"hiring", "engineers", "scientists", "recruitment", "retention", "headcount"},
    "leadership":          {"ceo", "cto", "executive", "roadmap", "strategy"},
    "use_case_portfolio":  {"revenue", "roi", "pilots", "production", "automation"},
    "culture":             {"culture", "agile", "experimentation", "fail-fast"},
}

_COMMON_WORDS = {"ai", "the", "and", "for", "with", "model", "data", "digital", "learning", "board"}

_DIM_CONFIDENCE_THRESHOLD = 0.12

# ── LLM dimension detection ────────────────────────────────────────────────────
_DIMENSION_DESCRIPTIONS: Dict[str, str] = {
    "data_infrastructure": (
        "Data platforms, cloud pipelines, databases, data lakes, ETL/ELT workflows, "
        "data quality, real-time streaming, data catalogs, storage architecture, "
        "lakehouse (e.g. Snowflake, Databricks, BigQuery, Redshift)"
    ),
    "ai_governance": (
        "AI policy and ethics, regulatory compliance, export controls, risk management "
        "frameworks, board AI committee, model risk oversight, responsible AI, CAIO/CDO roles, "
        "government regulations affecting AI products"
    ),
    "technology_stack": (
        "ML/AI frameworks and tools, GPU/hardware for AI, software SDKs/APIs/libraries, "
        "developer platforms, MLOps tooling, CUDA/PyTorch/TensorFlow, model training "
        "infrastructure, proprietary AI platforms, technology architecture"
    ),
    "talent": (
        "AI/ML hiring and job postings, data scientist recruitment, engineer headcount, "
        "talent pipeline, workforce skills, employee retention for technical roles, "
        "internship programs, AI research staff"
    ),
    "leadership": (
        "CEO/CTO/CDO statements on AI strategy, board-level AI priorities, executive "
        "investment roadmap, digital transformation direction, strategic AI commitments, "
        "management discussion of AI direction (MD&A)"
    ),
    "use_case_portfolio": (
        "Specific AI products deployed in production, commercial AI applications, "
        "AI revenue streams, business automation use cases, proof-of-concept to production "
        "deployments, named product lines or platforms generating revenue from AI"
    ),
    "culture": (
        "Innovation culture, data-driven mindset, employee reviews of work environment, "
        "agile/experimental culture, change readiness, Glassdoor feedback, "
        "internal collaboration norms, fail-fast mentality"
    ),
}


async def _detect_dimension_with_llm(
    question: str,
    router: ModelRouter,
) -> tuple[Optional[str], float]:
    """
    LLM-assisted dimension detection for company-specific or ambiguous queries.
    FIX: Now async — properly awaits router.complete().
    """
    valid_dims = set(_DIMENSION_DESCRIPTIONS.keys())
    dim_list = "\n".join(
        f"  {dim}: {desc}"
        for dim, desc in _DIMENSION_DESCRIPTIONS.items()
    )
    messages = [
        {
            "role": "system",
            "content": DIM_DETECTION_SYSTEM.format(
                valid_dims="\n".join(f"  {d}" for d in valid_dims)
            ),
        },
        {
            "role": "user",
            "content": DIM_DETECTION_USER.format(
                question=question,
                dim_list=dim_list,
            ),
        },
    ]
    try:
        raw = await router.complete("keyword_matching", messages)
        if hasattr(raw, "choices"):
            raw = raw.choices[0].message.content
        raw = str(raw).strip()
        detected = raw.lower().replace('"', "").replace("'", "").split()[0]
        if detected in valid_dims:
            logger.info("rag.llm_dim_detected", question=question[:80], dimension=detected)
            return detected, 0.75
        logger.warning("rag.llm_dim_invalid_response", question=question[:80], raw_response=raw[:100])
        return None, 0.0
    except Exception as e:
        logger.warning("rag.llm_dim_detection_failed", question=question[:80], error=str(e))
        return None, 0.0


def _detect_dimension_scored(question: str) -> tuple[Optional[str], float]:
    """
    Weighted dimension detection.

    FIX: Broad/overall/strengths questions now return (None, 0.0)
    so retrieval happens across ALL dimensions — not locked to one.
    """
    q_lower = question.lower()
    q_words = set(q_lower.split())

    _GAP_TRIGGERS = {
        "gaps", "gap", "weaknesses", "weakness", "risks", "risk",
        "missing", "lacking", "improve", "improvement", "challenge",
        "challenges", "concerns", "concern", "shortcoming", "shortcomings",
        "threats", "threat",
    }
    _BROAD_TRIGGERS = {
        "overall", "readiness", "assessment", "strengths", "strength",
        "summary", "overview", "evaluate", "evaluation", "prepare",
        "investment", "committee", "general", "biggest", "compared",
        "competitors", "competitive", "versus", "vs",
    }

    has_gap = bool(q_words & _GAP_TRIGGERS)
    has_broad = bool(q_words & _BROAD_TRIGGERS)

    # If BOTH gap and broad words → treat as broad (retrieve everything)
    if has_gap and has_broad:
        return None, 0.0

    # Pure gap question → ai_governance
    if has_gap and not has_broad:
        return "ai_governance", 0.30

    # Priority 2: talent/hiring — only if not also broad
    _TALENT_TRIGGERS = {
        "talent", "hiring", "hire", "recruitment", "engineers", "employees",
        "workforce", "headcount", "jobs", "job", "postings", "roles",
        "staff", "team",
    }
    if q_words & _TALENT_TRIGGERS and not has_broad:
        return "talent", 0.35

    # Priority 3: score justification
    _SCORE_TRIGGERS = {"score", "scored", "scoring", "why", "justify", "justification"}
    if q_words & _SCORE_TRIGGERS:
        for dim in DIMENSION_QUERY_MAP:
            dim_words = set(dim.replace("_", " ").lower().split())
            if dim_words & q_words:
                return dim, 0.50

    # Priority 4: broad → NO dimension lock, retrieve across everything
    if has_broad:
        return None, 0.0

    # Priority 5: weighted keyword overlap
    best_dim: Optional[str] = None
    best_score: float = 0.0

    for dim, keywords_str in DIMENSION_QUERY_MAP.items():
        kw_tokens = keywords_str.lower().split()
        discriminators = _DIMENSION_DISCRIMINATORS.get(dim, set())
        raw = 0.0
        for token in kw_tokens:
            if token not in q_words:
                continue
            if token in discriminators:
                raw += 3.0
            elif token in _COMMON_WORDS:
                raw += 0.3
            else:
                raw += 1.0
        normalised = raw / max(len(kw_tokens), 1)
        if normalised > best_score:
            best_score = normalised
            best_dim = dim

    if best_score < 0.05:
        return None, 0.0
    return best_dim, round(best_score, 4)


# ── Filter builder ─────────────────────────────────────────────────────────────

def _build_filter(
    ticker: str,
    dimension: Optional[str] = None,
    source_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build ChromaDB where-clause. ticker is always applied."""
    conditions: List[Dict] = [{"ticker": ticker}]

    if dimension and dimension not in ("string", ""):
        conditions.append({"dimension": dimension})

    if source_types:
        valid = [s for s in source_types if s and s != "string"]
        if valid:
            conditions.append({"source_type": {"$in": valid}})

    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ── Retrieval with fallback ────────────────────────────────────────────────────

async def _retrieve_with_fallback(
    retriever: HybridRetriever,
    query: str,
    ticker: str,
    dimension: Optional[str],
    top_k: int,
    min_results: int = 3,
    dim_confidence: float = 1.0,
    vector_store: Optional[VectorStore] = None,
) -> List:
    """
    Retrieve with graceful dimension-filter fallback.

    FIX: When dimension is None (broad questions), retrieves across ALL
    source types to give balanced evidence from SEC, jobs, Glassdoor, patents.
    """
    vs = vector_store or _get_vector_store()

    # Talent dimension — force job postings via direct vector store search
    if dimension == "talent":
        talent_query = (
            "machine learning AI engineer data scientist MLOps deep learning "
            "generative AI LLM hiring software engineer " + query
        )
        # Try job postings first
        job_results = vs.search(
            query=talent_query, top_k=top_k,
            ticker=ticker,
            source_types=["job_posting_indeed", "job_posting_linkedin"],
        )
        if len(job_results) >= min_results:
            return job_results
        # Add glassdoor if not enough jobs
        gd_results = vs.search(
            query=talent_query, top_k=top_k,
            ticker=ticker,
            source_types=["glassdoor_review"],
        )
        combined = list(job_results) + list(gd_results)
        if combined:
            seen = set()
            deduped = []
            for r in sorted(combined, key=lambda x: x.score, reverse=True):
                if r.doc_id not in seen:
                    seen.add(r.doc_id)
                    deduped.append(r)
            return deduped[:top_k]

    # Culture dimension — force Glassdoor via direct vector store search
    if dimension == "culture":
        culture_results = vs.search(
            query=query, top_k=top_k,
            ticker=ticker,
            source_types=["glassdoor_review"],
        )
        if len(culture_results) >= min_results:
            return culture_results

    # ── BROAD QUESTIONS (dimension=None) — pull diverse evidence mix ──────────
    if dimension is None:
        # For broad questions, pull from diverse sources using DENSE search only
        # (skip BM25 which may be under-seeded). Use the vector store directly
        # for faster, more reliable results across source types.
        source_groups = [
            ["sec_10k_item_1"],
            ["sec_10k_item_7"],
            ["sec_10k_item_1a"],
            ["board_proxy_def14a"],
            ["job_posting_indeed", "job_posting_linkedin"],
            ["glassdoor_review"],
            ["patent_uspto"],
        ]
        diverse_results = []
        per_group = max(2, top_k // len(source_groups))

        for src_types in source_groups:
            try:
                group_results = vs.search(
                    query=query,
                    top_k=per_group,
                    ticker=ticker,
                    source_types=src_types,
                )
                for r in group_results:
                    diverse_results.append(r)
            except Exception as e:
                logger.debug("rag.broad_group_search_failed", src_types=src_types, error=str(e))
                continue

        if len(diverse_results) >= min_results:
            # Deduplicate by doc_id, sort by score, return top_k
            # Convert SearchResult from vector_store to RetrievedDocument format
            seen = set()
            deduped = []
            for r in sorted(diverse_results, key=lambda x: x.score, reverse=True):
                if r.doc_id not in seen:
                    seen.add(r.doc_id)
                    deduped.append(RetrievedDocument(
                        doc_id=r.doc_id,
                        content=r.content,
                        metadata=r.metadata,
                        score=r.score,
                        retrieval_method="dense_diverse",
                    ))
            if deduped:
                return deduped[:top_k]

        # If diverse retrieval got too few, fall through to ticker-only below

    results = []

    _SEC_PRIMARY_DIMS = {
        "data_infrastructure", "ai_governance", "technology_stack",
        "leadership", "use_case_portfolio",
    }

    if dimension:
        if dim_confidence >= _DIM_CONFIDENCE_THRESHOLD:
            dim_keywords = DIMENSION_QUERY_MAP.get(dimension, "")
            enriched_query = (dim_keywords + " " + query).strip()
        else:
            enriched_query = query

        filter_with_dim = _build_filter(ticker, dimension=dimension)
        results = retriever.retrieve(enriched_query, k=top_k, filter_metadata=filter_with_dim)

        if len(results) >= min_results:
            return results

        logger.info("rag.fallback_triggered", ticker=ticker, dimension=dimension,
                    dim_confidence=dim_confidence, dim_results=len(results),
                    reason="too_few_dim_results")

        # SEC source affinity for SEC-primary dimensions
        # FIX: prioritize Item 1 and Item 7 over Item 1A for non-governance dims
        if dimension in _SEC_PRIMARY_DIMS:
            if dimension == "ai_governance":
                sec_sources = ["sec_10k_item_1a", "board_proxy_def14a"]
            else:
                # Item 1 (Business) and Item 7 (MD&A) have capabilities/strategy
                # Item 1A (Risk) only has risk language — deprioritize for strengths
                sec_sources = ["sec_10k_item_1", "sec_10k_item_7", "board_proxy_def14a"]
            filter_sec = _build_filter(ticker, source_types=sec_sources)
            sec_results = retriever.retrieve(enriched_query, k=top_k, filter_metadata=filter_sec)
            if len(sec_results) > len(results):
                results = sec_results
            if len(results) >= min_results:
                return results

        # General source affinity fallback
        affinity_sources = DIMENSION_SOURCE_AFFINITY.get(dimension, [])
        if affinity_sources:
            filter_src = _build_filter(ticker, source_types=affinity_sources)
            src_results = retriever.retrieve(enriched_query, k=top_k, filter_metadata=filter_src)
            if len(src_results) > len(results):
                results = src_results

        if len(results) >= min_results:
            return results

    # Ticker-only fallback — raw query, no dimension pollution
    filter_ticker_only = _build_filter(ticker)
    fallback = retriever.retrieve(query, k=top_k, filter_metadata=filter_ticker_only)

    # For SEC-primary dims, prefer SEC results even in fallback
    if dimension and dimension in _SEC_PRIMARY_DIMS and fallback:
        sec_fallback = [
            r for r in fallback
            if r.metadata.get("source_type", "").startswith("sec_")
            or r.metadata.get("source_type", "") == "board_proxy_def14a"
        ]
        if len(sec_fallback) >= min_results:
            return sec_fallback

    return fallback if len(fallback) > len(results) else results


# ── Request / Response Models ─────────────────────────────────────────────────

class IndexRequest(BaseModel):
    source_types: Optional[List[str]] = None
    signal_categories: Optional[List[str]] = None
    min_confidence: float = 0.0


class IndexResponse(BaseModel):
    indexed_count: int
    ticker: str
    source_counts: Dict[str, int] = {}


class BulkIndexRequest(BaseModel):
    tickers: List[str]
    source_types: Optional[List[str]] = None
    signal_categories: Optional[List[str]] = None
    min_confidence: float = 0.0
    force: bool = False


class BulkIndexResponse(BaseModel):
    results: Dict[str, IndexResponse]
    total_indexed: int
    failed: Dict[str, str]


class SearchRequest(BaseModel):
    query: str
    ticker: Optional[str] = None
    source_types: Optional[List[str]] = None
    dimension: Optional[str] = None
    top_k: int = 10
    use_hyde: bool = False


class SearchResult(BaseModel):
    doc_id: str
    content: str
    metadata: Dict[str, Any]
    score: float
    retrieval_method: str


class JustifyResponse(BaseModel):
    ticker: str
    dimension: str
    score: float
    level: int
    level_name: str
    generated_summary: str
    evidence_strength: str
    rubric_criteria: str
    supporting_evidence: List[Dict[str, Any]]
    gaps_identified: List[str]


class EvidenceItemResponse(BaseModel):
    source_type: str
    content: str
    confidence: float
    signal_category: str


class CompanyEvidenceListResponse(BaseModel):
    company_id: str
    dimension: Optional[str]
    count: int
    evidence: List[EvidenceItemResponse]


class ICPrepResponse(BaseModel):
    company_id: str
    ticker: str
    executive_summary: str
    recommendation: str
    key_strengths: List[str]
    key_gaps: List[str]
    risk_factors: List[str]
    dimension_scores: Dict[str, float]
    total_evidence_count: int
    generated_at: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/index/{ticker}", response_model=IndexResponse)
async def index_company_evidence(
    ticker: str,
    source_types: Optional[str] = Query(None),
    signal_categories: Optional[str] = Query(None),
    min_confidence: float = Query(0.0),
    force: bool = Query(False),
    vs: VectorStore = Depends(_get_vector_store),
    retriever: HybridRetriever = Depends(_get_retriever),
    mapper: DimensionMapper = Depends(_get_mapper),
    cs2: CS2Client = Depends(_get_cs2),
):
    """Fetch CS2 evidence for a company and index into ChromaDB."""
    logger.info("rag.index_start", ticker=ticker, force=force)

    if force:
        st_list = [s.strip() for s in source_types.split(",")] if source_types else None
        where: dict = {"ticker": {"$eq": ticker}}
        if st_list:
            where = {"$and": [where, {"source_type": {"$in": st_list}}]}
        vs.delete_by_filter(where)

    evidence = cs2.get_evidence(
        ticker=ticker,
        source_types=[s.strip() for s in source_types.split(",")] if source_types else None,
        signal_categories=[s.strip() for s in signal_categories.split(",")] if signal_categories else None,
        min_confidence=min_confidence,
    )

    from collections import defaultdict
    source_counts: Dict[str, int] = defaultdict(int)
    for e in evidence:
        source_counts[e.signal_category] += 1

    count = vs.index_cs2_evidence(evidence, mapper)
    if evidence:
        cs2.mark_indexed([e.evidence_id for e in evidence])

    retriever.seed_from_evidence(evidence)
    retriever.refresh_sparse_index()

    logger.info("rag.index_complete", ticker=ticker, indexed_count=count)
    return IndexResponse(indexed_count=count, ticker=ticker, source_counts=dict(source_counts))


@router.post("/index", response_model=BulkIndexResponse)
async def bulk_index_evidence(
    req: BulkIndexRequest,
    vs: VectorStore = Depends(_get_vector_store),
    retriever: HybridRetriever = Depends(_get_retriever),
    mapper: DimensionMapper = Depends(_get_mapper),
    cs2: CS2Client = Depends(_get_cs2),
):
    """Index CS2 evidence for multiple tickers in a single call."""
    logger.info("rag.bulk_index_start", tickers=req.tickers)

    from collections import defaultdict
    results: Dict[str, IndexResponse] = {}
    failed: Dict[str, str] = {}
    all_evidence = []

    for ticker in req.tickers:
        try:
            if req.force:
                where: dict = {"ticker": {"$eq": ticker}}
                if req.source_types:
                    where = {"$and": [where, {"source_type": {"$in": req.source_types}}]}
                vs.delete_by_filter(where)

            evidence = cs2.get_evidence(
                ticker=ticker,
                source_types=req.source_types,
                signal_categories=req.signal_categories,
                min_confidence=req.min_confidence,
            )

            source_counts: Dict[str, int] = defaultdict(int)
            for e in evidence:
                source_counts[e.signal_category] += 1

            count = vs.index_cs2_evidence(evidence, mapper)
            if evidence:
                cs2.mark_indexed([e.evidence_id for e in evidence])
                all_evidence.extend(evidence)

            results[ticker] = IndexResponse(
                indexed_count=count, ticker=ticker,
                source_counts=dict(source_counts),
            )
        except Exception as e:
            failed[ticker] = str(e)
            logger.warning("rag.bulk_index_ticker_error", ticker=ticker, error=str(e))

    if all_evidence:
        retriever.seed_from_evidence(all_evidence)
    retriever.refresh_sparse_index()

    total_indexed = sum(r.indexed_count for r in results.values())
    return BulkIndexResponse(results=results, total_indexed=total_indexed, failed=failed)


@router.delete("/index")
async def wipe_index(
    ticker: Optional[str] = Query(None),
    vs: VectorStore = Depends(_get_vector_store),
    retriever: HybridRetriever = Depends(_get_retriever),
):
    """Delete documents from the ChromaDB index."""
    if ticker:
        wiped = vs.delete_by_filter({"ticker": {"$eq": ticker}})
    else:
        wiped = vs.wipe()
        retriever.refresh_sparse_index()
    return {"wiped_count": wiped, "scope": ticker if ticker else "all"}


@router.post("/search", response_model=List[SearchResult])
async def search_evidence(
    req: SearchRequest,
    retriever: HybridRetriever = Depends(_get_retriever),
    vs: VectorStore = Depends(_get_vector_store),
    llm_router: ModelRouter = Depends(_get_router),
):
    """Hybrid dense + sparse search with optional HyDE enhancement."""
    logger.info("rag.search_start", query_len=len(req.query), ticker=req.ticker)

    source_types = None
    if req.source_types:
        source_types = [s for s in req.source_types if s and s != "string"]

    dimension = DIMENSION_ALIAS_MAP.get(req.dimension, req.dimension) if req.dimension else None

    if req.use_hyde and dimension:
        filter_meta = _build_filter(
            req.ticker or "",
            dimension=dimension,
            source_types=source_types,
        ) if req.ticker else {}
        hyde = HyDERetriever(retriever, llm_router)
        results = hyde.retrieve(
            req.query, k=req.top_k,
            filters=filter_meta or None,
            dimension=dimension or "",
        )
    elif req.ticker:
        results = await _retrieve_with_fallback(
            retriever=retriever,
            query=req.query,
            ticker=req.ticker,
            dimension=dimension if dimension and dimension != "string" else None,
            top_k=req.top_k,
            vector_store=vs,
        )
    else:
        filter_meta: Dict[str, Any] = {}
        if source_types:
            filter_meta["source_type"] = {"$in": source_types}
        if dimension and dimension != "string":
            filter_meta["dimension"] = dimension
        results = retriever.retrieve(
            req.query, k=req.top_k,
            filter_metadata=filter_meta or None,
        )

    logger.info("rag.search_complete", result_count=len(results))
    return [
        SearchResult(
            doc_id=r.doc_id,
            content=r.content[:500],
            metadata=r.metadata,
            score=r.score,
            retrieval_method=r.retrieval_method,
        )
        for r in results
    ]


@router.get("/evidence/{ticker}", response_model=CompanyEvidenceListResponse)
async def get_company_evidence_items(
    ticker: str,
    dimension: Optional[str] = Query(None, description="V^R dimension filter (e.g. talent, culture)"),
    limit: int = Query(10, ge=1, le=200, description="Max evidence items to return"),
    cs2: CS2Client = Depends(_get_cs2),
):
    """Return individual CS2 evidence items for a company, optionally filtered by V^R dimension."""
    ticker = ticker.upper()
    dim_to_signal = {
        "data_infrastructure": ["digital_presence"],
        "ai_governance": ["governance_signals"],
        "technology_stack": ["digital_presence", "technology_hiring"],
        "talent": ["technology_hiring"],
        "leadership": ["leadership_signals"],
        "use_case_portfolio": ["innovation_activity"],
        "culture": ["culture_signals"],
    }
    signal_cats = dim_to_signal.get(dimension) if dimension else None
    evidence = await asyncio.to_thread(cs2.get_evidence, ticker, signal_categories=signal_cats)
    items = [
        EvidenceItemResponse(
            source_type=e.source_type,
            content=e.content[:500],
            confidence=e.confidence,
            signal_category=e.signal_category,
        )
        for e in evidence[:limit]
    ]
    return CompanyEvidenceListResponse(
        company_id=ticker,
        dimension=dimension,
        count=len(items),
        evidence=items,
    )


@router.get("/justify/{ticker}/{dimension}", response_model=JustifyResponse)
async def justify_score(
    ticker: str,
    dimension: str,
    retriever: HybridRetriever = Depends(_get_retriever),
    llm_router: ModelRouter = Depends(_get_router),
):
    """Generate IC-ready justification for a dimension score with cited evidence."""
    logger.info("rag.justify_start", ticker=ticker, dimension=dimension)
    gen = JustificationGenerator(retriever=retriever, router=llm_router)

    try:
        j = await asyncio.to_thread(gen.generate_justification, ticker, dimension)
    except Exception as e:
        logger.error("rag.justify_error", ticker=ticker, dimension=dimension, error=str(e))
        raise ExternalServiceError("rag", f"{type(e).__name__}: {e}")

    return JustifyResponse(
        ticker=j.company_id,
        dimension=j.dimension,
        score=j.score,
        level=j.level,
        level_name=j.level_name,
        generated_summary=j.generated_summary,
        evidence_strength=j.evidence_strength,
        rubric_criteria=j.rubric_criteria,
        supporting_evidence=[
            {
                "source_type": e.source_type,
                "content": e.content[:300],
                "confidence": e.confidence,
            }
            for e in j.supporting_evidence[:5]
        ],
        gaps_identified=j.gaps_identified,
    )


@router.get("/ic-prep/{ticker}", response_model=ICPrepResponse)
async def ic_prep(
    ticker: str,
    dimensions: Optional[str] = Query(None),
    workflow: ICPrepWorkflow = Depends(_get_ic_prep),
):
    """Generate full 7-dimension IC meeting package with recommendation."""
    focus = [d.strip() for d in dimensions.split(",")] if dimensions else None
    logger.info("rag.ic_prep_start", ticker=ticker, focus_dimensions=focus)
    try:
        pkg = await workflow.prepare_meeting(ticker, focus_dimensions=focus)
    except Exception as e:
        logger.error("rag.ic_prep_error", ticker=ticker, error=str(e))
        raise ExternalServiceError("rag", "An internal error occurred during RAG processing.")

    dim_scores = {dim: j.score for dim, j in pkg.dimension_justifications.items()}
    return ICPrepResponse(
        company_id=pkg.company.company_id,
        ticker=pkg.company.ticker,
        executive_summary=pkg.executive_summary,
        recommendation=pkg.recommendation,
        key_strengths=pkg.key_strengths,
        key_gaps=pkg.key_gaps,
        risk_factors=pkg.risk_factors,
        dimension_scores=dim_scores,
        total_evidence_count=pkg.total_evidence_count,
        generated_at=pkg.generated_at,
    )


@router.get("/diagnostics")
async def rag_diagnostics(
    vs: VectorStore = Depends(_get_vector_store),
    retriever: HybridRetriever = Depends(_get_retriever),
):
    """Full ChromaDB diagnostic: accurate per-company document counts."""
    from collections import Counter
    total = vs.count()
    if total == 0:
        return {
            "total_documents": 0,
            "by_company": {},
            "by_source_type": {},
            "by_dimension": {},
            "sparse_index": {
                "sparse_index_size": retriever.sparse_index_size,
                "bm25_initialized": retriever._bm25 is not None,
            },
        }

    all_metas = vs.get_all_metadata()
    by_company   = dict(Counter(m.get("ticker",      "unknown") for m in all_metas).most_common())
    by_source    = dict(Counter(m.get("source_type", "unknown") for m in all_metas).most_common())
    by_dimension = dict(Counter(m.get("dimension",   "unknown") for m in all_metas).most_common())

    return {
        "total_documents": total,
        "by_company": by_company,
        "by_source_type": by_source,
        "by_dimension": by_dimension,
        "sparse_index": {
            "sparse_index_size": retriever.sparse_index_size,
            "bm25_initialized": retriever._bm25 is not None,
        },
    }


@router.get("/chatbot/{ticker}")
async def chatbot_query(
    ticker: str,
    question: str = Query(...),
    dimension: Optional[str] = Query(None),
    use_hyde: bool = Query(False),
    retriever: HybridRetriever = Depends(_get_retriever),
    vs: VectorStore = Depends(_get_vector_store),
    llm_router: ModelRouter = Depends(_get_router),
    scoring_repo=Depends(_get_scoring_repo),
    signal_repo=Depends(_get_signal_repo),
):
    """
    Answer a question about a company using RAG.

    FIX 1: await on all async LLM calls
    FIX 2: broad questions return (None, 0.0) → no dimension lock
    FIX 3: improved system prompt — strengths first, then gaps, always conclude
    """
    result = validate_ticker(ticker)
    if not result.passed:
        logger.warning("rag.guardrail_blocked", guard="validate_ticker", reason=result.reason)
        raise PlatformValidationError(result.reason)

    result = validate_question(question)
    if not result.passed:
        logger.warning("rag.guardrail_blocked", guard="validate_question", reason=result.reason)
        raise PlatformValidationError(result.reason)

    result = validate_dimension(dimension)
    if not result.passed:
        logger.warning("rag.guardrail_blocked", guard="validate_dimension", reason=result.reason)
        raise PlatformValidationError(result.reason)

    logger.info("rag.chatbot_query", ticker=ticker, question_len=len(question))

    detected_dimension = DIMENSION_ALIAS_MAP.get(dimension, dimension) if dimension else dimension
    dim_confidence = 1.0

    if not detected_dimension:
        detected_dimension, dim_confidence = _detect_dimension_scored(question)

        # LLM fallback ONLY when keyword detector returned low confidence
        # (not zero). A confidence of exactly 0.0 with None means the detector
        # intentionally said "this is a broad question — don't lock to any
        # dimension". We must NOT override that with LLM dimension detection,
        # or broad questions get funneled into a single dimension's evidence.
        keyword_intentionally_broad = (detected_dimension is None and dim_confidence == 0.0)

        if not keyword_intentionally_broad and (
            detected_dimension is None or dim_confidence < _DIM_CONFIDENCE_THRESHOLD
        ):
            llm_dim, llm_conf = await _detect_dimension_with_llm(question, llm_router)
            if llm_dim is not None:
                detected_dimension = llm_dim
                dim_confidence = llm_conf
                logger.info("rag.chatbot_used_llm_dim", ticker=ticker,
                            dimension=detected_dimension, confidence=dim_confidence)

    logger.info("rag.chatbot_dim_final", ticker=ticker, dimension=detected_dimension,
                confidence=dim_confidence)

    results = await _retrieve_with_fallback(
        retriever=retriever,
        query=question,
        ticker=ticker,
        dimension=detected_dimension,
        top_k=8,
        min_results=3,
        dim_confidence=dim_confidence,
        vector_store=vs,
    )

    if not results:
        return {
            "answer": (
                f"No evidence found for {ticker}. "
                "Please run the indexing pipeline first via POST /rag/index/{ticker}."
            ),
            "evidence": [],
            "sources_used": 0,
            "ticker": ticker,
            "dimension_detected": detected_dimension,
            "dim_confidence": dim_confidence,
        }

    # ── Build context with score enrichment ──────────────────────────────────
    results_sorted = sorted(results, key=lambda r: r.score, reverse=True)
    context_parts = []
    for r in results_sorted[:6]:
        src = r.metadata.get("source_type", "unknown")
        dim = r.metadata.get("dimension", "")
        fy = r.metadata.get("fiscal_year", "")
        label = (
            f"[{src}"
            + (f", {fy}" if fy else "")
            + (f", dim={dim}" if dim else "")
            + "]"
        )
        context_parts.append(f"{label}\n{r.content[:600]}")

    context = "\n\n---\n\n".join(context_parts)

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[Context truncated to fit token budget.]"

    # ── Enrich context with structured score data ─────────────────────────────
    # Direct Snowflake/S3 calls — NO HTTP self-requests (avoids deadlock)
    score_context = ""
    try:
        # Dimension scores from evidence_dimension_scores table
        dim_rows = scoring_repo.get_dimension_scores(ticker)
        if dim_rows:
            score_lines = [
                f"  {d['dimension'].replace('_',' ').title()}: {float(d['score']):.1f}/100"
                for d in dim_rows if d.get("dimension") and d.get("score") is not None
            ]
            if score_lines:
                score_context += "\nDIMENSION SCORES:\n" + "\n".join(score_lines)
    except Exception as e:
        logger.warning("rag.score_enrich_dims_failed", error=str(e))

    try:
        # Signal summary from company_signal_summaries table
        summary = signal_repo.get_summary_by_ticker(ticker)
        if summary:
            sig_lines = []
            for k, v in summary.items():
                if k.endswith("_score") and v is not None and "composite" not in k:
                    label = k.replace("_score", "").replace("_", " ").title()
                    sig_lines.append(f"  {label}: {v}/100")
            composite = summary.get("composite_score")
            if composite:
                sig_lines.insert(0, f"  Composite: {composite}/100")
            if sig_lines:
                score_context += "\nSIGNAL SCORES:\n" + "\n".join(sig_lines)
    except Exception as e:
        logger.warning("rag.score_enrich_signals_failed", error=str(e))

    try:
        # Culture from S3
        from app.services.signals.culture_signal_service import get_culture_signal_service
        cult_svc = get_culture_signal_service()
        cult_data, _ = cult_svc.get(ticker)
        if cult_data and cult_data.get("overall_score"):
            score_context += (
                f"\nCULTURE ({cult_data.get('review_count', 0)} reviews): "
                f"Overall={cult_data['overall_score']}/100, "
                f"Innovation={cult_data.get('innovation_score', 'N/A')}, "
                f"AI Awareness={cult_data.get('ai_awareness_score', 'N/A')}, "
                f"Change Readiness={cult_data.get('change_readiness_score', 'N/A')}"
            )
    except Exception as e:
        logger.warning("rag.score_enrich_culture_failed", error=str(e))

    dim_instruction = ""
    if detected_dimension and dim_confidence >= _DIM_CONFIDENCE_THRESHOLD:
        dim_label = detected_dimension.replace("_", " ").title()
        dim_instruction = f" Focus your answer on the {dim_label} dimension of AI readiness."

    score_section = f"Structured scores:\n{score_context}\n\n" if score_context else ""

    messages = [
        {
            "role": "system",
            "content": CHATBOT_SYSTEM.format(dim_instruction=dim_instruction),
        },
        {
            "role": "user",
            "content": CHATBOT_USER.format(
                ticker=ticker,
                context=context,
                score_section=score_section,
                question=question,
            ),
        },
    ]

    try:
        # Use chat_response task — routes to Claude Haiku (cheapest Anthropic model)
        # Haiku is ~$0.25/MTok input, $1.25/MTok output vs Sonnet at $3/$15
        # With $5 budget, Haiku gives ~4000 chatbot answers vs ~300 with Sonnet
        raw_answer = await llm_router.complete("chat_response", messages)
        if isinstance(raw_answer, str):
            answer = raw_answer
        elif hasattr(raw_answer, "choices"):
            answer = raw_answer.choices[0].message.content
        else:
            answer = str(raw_answer)

        # Safety check
        if not answer or not answer.strip() or answer.startswith("<coroutine"):
            answer = (
                f"Evidence was retrieved for {ticker} but the model response "
                "could not be parsed. Please try again."
            )
    except Exception as e:
        logger.error("rag.chatbot_llm_error", ticker=ticker, error=str(e))
        answer = f"Evidence retrieved but could not generate answer: {e}"

    logger.info("rag.chatbot_answer_generated", ticker=ticker, answer_len=len(answer),
                sources_used=len(results))

    answer = check_no_refusal(answer)
    answer = check_answer_grounded(answer, results_sorted[:4])
    length_result = check_answer_length(answer)
    if not length_result.passed:
        logger.warning("rag.guardrail_blocked", guard="check_answer_length",
                       reason=length_result.reason)
        answer = f"[Guard: answer quality check failed — {length_result.reason}]"

    return {
        "answer": answer,
        "evidence": [
            {
                "source_type": r.metadata.get("source_type"),
                "dimension": r.metadata.get("dimension"),
                "fiscal_year": r.metadata.get("fiscal_year"),
                "content": r.content[:300],
                "score": round(r.score, 4),
            }
            for r in results_sorted[:4]
        ],
        "sources_used": len(results),
        "dimension_detected": detected_dimension,
        "dim_confidence": dim_confidence,
        "ticker": ticker,
    }