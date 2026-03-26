"""
Services — PE Org-AI-R Platform
app/services/

Subdirectory layout rule:
  Top-level flat  — single-class services with no domain siblings.
  Subdirectory    — services that share a domain, have utilities, or need
                    their own namespace (e.g. signals/, retrieval/, llm/).
"""

# NOTE:
# Keep this package import-light. Some entrypoints (e.g. the MCP server) import
# modules under `app.services.*` but should not require Redis/S3 to be configured
# just to import the package.


def get_cache():
    from app.services.cache import get_cache as _get_cache
    return _get_cache()


def get_s3_service():
    from app.services.s3_storage import get_s3_service as _get_s3_service
    return _get_s3_service()


def get_snowflake_connection():
    """Lazy import — defers snowflake-connector-python import until first DB call."""
    from app.repositories.base import get_snowflake_connection as _get
    return _get()


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


def __getattr__(name: str):
    if name == "RedisCache":
        from app.services.redis_cache import RedisCache
        return RedisCache
    raise AttributeError(name)



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
