"""ChromaDB vector store for PE evidence indexing.

Uses Chroma Cloud HTTP API directly — no chromadb Python package needed.
This avoids the onnxruntime/DLL hanging issue on Windows.

Storage options:
  OPTION A (current): Chroma Cloud HTTP API — shared across team
    Requires: CHROMA_API_KEY, CHROMA_TENANT, CHROMA_DATABASE in .env

  OPTION B (local): chromadb PersistentClient — local file storage
    Only use if Chroma Cloud is unavailable
    Requires chromadb package to be working

FIX (multi-dimension indexing):
  Previously each chunk was indexed once with only its primary dimension.
  Now each chunk is indexed once per relevant dimension (weight >= 0.15),
  so dimension-filtered searches always find content.
  Doc count grows from ~863 to ~1800-2200 for NVDA — expected and correct.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import requests

try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except Exception:
    _ST_AVAILABLE = False
    SentenceTransformer = None

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pe_evidence"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_BASE = "https://api.trychroma.com/api/v2"

# Dimensions below this weight threshold are too peripheral to index separately
MULTI_DIM_MIN_WEIGHT = 0.15


@dataclass
class SearchResult:
    doc_id: str
    content: str
    metadata: Dict[str, Any]
    score: float
    distance: float


def _build_chroma_client(persist_dir: str = "./chroma_data") -> Optional[Any]:
    """
    Build ChromaDB client — returns None since we use HTTP API directly.
    Kept for backward compatibility with hybrid.py imports.
    """
    return None


class VectorStore:
    """
    ChromaDB vector store using Chroma Cloud HTTP API directly.

    Bypasses the chromadb Python package entirely to avoid
    onnxruntime DLL issues on Windows.

    Uses sentence-transformers for embeddings locally,
    then sends vectors to Chroma Cloud via REST API.
    """

    def __init__(self, persist_dir: str = "./chroma_data"):
        self.persist_dir = persist_dir
        self._encoder: Optional[Any] = None
        self._collection_id: Optional[str] = None
        self._local_collection = None

        # Chroma Cloud credentials
        self._api_key = os.getenv("CHROMA_API_KEY", "")
        self._tenant = os.getenv("CHROMA_TENANT", "")
        self._database = os.getenv("CHROMA_DATABASE", "pe_org-air-platform")
        self._use_cloud = bool(self._api_key and self._tenant)

        self._init()

    def _init(self):
        # Initialize sentence transformer
        if _ST_AVAILABLE:
            try:
                self._encoder = SentenceTransformer(EMBEDDING_MODEL)
            except Exception as e:
                logger.warning("sentence_transformer_failed error=%s", e)

        if self._use_cloud:
            self._collection_id = self._ensure_collection()
            if self._collection_id:
                logger.info("chroma_cloud_connected collection_id=%s", self._collection_id)
            else:
                logger.warning("chroma_cloud_failed falling_back_to_local")
                self._use_cloud = False

        if not self._use_cloud:
            self._init_local()

    def _headers(self) -> Dict[str, str]:
        return {
            "x-chroma-token": self._api_key,
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return f"{CHROMA_BASE}/tenants/{self._tenant}/databases/{self._database}"

    def _ensure_collection(self) -> Optional[str]:
        """Get or create the pe_evidence collection in Chroma Cloud."""
        try:
            # Check existing collections
            resp = requests.get(
                f"{self._base_url()}/collections",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                for col in resp.json():
                    if col.get("name") == COLLECTION_NAME:
                        return col["id"]

            # Create collection
            resp = requests.post(
                f"{self._base_url()}/collections",
                headers=self._headers(),
                json={
                    "name": COLLECTION_NAME,
                    "metadata": {"hnsw:space": "cosine"},
                    "get_or_create": True,
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return resp.json()["id"]
            logger.warning("create_collection_failed status=%s", resp.status_code)
        except Exception as e:
            logger.warning("chroma_cloud_init_error error=%s", e)
        return None

    def _init_local(self):
        """Initialize local chromadb as fallback."""
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._local_collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("local_chromadb_initialized")
        except Exception as e:
            logger.warning("local_chromadb_failed error=%s", e)

    def _encode(self, texts: List[str]) -> List[List[float]]:
        if self._encoder is None:
            return [[0.0] * 384 for _ in texts]
        return self._encoder.encode(texts, show_progress_bar=False).tolist()

    def _cloud_upsert(self, ids, documents, embeddings, metadatas) -> bool:
        try:
            resp = requests.post(
                f"{self._base_url()}/collections/{self._collection_id}/upsert",
                headers=self._headers(),
                json={
                    "ids": ids,
                    "documents": documents,
                    "embeddings": embeddings,
                    "metadatas": metadatas,
                },
                timeout=60,
            )
            if resp.status_code in (200, 201):
                return True
            logger.warning("cloud_upsert_failed status=%s body=%s", resp.status_code, resp.text[:300])
            return False
        except Exception as e:
            logger.warning("cloud_upsert_error error=%s", e)
            return False

    def _cloud_count(self) -> int:
        try:
            resp = requests.get(
                f"{self._base_url()}/collections/{self._collection_id}/count",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return int(resp.json())
        except Exception:
            pass
        return 0

    def _cloud_query(self, query_embedding, n_results, where=None) -> Dict:
        body: Dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            body["where"] = where
        try:
            resp = requests.post(
                f"{self._base_url()}/collections/{self._collection_id}/query",
                headers=self._headers(),
                json=body,
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning("cloud_query_error error=%s", e)
        return {}

    def _cloud_delete(self, where: Dict) -> int:
        try:
            resp = requests.post(
                f"{self._base_url()}/collections/{self._collection_id}/get",
                headers=self._headers(),
                json={"where": where, "include": []},
                timeout=15,
            )
            if resp.status_code != 200:
                return 0
            ids = resp.json().get("ids", [])
            if not ids:
                return 0
            del_resp = requests.post(
                f"{self._base_url()}/collections/{self._collection_id}/delete",
                headers=self._headers(),
                json={"ids": ids},
                timeout=15,
            )
            return len(ids) if del_resp.status_code in (200, 201) else 0
        except Exception as e:
            logger.warning("cloud_delete_error error=%s", e)
            return 0

    # ── Core indexing method (FIXED: multi-dimension expansion) ───────────────

    def index_cs2_evidence(self, evidence_list: list, dimension_mapper: Any) -> int:
        """
        Index CS2Evidence objects into ChromaDB with multi-dimension expansion.

        Each chunk is stored once per dimension it significantly contributes to
        (weight >= MULTI_DIM_MIN_WEIGHT = 0.15), tagged with that dimension in
        metadata. This ensures dimension-filtered searches find content for all
        7 dimensions, not just the primary dimension of the signal category.

        Example: a sec_10k_item_1 chunk (signal=leadership_signals) is indexed
        under dimension=leadership (weight=0.45), dimension=use_case_portfolio
        (weight=0.25), AND dimension=ai_governance (weight=0.20) — three copies,
        each with a unique suffixed ID like  ev_abc123__leadership.

        Returns total number of vectors indexed (will be > len(evidence_list)
        when multi-dim expansion applies, which is expected).
        """
        if not evidence_list:
            return 0

        documents: List[str] = []
        metadatas: List[Dict] = []
        ids: List[str] = []
        seen_content_hashes: set = set()

        for ev in evidence_list:
            if not ev.content:
                continue

            # Deduplicate by content (first 2000 chars)
            content_snippet = ev.content[:2000]
            content_hash = hashlib.sha256(content_snippet.encode()).hexdigest()
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)

            # Get all dimensions this evidence contributes to
            dim_weights = dimension_mapper.get_dimension_weights(ev.signal_category)
            relevant_dims = {
                dim: weight
                for dim, weight in dim_weights.items()
                if weight >= MULTI_DIM_MIN_WEIGHT
            }
            # Always index under at least the primary dimension
            if not relevant_dims:
                primary = dimension_mapper.get_primary_dimension(ev.signal_category)
                relevant_dims = {primary: 1.0}

            base_id = ev.evidence_id or f"ev_{content_hash[:16]}"

            # One index entry per relevant dimension
            for dim, weight in relevant_dims.items():
                # Suffix ID only when multiple dimensions to avoid collisions
                doc_id = base_id if len(relevant_dims) == 1 else f"{base_id}__{dim}"

                meta = {
                    "evidence_id": ev.evidence_id or "",
                    "ticker": ev.company_id,          # company_id IS the ticker
                    "source_type": ev.source_type,
                    "signal_category": ev.signal_category,
                    "dimension": dim,                 # tagged with THIS dimension
                    "dimension_weight": float(weight),
                    "dimension_weights": json.dumps({k: v for k, v in dim_weights.items()}),
                    "confidence": float(ev.confidence),
                    "fiscal_year": str(ev.fiscal_year or ""),
                    "source_url": ev.source_url or "",
                    "page_number": str(ev.page_number or ""),
                }
                documents.append(content_snippet)
                ids.append(doc_id)
                metadatas.append(meta)

        if not documents:
            return 0

        # Encode all texts in one batch
        embeddings_list = self._encode(documents)
        batch_size = 100
        total_indexed = 0

        for i in range(0, len(documents), batch_size):
            b_ids = ids[i:i + batch_size]
            b_docs = documents[i:i + batch_size]
            b_embs = embeddings_list[i:i + batch_size]
            b_metas = metadatas[i:i + batch_size]

            if self._use_cloud and self._collection_id:
                if self._cloud_upsert(b_ids, b_docs, b_embs, b_metas):
                    total_indexed += len(b_ids)
                else:
                    logger.warning("cloud_upsert_batch_failed batch_start=%d", i)
            elif self._local_collection:
                try:
                    self._local_collection.upsert(
                        ids=b_ids,
                        documents=b_docs,
                        embeddings=b_embs,
                        metadatas=b_metas,
                    )
                    total_indexed += len(b_ids)
                except Exception as e:
                    logger.warning("local_upsert_failed error=%s", e)

        logger.info(
            "index_cs2_evidence_complete evidence_count=%d vectors_indexed=%d",
            len(seen_content_hashes),
            total_indexed,
        )
        return total_indexed

    # ── Other methods (unchanged from original) ───────────────────────────────

    def delete_by_filter(self, metadata_filter: dict) -> int:
        if self._use_cloud and self._collection_id:
            return self._cloud_delete(metadata_filter)
        elif self._local_collection:
            try:
                results = self._local_collection.get(where=metadata_filter, include=[])
                ids = results.get("ids", [])
                if ids:
                    self._local_collection.delete(ids=ids)
                return len(ids)
            except Exception:
                return 0
        return 0

    def search(
        self,
        query: str,
        top_k: int = 10,
        ticker: Optional[str] = None,
        dimension: Optional[str] = None,
        source_types: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> List[SearchResult]:
        """Dense vector search with optional metadata filters.

        NOTE: Chroma Cloud HTTP API silently ignores `where` filters (returns
        200 OK but applies no filtering).  We work around this by fetching a
        larger candidate set and applying all filters in Python after the fact.
        """
        has_filter = any([ticker, dimension, source_types, min_confidence > 0])

        query_emb = self._encode([query])[0]

        if self._use_cloud and self._collection_id:
            total = self._cloud_count()
            if total == 0:
                return []

            # Fetch a bigger candidate pool when filtering so we have enough
            # hits after client-side filtering.  30× gives good coverage up
            # to ~30k docs; cap at total to avoid Chroma Cloud errors.
            fetch_k = min(top_k * 30, total) if has_filter else min(top_k, total)

            # Do NOT pass `where` — Chroma Cloud silently ignores it.
            results = self._cloud_query(query_emb, fetch_k)
            if not results:
                return []

            output = []
            for doc_id, doc, meta, dist in zip(
                results.get("ids", [[]])[0],
                results.get("documents", [[]])[0],
                results.get("metadatas", [[]])[0],
                results.get("distances", [[]])[0],
            ):
                # Apply filters client-side
                if ticker and meta.get("ticker") != ticker:
                    continue
                if dimension and meta.get("dimension") != dimension:
                    continue
                if source_types and meta.get("source_type") not in source_types:
                    continue
                if min_confidence > 0 and float(meta.get("confidence", 0)) < min_confidence:
                    continue
                output.append(SearchResult(
                    doc_id=doc_id, content=doc,
                    metadata=meta, score=1.0 - dist, distance=dist,
                ))
            return output[:top_k]

        elif self._local_collection:
            try:
                count = self._local_collection.count()
                if count == 0:
                    return []
                # Build where clause for local chromadb (its filter works correctly)
                local_conditions = []
                if ticker:
                    local_conditions.append({"ticker": {"$eq": ticker}})
                if dimension:
                    local_conditions.append({"dimension": {"$eq": dimension}})
                if source_types:
                    local_conditions.append({"source_type": {"$in": source_types}})
                if min_confidence > 0:
                    local_conditions.append({"confidence": {"$gte": min_confidence}})
                local_where = None
                if len(local_conditions) > 1:
                    local_where = {"$and": local_conditions}
                elif len(local_conditions) == 1:
                    local_where = local_conditions[0]
                kwargs: Dict[str, Any] = {
                    "query_embeddings": [query_emb],
                    "n_results": min(top_k, count),
                    "include": ["documents", "metadatas", "distances"],
                }
                if local_where:
                    kwargs["where"] = local_where
                results = self._local_collection.query(**kwargs)
                return [
                    SearchResult(
                        doc_id=meta.get("source_url", "") or doc[:50],
                        content=doc, metadata=meta,
                        score=1.0 - dist, distance=dist,
                    )
                    for doc, meta, dist in zip(
                        results["documents"][0],
                        results["metadatas"][0],
                        results["distances"][0],
                    )
                ]
            except Exception as e:
                logger.warning("local_search_failed error=%s", e)

        return []

    def get_sample(
        self,
        limit: int = 10,
        ticker: Optional[str] = None,
    ) -> List["SearchResult"]:
        """Fetch documents by metadata without embeddings (uses /get endpoint).

        Used by rag_debug — does not require sentence-transformers to be loaded.
        """
        limit = min(limit, 300)  # Chroma Cloud free tier cap
        if self._use_cloud and self._collection_id:
            body: Dict[str, Any] = {
                "limit": limit,
                "include": ["documents", "metadatas"],
            }
            if ticker:
                body["where"] = {"ticker": {"$eq": ticker}}
            try:
                resp = requests.post(
                    f"{self._base_url()}/collections/{self._collection_id}/get",
                    headers=self._headers(),
                    json=body,
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ids = data.get("ids") or []
                    docs = data.get("documents") or [""] * len(ids)
                    metas = data.get("metadatas") or [{}] * len(ids)
                    return [
                        SearchResult(doc_id=id_, content=doc or "", metadata=meta, score=0.0, distance=0.0)
                        for id_, doc, meta in zip(ids, docs, metas)
                    ]
                logger.warning("cloud_get_sample_failed status=%s body=%s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning("cloud_get_sample_error error=%s", e)
        elif self._local_collection:
            try:
                kwargs: Dict[str, Any] = {
                    "limit": limit,
                    "include": ["documents", "metadatas"],
                }
                if ticker:
                    kwargs["where"] = {"ticker": {"$eq": ticker}}
                results = self._local_collection.get(**kwargs)
                ids = results.get("ids") or []
                docs = results.get("documents") or [""] * len(ids)
                metas = results.get("metadatas") or [{}] * len(ids)
                return [
                    SearchResult(doc_id=id_, content=doc or "", metadata=meta, score=0.0, distance=0.0)
                    for id_, doc, meta in zip(ids, docs, metas)
                ]
            except Exception as e:
                logger.warning("local_get_sample_error error=%s", e)
        return []

    def wipe(self) -> int:
        if self._use_cloud and self._collection_id:
            count = self._cloud_count()
            try:
                requests.delete(
                    f"{self._base_url()}/collections/{COLLECTION_NAME}",
                    headers=self._headers(),
                    timeout=15,
                )
                self._collection_id = self._ensure_collection()
            except Exception as e:
                logger.warning("cloud_wipe_failed error=%s", e)
            return count
        elif self._local_collection:
            try:
                count = self._local_collection.count()
                self._local_collection.delete(where={"ticker": {"$ne": ""}})
                return count
            except Exception:
                return 0
        return 0

    def count(self) -> int:
        if self._use_cloud and self._collection_id:
            return self._cloud_count()
        elif self._local_collection:
            try:
                return self._local_collection.count()
            except Exception:
                return 0
        return 0

    def get_all_metadata(self) -> List[Dict[str, Any]]:
        """Paginate through entire collection, returning only metadata dicts.

        Uses /get endpoint — no embeddings required.
        Chroma Cloud free tier: max 300 per request, so paginates automatically.
        """
        all_metas: List[Dict[str, Any]] = []
        if self._use_cloud and self._collection_id:
            offset = 0
            batch = 300
            while True:
                try:
                    resp = requests.post(
                        f"{self._base_url()}/collections/{self._collection_id}/get",
                        headers=self._headers(),
                        json={"limit": batch, "offset": offset, "include": ["metadatas"]},
                        timeout=30,
                    )
                    if resp.status_code != 200:
                        logger.warning("get_all_metadata_failed status=%s", resp.status_code)
                        break
                    data = resp.json()
                    ids = data.get("ids") or []
                    metas = data.get("metadatas") or [{}] * len(ids)
                    if not ids:
                        break
                    all_metas.extend(metas)
                    offset += len(ids)
                    if len(ids) < batch:
                        break
                except Exception as e:
                    logger.warning("get_all_metadata_error error=%s", e)
                    break
        elif self._local_collection:
            try:
                results = self._local_collection.get(include=["metadatas"])
                all_metas = results.get("metadatas") or []
            except Exception as e:
                logger.warning("local_get_all_metadata_error error=%s", e)
        return all_metas

    def get_all_documents(self) -> List["SearchResult"]:
        """Paginate through entire collection, returning IDs + content + metadata.

        Same pagination strategy as get_all_metadata() but includes documents.
        Used by HybridRetriever.rebuild_sparse_index_from_chroma().
        """
        all_docs: List[SearchResult] = []
        if self._use_cloud and self._collection_id:
            offset = 0
            batch = 300
            while True:
                try:
                    resp = requests.post(
                        f"{self._base_url()}/collections/{self._collection_id}/get",
                        headers=self._headers(),
                        json={"limit": batch, "offset": offset, "include": ["documents", "metadatas"]},
                        timeout=30,
                    )
                    if resp.status_code != 200:
                        logger.warning("get_all_documents_failed status=%s", resp.status_code)
                        break
                    data = resp.json()
                    ids = data.get("ids") or []
                    documents = data.get("documents") or [""] * len(ids)
                    metas = data.get("metadatas") or [{}] * len(ids)
                    if not ids:
                        break
                    for doc_id, content, meta in zip(ids, documents, metas):
                        all_docs.append(SearchResult(
                            doc_id=doc_id,
                            content=content or "",
                            metadata=meta or {},
                            score=0.0,
                            distance=0.0,
                        ))
                    offset += len(ids)
                    if len(ids) < batch:
                        break
                except Exception as e:
                    logger.warning("get_all_documents_error error=%s", e)
                    break
        elif self._local_collection:
            try:
                results = self._local_collection.get(include=["documents", "metadatas"])
                ids = results.get("ids") or []
                documents = results.get("documents") or [""] * len(ids)
                metas = results.get("metadatas") or [{}] * len(ids)
                for doc_id, content, meta in zip(ids, documents, metas):
                    all_docs.append(SearchResult(
                        doc_id=doc_id,
                        content=content or "",
                        metadata=meta or {},
                        score=0.0,
                        distance=0.0,
                    ))
            except Exception as e:
                logger.warning("local_get_all_documents_error error=%s", e)
        return all_docs
