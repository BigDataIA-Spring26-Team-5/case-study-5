"""
Services — PE Org-AI-R Platform
app/services/

Subdirectory layout rule:
  Top-level flat  — single-class services with no domain siblings.
  Subdirectory    — services that share a domain, have utilities, or need
                    their own namespace (e.g. signals/, retrieval/, llm/).
"""

from app.services.cache import get_cache
from app.services.redis_cache import RedisCache
from app.services.s3_storage import get_s3_service
from app.repositories.base import get_snowflake_connection


def get_document_collector_service():
    """Lazy import to avoid circular dependency."""
    from app.services.document_collector import get_document_collector_service as _get
    return _get()


def get_document_chunking_service():
    """Lazy import to avoid circular dependency."""
    from app.services.document_chunking_service import get_document_chunking_service as _get
    return _get()


def get_document_parsing_service():
    """Lazy import to avoid circular dependency."""
    from app.services.document_parsing_service import get_document_parsing_service as _get
    return _get()


def get_leadership_service():
    """Lazy import to avoid circular dependency."""
    from app.services.signals.leadership_service import get_leadership_service as _get
    return _get()


def get_job_data_service():
    """Lazy import to avoid circular dependency."""
    from app.services.signals.job_data_service import get_job_data_service as _get
    return _get()


def get_job_signal_service():
    """Lazy import to avoid circular dependency."""
    from app.services.signals.job_signal_service import get_job_signal_service as _get
    return _get()


def get_tech_signal_service():
    """Lazy import to avoid circular dependency."""
    from app.services.signals.tech_signal_service import get_tech_signal_service as _get
    return _get()


def get_patent_signal_service():
    """Lazy import to avoid circular dependency."""
    from app.services.signals.patent_signal_service import get_patent_signal_service as _get
    return _get()



__all__ = [
    # Core services
    "get_cache",
    "get_document_chunking_service",
    "get_document_collector_service",
    "get_document_parsing_service",
    "get_leadership_service",
    "RedisCache",
    "get_s3_service",
    "get_snowflake_connection",

    # Data services
    "get_job_data_service",

    # Signal services
    "get_job_signal_service",
    "get_tech_signal_service",
    "get_patent_signal_service",
]
