"""
Lifespan — PE Org-AI-R Platform
app/core/lifespan.py

Central lifecycle manager. Creates all singletons at startup, attaches to
app.state, and cleans up at shutdown.
"""

import signal
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.shutdown import set_shutdown

logger = logging.getLogger(__name__)


def _create_singletons(app: FastAPI) -> None:
    """Instantiate all singletons and attach them to app.state."""

    # ── 1. Repositories (all no-arg constructors) ────────────────────────
    from app.repositories.company_repository import CompanyRepository
    from app.repositories.industry_repository import IndustryRepository
    from app.repositories.assessment_repository import AssessmentRepository
    from app.repositories.dimension_score_repository import DimensionScoreRepository
    from app.repositories.document_repository import DocumentRepository
    from app.repositories.signal_repository import SignalRepository
    from app.repositories.scoring_repository import ScoringRepository
    from app.repositories.composite_scoring_repository import CompositeScoringRepository
    from app.repositories.health_repository import HealthRepository

    app.state.company_repository = CompanyRepository()
    app.state.industry_repository = IndustryRepository()
    app.state.assessment_repository = AssessmentRepository()
    app.state.dimension_score_repository = DimensionScoreRepository()
    app.state.document_repository = DocumentRepository()
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
    app.state.cs2_client = CS2Client()

    # ── 3. HybridRetriever ───────────────────────────────────────────────
    from app.services.retrieval.hybrid import HybridRetriever

    app.state.hybrid_retriever = HybridRetriever()

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

    # ── 5. Domain services (no-arg; resolve internal deps via factories) ─
    from app.services.composite_scoring_service import CompositeScoringService
    from app.services.document_collector import DocumentCollectorService
    from app.services.document_parsing_service import DocumentParsingService
    from app.services.document_chunking_service import DocumentChunkingService
    from app.services.scoring_service import ScoringService
    from app.services.job_signal_service import JobSignalService
    from app.services.patent_signal_service import PatentSignalService
    from app.services.tech_signal_service import TechSignalService
    from app.services.leadership_service import LeadershipSignalService

    app.state.composite_scoring_service = CompositeScoringService()
    app.state.document_collector_service = DocumentCollectorService()
    app.state.document_parsing_service = DocumentParsingService()
    app.state.document_chunking_service = DocumentChunkingService()
    app.state.scoring_service = ScoringService()
    app.state.job_signal_service = JobSignalService()
    app.state.patent_signal_service = PatentSignalService()
    app.state.tech_signal_service = TechSignalService()
    app.state.leadership_service = LeadershipSignalService()


def _cleanup_singletons(app: FastAPI) -> None:
    """Clean up singletons at shutdown. Minimal for now — repos use context managers."""
    logger.info("Singleton cleanup complete")


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
    logger.info("Starting PE Org-AI-R Platform Foundation API...")
    print("Starting PE Org-AI-R Platform Foundation API...")
    print("Swagger UI available at: http://localhost:8000/docs")

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
    set_shutdown()
    _cleanup_singletons(app)
