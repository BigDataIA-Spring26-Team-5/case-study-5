"""Hybrid Retriever — Dense (Chroma Cloud HTTP) + Sparse (BM25) + RRF fusion.

Uses VectorStore for dense search (which uses Chroma Cloud HTTP API directly)
to avoid onnxruntime/chromadb DLL issues on Windows.

FIX (original): BM25 corpus seeded from ChromaDB on startup via broad sampling.
     Previously _load_bm25_from_store() was a no-op (pass), meaning BM25 never
     fired and every retrieval was dense-only.

FIX (ticker-scoped BM25 — broken):
     _seed_ticker() tried to fetch ticker-specific docs from Chroma Cloud but
     Chroma Cloud silently ignores where-filters on query requests, returning
     random docs from all tickers. GOOGL was marked as seeded but BM25 never
     actually got GOOGL's SEC chunks.

FIX (this session — direct evidence seeding):
     Added seed_from_evidence(evidence_list) which seeds BM25 directly from
     the CS2Evidence objects returned by cs2_client.get_evidence(). Called from
     rag.py index endpoint after vs.index_cs2_evidence(). No Chroma query needed.
     _seed_ticker() is now a no-op stub.
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

try:
    from rank_bm25 import BM25Okapi
    _BM25_AVAILABLE = True
except ImportError:
    _BM25_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except Exception:
    _ST_AVAILABLE = False
    SentenceTransformer = None

from app.services.search.vector_store import VectorStore, SearchResult

logger = logging.getLogger(__name__)

# How many docs to pull from ChromaDB to seed BM25 at startup.
BM25_SEED_LIMIT = 500

# If fewer than this many ticker-specific docs are in the BM25 corpus,
# trigger a per-ticker re-seed before sparse scoring.
BM25_TICKER_MIN_DOCS = 50

# Pickle cache for BM25 corpus
BM25_PICKLE_PATH = os.environ.get("BM25_PICKLE_PATH", "/tmp/bm25_cache.pkl")
BM25_PICKLE_TTL = 86400  # 24 hours

# How many ticker-specific docs to fetch when re-seeding for a ticker.
BM25_TICKER_SEED_K = 200

# Broad seed queries — rotated to maximise vocabulary coverage when seeding BM25.
_SEED_QUERIES = [
    "AI machine learning data infrastructure technology governance talent",
    "revenue growth strategy investment risk compliance board",
    "cloud platform engineering pipeline data quality analytics",
    "leadership executive officer director management team culture",
    "patent innovation research development product deployment",
]

# Ticker-specific seed query — kept for reference, no longer used by _seed_ticker()
_TICKER_SEED_QUERY = (
    "AI technology data infrastructure governance talent leadership "
    "machine learning strategy innovation board SEC filing"
)


@dataclass
class RetrievedDocument:
    doc_id: str
    content: str
    metadata: Dict[str, Any]
    score: float
    retrieval_method: str  # "dense", "sparse", "hybrid", or "seed"


class HybridRetriever:
    """Combines dense (Chroma Cloud) + sparse (BM25) retrieval with RRF fusion.

    Dense search uses VectorStore which calls Chroma Cloud HTTP API directly.
    No chromadb Python package required.
    """

    def __init__(
        self,
        dense_weight: float = 0.6,
        sparse_weight: float = 0.4,
        rrf_k: int = 60,
        persist_dir: str = "./chroma_data",
        vector_store: Optional["VectorStore"] = None,
    ):
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.rrf_k = rrf_k
        self.persist_dir = persist_dir

        # Dense index via VectorStore (uses Chroma Cloud HTTP API)
        self._vector_store = vector_store or VectorStore(persist_dir=persist_dir)

        # Sparse index
        self._bm25: Optional[Any] = None
        self._doc_store: List[RetrievedDocument] = []
        self._tokenized_corpus: List[List[str]] = []

        # Track which tickers have already been seeded into BM25
        self._seeded_tickers: Set[str] = set()

        # Try pickle first, then fall back to ChromaDB seeding
        if not self._try_load_pickle():
            self._load_bm25_from_store()

    @property
    def sparse_index_size(self) -> int:
        """Number of documents in the BM25 sparse index."""
        return len(self._doc_store)

    # ── Pickle persistence ─────────────────────────────────────────────────────

    def _try_load_pickle(self) -> bool:
        path = Path(BM25_PICKLE_PATH)
        if not path.exists():
            logger.debug("bm25_pickle_miss reason=file_not_found path=%s", BM25_PICKLE_PATH)
            return False
        age = time.time() - path.stat().st_mtime
        if age > BM25_PICKLE_TTL:
            logger.info("bm25_pickle_expired age_seconds=%.0f ttl=%d", age, BM25_PICKLE_TTL)
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._doc_store = data["doc_store"]
            self._tokenized_corpus = data["tokenized_corpus"]
            if _BM25_AVAILABLE and self._tokenized_corpus:
                self._bm25 = BM25Okapi(self._tokenized_corpus)
            logger.info("bm25_pickle_loaded doc_count=%d age_seconds=%.0f", len(self._doc_store), age)
            return True
        except Exception as e:
            logger.warning("bm25_pickle_load_failed error=%s", e)
            return False

    def _save_pickle(self) -> None:
        if not self._doc_store:
            return
        try:
            data = {"doc_store": self._doc_store, "tokenized_corpus": self._tokenized_corpus}
            with open(BM25_PICKLE_PATH, "wb") as f:
                pickle.dump(data, f)
            logger.info("bm25_pickle_saved doc_count=%d path=%s", len(self._doc_store), BM25_PICKLE_PATH)
        except Exception as e:
            logger.warning("bm25_pickle_save_failed error=%s", e)

    # ── Startup global seed ────────────────────────────────────────────────────

    def _load_bm25_from_store(self):
        """
        Seed BM25 corpus from ChromaDB at startup.

        Runs multiple broad queries and unions the results to maximise
        vocabulary coverage. Capped at BM25_SEED_LIMIT total unique docs.
        """
        if not _BM25_AVAILABLE:
            logger.warning("bm25_unavailable rank_bm25 not installed")
            return

        total = self._vector_store.count()
        if total == 0:
            logger.info("bm25_seed_skipped reason=empty_vector_store")
            return

        seen_ids: set = set()
        docs: List[RetrievedDocument] = []

        per_query_k = max(BM25_SEED_LIMIT // len(_SEED_QUERIES), 50)

        for seed_query in _SEED_QUERIES:
            if len(docs) >= BM25_SEED_LIMIT:
                break
            try:
                results = self._vector_store.search(
                    query=seed_query,
                    top_k=per_query_k,
                )
                for r in results:
                    if r.doc_id not in seen_ids:
                        seen_ids.add(r.doc_id)
                        docs.append(RetrievedDocument(
                            doc_id=r.doc_id,
                            content=r.content,
                            metadata=r.metadata,
                            score=r.score,
                            retrieval_method="dense",
                        ))
                        if len(docs) >= BM25_SEED_LIMIT:
                            break
            except Exception as e:
                logger.warning("bm25_seed_query_failed query=%s error=%s", seed_query[:30], e)

        if docs:
            self._doc_store = docs
            self._tokenized_corpus = [d.content.lower().split() for d in docs]
            self._bm25 = BM25Okapi(self._tokenized_corpus)
            logger.info("bm25_seeded doc_count=%d", len(docs))
            self._save_pickle()
        else:
            logger.warning("bm25_seed_empty no_docs_fetched")

    # ── Direct evidence seeding (PRIMARY FIX) ─────────────────────────────────

    def seed_from_evidence(self, evidence_list: list) -> None:
        """
        Seed BM25 corpus directly from CS2Evidence objects.

        This is the correct replacement for the broken _seed_ticker() approach.
        Chroma Cloud silently ignores where-filters on query requests, so
        _seed_ticker() was fetching random docs from all tickers and marking
        GOOGL as seeded without actually adding GOOGL's SEC chunks.

        This method seeds directly from the evidence list that was just indexed,
        guaranteeing BM25 has exactly the same content as Chroma.

        Called from rag.py index_company_evidence() after vs.index_cs2_evidence().

        Args:
            evidence_list: List[CS2Evidence] from cs2_client.get_evidence()
        """
        if not _BM25_AVAILABLE or not evidence_list:
            return

        from app.services.retrieval.dimension_mapper import DimensionMapper
        _dm = DimensionMapper()

        existing_ids = {d.doc_id for d in self._doc_store}
        new_docs: List[RetrievedDocument] = []

        for ev in evidence_list:
            if not ev.content:
                continue
            doc_id = ev.evidence_id or f"ev_{ev.content[:16]}"
            if doc_id in existing_ids:
                continue
            existing_ids.add(doc_id)

            # Derive primary dimension from signal category for BM25 filtering
            try:
                primary_dim = _dm.get_primary_dimension(ev.signal_category)
                dim_str = primary_dim if isinstance(primary_dim, str) else str(primary_dim)
            except Exception:
                dim_str = ""

            new_docs.append(RetrievedDocument(
                doc_id=doc_id,
                content=ev.content,
                metadata={
                    "ticker": ev.company_id,
                    "source_type": ev.source_type,
                    "signal_category": ev.signal_category,
                    "dimension": dim_str,
                    "confidence": ev.confidence,
                },
                score=0.0,
                retrieval_method="seed",
            ))

        if not new_docs:
            logger.info(
                "bm25_seed_from_evidence_no_new_docs evidence_count=%d",
                len(evidence_list),
            )
            return

        self._doc_store.extend(new_docs)
        self._tokenized_corpus = [d.content.lower().split() for d in self._doc_store]
        self._bm25 = BM25Okapi(self._tokenized_corpus)

        # Mark tickers as seeded so _seed_ticker() won't interfere
        tickers = {ev.company_id for ev in evidence_list if ev.company_id}
        self._seeded_tickers.update(tickers)

        logger.info(
            "bm25_seeded_from_evidence added=%d total_corpus=%d tickers=%s",
            len(new_docs), len(self._doc_store), tickers,
        )
        self._save_pickle()

    # ── Per-ticker lazy seed (DEPRECATED — now a no-op) ───────────────────────

    def _seed_ticker(self, ticker: str) -> None:
        """
        Deprecated — replaced by seed_from_evidence().

        Chroma Cloud HTTP API silently ignores where-filters on query requests,
        so this method was fetching random docs from all tickers and falsely
        marking the ticker as seeded. seed_from_evidence() is the correct fix.
        """
        self._seeded_tickers.add(ticker)
        logger.debug(
            "bm25_seed_ticker_noop ticker=%s reason=chroma_cloud_ignores_filters",
            ticker,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def rebuild_sparse_index_from_chroma(self) -> int:
        """Rebuild BM25 sparse index from ALL ChromaDB documents.

        Unlike _load_bm25_from_store() which only samples ~500 docs via seed queries,
        this fetches every document in the collection for complete coverage.
        Call on startup to restore full hybrid retrieval capability.
        Returns the number of documents indexed.
        """
        if not _BM25_AVAILABLE:
            logger.warning("rebuild_sparse_skipped reason=bm25_unavailable")
            return 0

        # Uses get_all_documents() instead of collection.get() — retrieves complete corpus
        # including metadata, not just a seed-query subset. This is intentionally more
        # comprehensive than the plan's original collection.get() approach.
        all_results = self._vector_store.get_all_documents()
        if not all_results:
            logger.info("rebuild_sparse_skipped reason=no_documents_in_chroma")
            return 0

        self._doc_store = [
            RetrievedDocument(
                doc_id=r.doc_id,
                content=r.content,
                metadata=r.metadata,
                score=0.0,
                retrieval_method="chroma_rebuild",
            )
            for r in all_results
            if r.content
        ]
        self._tokenized_corpus = [d.content.lower().split() for d in self._doc_store]
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        self._seeded_tickers = {
            d.metadata.get("ticker")
            for d in self._doc_store
            if d.metadata.get("ticker")
        }
        self._save_pickle()
        logger.info("rebuild_sparse_complete doc_count=%d", len(self._doc_store))
        return len(self._doc_store)

    def refresh_sparse_index(self):
        """
        Rebuild BM25 from the current _doc_store.

        Does NOT wipe the doc store — just rebuilds the BM25 index from
        whatever is already accumulated. This prevents the bug where
        indexing ticker B would erase ticker A's BM25 data.

        If _doc_store is empty (fresh startup), seeds from ChromaDB.
        """
        logger.info("bm25_refresh_start doc_count=%d", len(self._doc_store))
        if not self._doc_store:
            self._load_bm25_from_store()
        else:
            self._tokenized_corpus = [
                d.content.lower().split() for d in self._doc_store
            ]
            if _BM25_AVAILABLE and self._tokenized_corpus:
                self._bm25 = BM25Okapi(self._tokenized_corpus)
            self._save_pickle()
        logger.info("bm25_refresh_complete doc_count=%d", len(self._doc_store))

    def _encode(self, texts: List[str]) -> List[List[float]]:
        return self._vector_store._encode(texts)

    def index_documents(self, documents: List[RetrievedDocument]) -> int:
        """Index documents into both dense and sparse indices."""
        if not documents:
            return 0

        self._doc_store.extend(documents)
        self._tokenized_corpus = [
            doc.content.lower().split() for doc in self._doc_store
        ]
        if _BM25_AVAILABLE and self._tokenized_corpus:
            self._bm25 = BM25Okapi(self._tokenized_corpus)

        if self._vector_store._use_cloud and self._vector_store._collection_id:
            texts = [d.content[:2000] for d in documents]
            ids = [d.doc_id for d in documents]
            metas = [d.metadata for d in documents]
            embeddings = self._encode(texts)
            batch = 100
            for i in range(0, len(texts), batch):
                self._vector_store._cloud_upsert(
                    ids[i:i+batch],
                    texts[i:i+batch],
                    embeddings[i:i+batch],
                    metas[i:i+batch],
                )

        return len(documents)

    def retrieve(
        self,
        query: str,
        k: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievedDocument]:
        """Retrieve top-k documents using RRF-fused hybrid search."""
        n_candidates = k * 5
        dense_results = self._dense_search(query, n_candidates, filter_metadata)
        sparse_results = self._sparse_search(query, n_candidates, filter_metadata)
        return self._rrf_fusion(dense_results, sparse_results, k)

    # ── Search internals ───────────────────────────────────────────────────────

    def _dense_search(
        self,
        query: str,
        k: int,
        filter_metadata: Optional[Dict[str, Any]],
    ) -> List[RetrievedDocument]:
        """Dense search via VectorStore (Chroma Cloud HTTP API)."""
        ticker = filter_metadata.get("ticker") if filter_metadata else None
        dimension = filter_metadata.get("dimension") if filter_metadata else None
        source_types = filter_metadata.get("source_type") if filter_metadata else None
        if isinstance(source_types, str):
            source_types = [source_types]

        if filter_metadata and "$and" in filter_metadata:
            for clause in filter_metadata["$and"]:
                if "ticker" in clause:
                    ticker = ticker or clause["ticker"]
                if "dimension" in clause:
                    dimension = dimension or clause["dimension"]
                if "source_type" in clause:
                    st = clause["source_type"]
                    source_types = source_types or (st.get("$in") if isinstance(st, dict) else [st])

        results = self._vector_store.search(
            query=query,
            top_k=k,
            ticker=ticker,
            dimension=dimension,
            source_types=source_types,
        )

        return [
            RetrievedDocument(
                doc_id=r.doc_id,
                content=r.content,
                metadata=r.metadata,
                score=r.score,
                retrieval_method="dense",
            )
            for r in results
        ]

    def _sparse_search(
        self,
        query: str,
        k: int,
        filter_metadata: Optional[Dict[str, Any]],
    ) -> List[RetrievedDocument]:
        """BM25 sparse search over in-memory doc store."""
        if self._bm25 is None or not self._doc_store:
            return []

        flat_filter = self._flatten_filter(filter_metadata)

        # Ticker-scoped check — _seed_ticker() is now a no-op.
        # Real seeding happens via seed_from_evidence() called from rag.py.
        ticker = flat_filter.get("ticker")
        if ticker and _BM25_AVAILABLE:
            ticker_count = sum(
                1 for d in self._doc_store
                if d.metadata.get("ticker") == ticker
            )
            if ticker_count < BM25_TICKER_MIN_DOCS:
                logger.info(
                    "bm25_ticker_under_represented ticker=%s corpus_count=%d "
                    "hint=call_seed_from_evidence_after_indexing",
                    ticker, ticker_count,
                )
                # _seed_ticker is a no-op now — log and continue
                self._seed_ticker(ticker)

        tokens = query.lower().split()
        scores = self._bm25.get_scores(tokens)
        ranked_idx = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )

        results = []
        for idx in ranked_idx:
            if len(results) >= k:
                break
            if scores[idx] <= 0:
                break
            doc = self._doc_store[idx]
            if flat_filter and not self._matches_filter(doc.metadata, flat_filter):
                continue
            results.append(RetrievedDocument(
                doc_id=doc.doc_id,
                content=doc.content,
                metadata=doc.metadata,
                score=float(scores[idx]),
                retrieval_method="sparse",
            ))
        return results

    def _rrf_fusion(
        self,
        dense: List[RetrievedDocument],
        sparse: List[RetrievedDocument],
        k: int,
    ) -> List[RetrievedDocument]:
        """Reciprocal Rank Fusion."""
        rrf_scores: Dict[str, float] = {}
        doc_map: Dict[str, RetrievedDocument] = {}

        for rank, doc in enumerate(dense):
            rrf_scores[doc.doc_id] = rrf_scores.get(doc.doc_id, 0.0) + (
                self.dense_weight / (self.rrf_k + rank + 1)
            )
            doc_map[doc.doc_id] = doc

        for rank, doc in enumerate(sparse):
            rrf_scores[doc.doc_id] = rrf_scores.get(doc.doc_id, 0.0) + (
                self.sparse_weight / (self.rrf_k + rank + 1)
            )
            if doc.doc_id not in doc_map:
                doc_map[doc.doc_id] = doc

        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]
        return [
            RetrievedDocument(
                doc_id=did,
                content=doc_map[did].content,
                metadata=doc_map[did].metadata,
                score=score,
                retrieval_method="hybrid",
            )
            for did, score in ranked
        ]

    # ── Filter helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_filter(filter_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Flatten a nested $and filter into a simple key→value dict."""
        if not filter_metadata:
            return {}
        if "$and" not in filter_metadata:
            return filter_metadata
        flat: Dict[str, Any] = {}
        for clause in filter_metadata["$and"]:
            for k, v in clause.items():
                if k.startswith("$"):
                    continue
                if isinstance(v, dict) and "$in" in v:
                    flat[k] = v["$in"]
                elif isinstance(v, dict) and "$eq" in v:
                    flat[k] = v["$eq"]
                else:
                    flat[k] = v
        return flat

    @staticmethod
    def _build_where(filter_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not filter_metadata:
            return {}
        conditions = []
        for k, v in filter_metadata.items():
            if isinstance(v, list):
                conditions.append({k: {"$in": v}})
            else:
                conditions.append({k: {"$eq": v}})
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _matches_filter(metadata: Dict[str, Any], filter_metadata: Dict[str, Any]) -> bool:
        for k, v in filter_metadata.items():
            val = metadata.get(k)
            if isinstance(v, list):
                if val not in v:
                    return False
            elif val != v:
                return False
        return True
