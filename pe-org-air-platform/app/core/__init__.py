"""
Core Package - PE Org-AI-R Platform
app/core/__init__.py

Core infrastructure: dependencies, exceptions.
"""

__all__ = [
    # Dependencies
    "get_assessment_repository",
    "get_company_repository",
    "get_dimension_score_repository",
    "get_industry_repository",
]


def __getattr__(name: str):
    """Lazy-load dependency providers to avoid repository import cycles."""
    if name in {
        "get_assessment_repository",
        "get_company_repository",
        "get_dimension_score_repository",
        "get_industry_repository",
    }:
        from app.core import dependencies as _dependencies

        return getattr(_dependencies, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
