"""Retrieval pipeline settings — single source of truth for magic numbers.

These constants control the BM25 seeding, hybrid retrieval, and RAG context
limits. Previously scattered across hybrid.py, rag.py, and other files.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalSettings:
    """Documented settings for the retrieval pipeline."""

    # BM25 seeding
    bm25_seed_limit: int = 500
    """Max unique docs to pull from ChromaDB to seed BM25 at startup."""

    bm25_ticker_min_docs: int = 50
    """If fewer than this many ticker-specific docs in BM25 corpus, re-seed."""

    bm25_ticker_seed_k: int = 200
    """Docs to fetch when re-seeding for a specific ticker."""

    # Hybrid retrieval (RRF fusion)
    rrf_k: int = 60
    """Reciprocal Rank Fusion smoothing parameter."""

    dense_weight: float = 0.6
    """Weight for dense (vector) retrieval in hybrid fusion."""

    sparse_weight: float = 0.4
    """Weight for sparse (BM25) retrieval in hybrid fusion."""

    # RAG context limits
    max_context_chars: int = 6000
    """Maximum characters of context to send to the LLM."""


# Singleton instance
RETRIEVAL_SETTINGS = RetrievalSettings()
