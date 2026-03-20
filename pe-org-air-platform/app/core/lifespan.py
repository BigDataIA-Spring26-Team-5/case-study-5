"""
Lifespan — PE Org-AI-R Platform
app/core/lifespan.py

Central lifecycle manager. Creates all singletons at startup, attaches to
app.state, and cleans up at shutdown.
"""

import signal
import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.shutdown import set_shutdown
from app.services.cache import get_cache

logger = structlog.get_logger()


def _create_singletons(app: FastAPI) -> None:
    """Instantiate all singletons and attach them to app.state."""

    # ── 1. Repositories (all no-arg constructors) ────────────────────────
    from app.repositories.company_repository import CompanyRepository
    from app.repositories.industry_repository import IndustryRepository
    from app.repositories.assessment_repository import AssessmentRepository
    from app.repositories.dimension_score_repository import DimensionScoreRepository
    from app.repositories.document_repository import DocumentRepository
    from app.repositories.chunk_repository import ChunkRepository
    from app.repositories.signal_repository import SignalRepository
    from app.repositories.scoring_repository import ScoringRepository
    from app.repositories.composite_scoring_repository import CompositeScoringRepository
    from app.repositories.health_repository import HealthRepository

    app.state.company_repository = CompanyRepository()
    app.state.industry_repository = IndustryRepository()
    app.state.assessment_repository = AssessmentRepository()
    app.state.dimension_score_repository = DimensionScoreRepository()
    app.state.document_repository = DocumentRepository()
    app.state.chunk_repository = ChunkRepository()
    app.state.signal_repository = SignalRepository()
    app.state.scoring_repository = ScoringRepository()
    app.state.composite_scoring_repository = CompositeScoringRepository()
    app.state.health_repository = HealthRepository()

    # ── 2. Infrastructure services (no-arg constructors) ─────────────────
    from app.services.s3_storage import S3StorageService
    from app.services.search.vector_store import VectorStore
    from app.services.retrieval.dimension_mapper import DimensionMapper
    from app.services.llm.router import ModelRouter
    from app.services.integration.cs2_client import CS2Client

    app.state.s3_service = S3StorageService()
    app.state.vector_store = VectorStore()
    app.state.dimension_mapper = DimensionMapper()
    app.state.model_router = ModelRouter()
    app.state.cs2_client = CS2Client(company_repo=app.state.company_repository)

    # ── 3. HybridRetriever ───────────────────────────────────────────────
    from app.services.retrieval.hybrid import HybridRetriever

    app.state.hybrid_retriever = HybridRetriever()

    # Rebuild BM25 from persistent ChromaDB data (full coverage, not seed-query subset)
    doc_count = app.state.hybrid_retriever.rebuild_sparse_index_from_chroma()
    logger.info("bm25_index_rebuilt", doc_count=doc_count)

    # ── 4. Composed services (depend on singletons above) ────────────────
    from app.services.justification.generator import JustificationGenerator
    from app.services.workflows.ic_prep import ICPrepWorkflow
    from app.services.collection.analyst_notes import AnalystNotesCollector

    app.state.justification_generator = JustificationGenerator(
        scoring_repo=app.state.scoring_repository,
        retriever=app.state.hybrid_retriever,
        router=app.state.model_router,
    )
    app.state.ic_prep_workflow = ICPrepWorkflow(
        company_repo=app.state.company_repository,
        scoring_repo=app.state.scoring_repository,
        composite_repo=app.state.composite_scoring_repository,
        generator=app.state.justification_generator,
    )
    app.state.analyst_notes_collector = AnalystNotesCollector(
        retriever=app.state.hybrid_retriever,
    )

    # ── 5. Domain services (pass already-created repos from app.state) ──
    from app.services.composite_scoring_service import CompositeScoringService
    from app.services.document_collector import DocumentCollectorService
    from app.services.document_parsing_service import DocumentParsingService
    from app.services.document_chunking_service import DocumentChunkingService
    from app.services.scoring_service import ScoringService
    from app.services.signals.job_signal_service import JobSignalService
    from app.services.signals.patent_signal_service import PatentSignalService
    from app.services.signals.tech_signal_service import TechSignalService
    from app.services.signals.leadership_service import LeadershipSignalService
    from app.services.signals.board_composition_service import BoardCompositionService
    from app.services.signals.culture_signal_service import CultureSignalService

    app.state.composite_scoring_service = CompositeScoringService()
    app.state.document_collector_service = DocumentCollectorService(
        company_repo=app.state.company_repository,
        document_repo=app.state.document_repository,
    )
    app.state.document_parsing_service = DocumentParsingService(
        document_repo=app.state.document_repository,
    )
    app.state.document_chunking_service = DocumentChunkingService(
        document_repo=app.state.document_repository,
        chunk_repo=app.state.chunk_repository,
    )
    app.state.scoring_service = ScoringService(
        company_repo=app.state.company_repository,
        scoring_repo=app.state.scoring_repository,
        signal_repo=app.state.signal_repository,
        document_repo=app.state.document_repository,
        chunk_repo=app.state.chunk_repository,
    )
    app.state.job_signal_service = JobSignalService(
        company_repo=app.state.company_repository,
        signal_repo=app.state.signal_repository,
    )
    app.state.patent_signal_service = PatentSignalService(
        company_repo=app.state.company_repository,
        signal_repo=app.state.signal_repository,
    )
    app.state.tech_signal_service = TechSignalService(
        company_repo=app.state.company_repository,
        signal_repo=app.state.signal_repository,
    )
    app.state.leadership_service = LeadershipSignalService(
        company_repo=app.state.company_repository,
        signal_repo=app.state.signal_repository,
        document_repo=app.state.document_repository,
    )
    app.state.board_composition_service = BoardCompositionService(
        company_repo=app.state.company_repository,
    )
    app.state.culture_signal_service = CultureSignalService(
        company_repo=app.state.company_repository,
    )

    # ── 6. Task Store (Redis-backed, in-memory fallback) ─────────────────
    from app.services.task_store import TaskStore
    from app.services.cache import get_cache

    cache = get_cache()
    if cache:
        app.state.task_store = TaskStore(redis_client=cache.client)
    else:
        app.state.task_store = TaskStore(redis_client=None)




