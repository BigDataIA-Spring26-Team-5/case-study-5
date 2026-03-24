"""
Dependencies — PE Org-AI-R Platform
app/core/dependencies.py

FastAPI dependency injection for repositories, services, and singletons.
All routers should use Depends() with these providers.

Singletons are created at startup in app/core/lifespan.py and attached to
app.state. Each provider here simply reads from request.app.state.
"""

from fastapi import Request


# ── Repository providers ─────────────────────────────────────────────────────

def get_company_repository(request: Request):
    return request.app.state.company_repository


def get_industry_repository(request: Request):
    return request.app.state.industry_repository


def get_assessment_repository(request: Request):
    return request.app.state.assessment_repository


def get_dimension_score_repository(request: Request):
    return request.app.state.dimension_score_repository


def get_document_repository(request: Request):
    return request.app.state.document_repository


def get_signal_repository(request: Request):
    return request.app.state.signal_repository


def get_scoring_repository(request: Request):
    return request.app.state.scoring_repository


def get_composite_scoring_repository(request: Request):
    return request.app.state.composite_scoring_repository


def get_health_repository(request: Request):
    return request.app.state.health_repository


def get_chunk_repository(request: Request):
    return request.app.state.chunk_repository


def get_assessment_snapshot_repository(request: Request):
    return request.app.state.assessment_snapshot_repository


# ── Service providers ────────────────────────────────────────────────────────

def get_vector_store(request: Request):
    return request.app.state.vector_store


def get_hybrid_retriever(request: Request):
    return request.app.state.hybrid_retriever


def get_model_router(request: Request):
    return request.app.state.model_router


def get_dimension_mapper(request: Request):
    return request.app.state.dimension_mapper


def get_analyst_notes_collector(request: Request):
    return request.app.state.analyst_notes_collector


def get_composite_scoring_service(request: Request):
    return request.app.state.composite_scoring_service


def get_document_collector_service(request: Request):
    return request.app.state.document_collector_service


def get_document_parsing_service(request: Request):
    return request.app.state.document_parsing_service


def get_document_chunking_service(request: Request):
    return request.app.state.document_chunking_service


def get_scoring_service(request: Request):
    return request.app.state.scoring_service


def get_job_signal_service(request: Request):
    return request.app.state.job_signal_service


def get_patent_signal_service(request: Request):
    return request.app.state.patent_signal_service


def get_tech_signal_service(request: Request):
    return request.app.state.tech_signal_service


def get_leadership_service(request: Request):
    return request.app.state.leadership_service


def get_board_composition_service(request: Request):
    return request.app.state.board_composition_service


def get_culture_signal_service_dep(request: Request):
    return request.app.state.culture_signal_service


def get_cs2_client(request: Request):
    return request.app.state.cs2_client


def get_ic_prep_workflow(request: Request):
    return request.app.state.ic_prep_workflow


def get_task_store(request: Request):
    return request.app.state.task_store


def get_cs1_client(request: Request):
    return request.app.state.cs1_client


def get_cs3_client(request: Request):
    return request.app.state.cs3_client


def get_cs4_client(request: Request):
    return request.app.state.cs4_client


def get_portfolio_data_service(request: Request):
    return request.app.state.portfolio_data_service


def get_history_service(request: Request):
    return request.app.state.history_service


def get_fund_air_calculator(request: Request):
    return request.app.state.fund_air_calculator


from app.middleware.correlation import get_correlation_id


def get_correlation_id_dep(request: Request) -> str:
    return getattr(request.state, "correlation_id", get_correlation_id())
