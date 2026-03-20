"""Analyst Notes Collector — Post-LOI DD notes indexer.

Persistence strategy:
  - S3:        Raw note content stored at analyst_notes/{company_id}/{note_id}.json
  - Snowflake: Structured metadata in ANALYST_NOTES table (queryable)
  - ChromaDB:  Indexed via HybridRetriever for RAG retrieval
  - In-memory: _notes cache for fast same-session lookups

On server restart: in-memory cache is empty but Snowflake + S3 + ChromaDB persist.
Call load_from_snowflake(company_id) to restore in-memory cache for a company.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from app.services.retrieval.hybrid import HybridRetriever, RetrievedDocument
from app.services.retrieval.dimension_mapper import DimensionMapper
from app.services.s3_storage import get_s3_service
from app.repositories.base import BaseRepository

import snowflake.connector

logger = logging.getLogger(__name__)

NOTE_TYPES = [
    "interview_transcript",
    "management_meeting",
    "site_visit",
    "dd_finding",
    "data_room_summary",
]

SEVERITY_LEVELS = ["critical", "high", "medium", "low"]


@dataclass
class AnalystNote:
    note_id: str
    company_id: str
    note_type: str
    content: str
    dimension: str
    assessor: str
    confidence: float = 1.0  # Primary sources = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    s3_key: Optional[str] = None


class AnalystNotesRepository(BaseRepository):
    """Snowflake persistence for analyst notes metadata."""

    TABLE_NAME = "ANALYST_NOTES"

    def insert(self, note: AnalystNote) -> None:
        """Insert a new analyst note row into Snowflake."""
        sql = """
        INSERT INTO ANALYST_NOTES (
            note_id, company_id, note_type, dimension,
            assessor, confidence, s3_key, metadata, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), %s)
        """
        params = (
            note.note_id,
            note.company_id,
            note.note_type,
            note.dimension,
            note.assessor,
            note.confidence,
            note.s3_key or "",
            json.dumps(note.metadata, default=str),
            note.created_at,
        )
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                conn.commit()
            finally:
                cur.close()

    def get_by_company(self, company_id: str) -> List[Dict]:
        """Fetch all notes for a company from Snowflake."""
        sql = """
        SELECT note_id, company_id, note_type, dimension,
               assessor, confidence, s3_key, metadata, created_at
        FROM ANALYST_NOTES
        WHERE company_id = %s
        ORDER BY created_at DESC
        """
        with self.get_connection() as conn:
            cur = conn.cursor(snowflake.connector.DictCursor)
            try:
                cur.execute(sql, (company_id,))
                return [self._row_to_dict(r) for r in cur.fetchall()]
            finally:
                cur.close()

    def get_by_id(self, note_id: str) -> Optional[Dict]:
        """Fetch a single note by ID from Snowflake."""
        sql = """
        SELECT note_id, company_id, note_type, dimension,
               assessor, confidence, s3_key, metadata, created_at
        FROM ANALYST_NOTES
        WHERE note_id = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor(snowflake.connector.DictCursor)
            try:
                cur.execute(sql, (note_id,))
                row = cur.fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                cur.close()

    @staticmethod
    def _row_to_dict(row: Dict) -> Dict:
        return {
            "note_id":    row.get("NOTE_ID"),
            "company_id": row.get("COMPANY_ID"),
            "note_type":  row.get("NOTE_TYPE"),
            "dimension":  row.get("DIMENSION"),
            "assessor":   row.get("ASSESSOR"),
            "confidence": float(row["CONFIDENCE"]) if row.get("CONFIDENCE") else 1.0,
            "s3_key":     row.get("S3_KEY"),
            "metadata":   row.get("METADATA") or {},
            "created_at": row.get("CREATED_AT"),
        }