def _register_windows_signal_handlers():
    """Fallback signal handlers for Windows (no loop.add_signal_handler support)."""
    original_sigint = signal.getsignal(signal.SIGINT)

    def _windows_handler(signum, frame):
        print("\nReceived Ctrl+C — shutting down gracefully...")
        set_shutdown()
        if callable(original_sigint):
            original_sigint(signum, frame)

    signal.signal(signal.SIGINT, _windows_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    from app.core.logging_config import configure_logging
    configure_logging()

    logger.info("Starting PE Org-AI-R Platform Foundation API...")
    print("Starting PE Org-AI-R Platform Foundation API...")
    print("Swagger UI available at: http://localhost:8000/docs")

    from app.core.settings import get_settings
    _settings = get_settings()
    logger.info(
        "settings_loaded",
        portfolio=["NVDA", "JPM", "WMT", "GE", "DG"],
        dim_weights_sum=round(sum([
            _settings.W_DATA_INFRA, _settings.W_AI_GOVERNANCE, _settings.W_TECH_STACK,
            _settings.W_TALENT, _settings.W_LEADERSHIP, _settings.W_USE_CASES, _settings.W_CULTURE,
        ]), 3),
        alpha=_settings.ALPHA_VR_WEIGHT,
        beta=_settings.BETA_SYNERGY_WEIGHT,
    )

    _create_singletons(app)
    logger.info("All singletons created and attached to app.state")

    loop = asyncio.get_running_loop()

    def _signal_handler(sig):
        print(f"\nReceived {sig.name} — shutting down gracefully...")
        set_shutdown()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler, sig)
    except NotImplementedError:
        print("Signal handlers not supported on Windows, using fallback...")
        _register_windows_signal_handlers()

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    print("Shutting down PE Org-AI-R Platform Foundation API...")
    logger.info("lifespan_shutdown_begin")
    set_shutdown()

    # Close Redis (sync client — no await needed)
    try:
        cache = get_cache()
        if cache and cache.client:
            cache.client.close()
    except Exception as e:
        logger.warning("shutdown_redis_close_error", error=str(e))

    # Close CS2Client (has async close() from BaseAPIClient)
    if hasattr(app.state, "cs2_client"):
        try:
            await app.state.cs2_client.close()
        except Exception as e:
            logger.warning("shutdown_cs2_close_error", error=str(e))

    # Iterate app.state for any remaining .close() methods
    for attr_name in list(vars(app.state).keys()):
        obj = getattr(app.state, attr_name, None)
        if obj is None or obj is app.state.cs2_client:
            continue
        if hasattr(obj, "close") and callable(obj.close):
            try:
                result = obj.close()
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.warning("shutdown_close_error", resource=attr_name, error=str(e))

    logger.info("lifespan_shutdown_complete")