class AnalystNotesCollector:
    """Indexes post-LOI DD notes into ChromaDB, Snowflake, and S3.

    Persistence layers:
      - S3:        Raw content at analyst_notes/{company_id}/{note_id}.json
      - Snowflake: Structured metadata in ANALYST_NOTES (queryable)
      - ChromaDB:  Indexed for RAG retrieval via HybridRetriever
      - Memory:    _notes cache for fast same-session access
    """

    def __init__(self, retriever: Optional[HybridRetriever] = None):
        self.retriever = retriever or HybridRetriever()
        self.mapper = DimensionMapper()
        self._notes: Dict[str, AnalystNote] = {}
        self._s3 = get_s3_service()
        try:
            self._repo = AnalystNotesRepository()
            self._snowflake_ok = True
        except Exception as e:
            logger.warning("Snowflake unavailable for analyst notes: %s", e)
            self._repo = None
            self._snowflake_ok = False

    # ------------------------------------------------------------------
    # Public submission methods
    # ------------------------------------------------------------------

    def submit_interview(
        self,
        company_id: str,
        interviewee: str,
        interviewee_title: str,
        transcript: str,
        assessor: str,
        dimensions_discussed: Optional[List[str]] = None,
    ) -> str:
        """Index an interview transcript. Returns note_id."""
        note_id = str(uuid.uuid4())
        primary_dim = (dimensions_discussed or ["leadership"])[0]
        meta = {
            "interviewee": interviewee,
            "interviewee_title": interviewee_title,
            "note_type": "interview_transcript",
            "assessor": assessor,
            "dimensions_discussed": ",".join(dimensions_discussed or []),
            "company_id": company_id,
            "dimension": primary_dim,
            "confidence": 1.0,
        }
        note = AnalystNote(
            note_id=note_id,
            company_id=company_id,
            note_type="interview_transcript",
            content=transcript,
            dimension=primary_dim,
            assessor=assessor,
            metadata=meta,
        )
        self._persist(note)
        return note_id

    def submit_dd_finding(
        self,
        company_id: str,
        title: str,
        finding: str,
        dimension: str,
        severity: str,
        assessor: str,
    ) -> str:
        """Index a DD finding. Returns note_id."""
        note_id = str(uuid.uuid4())
        content = f"[{severity.upper()}] {title}\n\n{finding}"
        meta = {
            "title": title,
            "severity": severity,
            "note_type": "dd_finding",
            "assessor": assessor,
            "company_id": company_id,
            "dimension": dimension,
            "confidence": 1.0,
        }
        note = AnalystNote(
            note_id=note_id,
            company_id=company_id,
            note_type="dd_finding",
            content=content,
            dimension=dimension,
            assessor=assessor,
            metadata=meta,
        )
        self._persist(note)
        return note_id

    def submit_data_room_summary(
        self,
        company_id: str,
        document_name: str,
        summary: str,
        dimension: str,
        assessor: str,
    ) -> str:
        """Index a data room document summary. Returns note_id."""
        note_id = str(uuid.uuid4())
        content = f"Data Room Document: {document_name}\n\n{summary}"
        meta = {
            "document_name": document_name,
            "note_type": "data_room_summary",
            "assessor": assessor,
            "company_id": company_id,
            "dimension": dimension,
            "confidence": 1.0,
        }
        note = AnalystNote(
            note_id=note_id,
            company_id=company_id,
            note_type="data_room_summary",
            content=content,
            dimension=dimension,
            assessor=assessor,
            metadata=meta,
        )
        self._persist(note)
        return note_id

    # ------------------------------------------------------------------
    # Retrieval helpers
    # ------------------------------------------------------------------

    def get_note(self, note_id: str) -> Optional[AnalystNote]:
        """Get note from memory cache first, then Snowflake fallback."""
        if note_id in self._notes:
            return self._notes[note_id]
        # Fallback to Snowflake
        if self._snowflake_ok and self._repo:
            row = self._repo.get_by_id(note_id)
            if row:
                return self._row_to_note(row)
        return None

    def list_notes(self, company_id: str) -> List[AnalystNote]:
        """List notes for a company — memory first, Snowflake fallback."""
        memory_notes = [n for n in self._notes.values() if n.company_id == company_id]
        if memory_notes:
            return memory_notes
        return self.load_from_snowflake(company_id)

    def load_from_snowflake(self, company_id: str) -> List[AnalystNote]:
        """Load notes from Snowflake into memory cache and re-index in ChromaDB."""
        if not self._snowflake_ok or not self._repo:
            return []
        rows = self._repo.get_by_company(company_id)
        notes = []
        for row in rows:
            # Fetch raw content from S3
            content = ""
            if row.get("s3_key"):
                raw = self._s3.get_file(row["s3_key"])
                if raw:
                    try:
                        data = json.loads(raw)
                        content = data.get("content", "")
                    except Exception:
                        content = raw.decode("utf-8", errors="ignore")

            note = AnalystNote(
                note_id=row["note_id"],
                company_id=row["company_id"],
                note_type=row["note_type"],
                content=content,
                dimension=row["dimension"],
                assessor=row["assessor"],
                confidence=row["confidence"],
                metadata=row["metadata"],
                created_at=str(row.get("created_at", "")),
                s3_key=row.get("s3_key"),
            )
            self._notes[note.note_id] = note
            if content:
                self._index_note(note)
            notes.append(note)
        return notes

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _persist(self, note: AnalystNote) -> None:
        """Persist to S3, Snowflake, ChromaDB, and memory cache."""
        # 1. S3 — raw content
        s3_key = self._save_to_s3(note)
        note.s3_key = s3_key

        # 2. Snowflake — structured metadata
        if self._snowflake_ok and self._repo:
            try:
                self._repo.insert(note)
            except Exception as e:
                logger.warning("snowflake_insert_failed note_id=%s error=%s", note.note_id, e)

        # 3. ChromaDB — RAG indexing
        self._index_note(note)

        # 4. Memory cache
        self._notes[note.note_id] = note

    def _save_to_s3(self, note: AnalystNote) -> str:
        """Save note content to S3. Returns s3_key."""
        # Path: analyst_notes/{company_id}/{note_id}.json
        s3_key = f"analyst_notes/{note.company_id}/{note.note_id}.json"
        payload = {
            "note_id": note.note_id,
            "company_id": note.company_id,
            "note_type": note.note_type,
            "dimension": note.dimension,
            "assessor": note.assessor,
            "confidence": note.confidence,
            "content": note.content,
            "metadata": note.metadata,
            "created_at": note.created_at,
        }
        try:
            self._s3.upload_json(payload, s3_key)
        except Exception as e:
            logger.warning("s3_upload_failed note_id=%s error=%s", note.note_id, e)
        return s3_key

    def _index_note(self, note: AnalystNote) -> None:
        """Index note into ChromaDB via HybridRetriever."""
        doc = RetrievedDocument(
            doc_id=note.note_id,
            content=note.content,
            metadata=note.metadata,
            score=1.0,
            retrieval_method="direct",
        )
        try:
            self.retriever.index_documents([doc])
        except Exception as e:
            logger.warning("chroma_index_failed note_id=%s error=%s", note.note_id, e)

    @staticmethod
    def _row_to_note(row: Dict) -> AnalystNote:
        return AnalystNote(
            note_id=row["note_id"],
            company_id=row["company_id"],
            note_type=row["note_type"],
            content="",  # content lives in S3, not Snowflake
            dimension=row["dimension"],
            assessor=row["assessor"],
            confidence=row["confidence"],
            metadata=row.get("metadata") or {},
            created_at=str(row.get("created_at", "")),
            s3_key=row.get("s3_key"),
        )