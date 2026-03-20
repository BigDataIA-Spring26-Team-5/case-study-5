"""
Scoring Service — CS3 Pipeline Orchestrator
app/services/scoring_service.py

Pipeline (9 evidence sources):
  1. 4 CS2 signal scores (hiring, innovation, digital, leadership)
  2. 3 SEC rubric scores (item 1, 1a, 7)  
  2.5a. 1 board governance (from board_analyzer)
  2.5b. 1 culture signal (from culture_collector)
  → EvidenceMapper → 7 DimensionScores → Snowflake

Changes in this version:
  - Added _fetch_board_governance() and _fetch_culture_signal()
  - GE fix: fallback to ALL chunks when section-matched text < 3000 words
"""

import json
import time
import uuid
import structlog
from decimal import Decimal
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from app.scoring.evidence_mapper import (
    EvidenceMapper, EvidenceScore, SignalSource, Dimension,
)
from app.scoring.rubric_scorer import RubricScorer
from app.repositories.scoring_repository import ScoringRepository
from app.repositories.signal_repository import SignalRepository
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError, PipelineIncompleteError

logger = structlog.get_logger()

# Minimum words needed for reliable rubric scoring
_MIN_SECTION_WORDS = 3000


class ScoringService:
    """Orchestrates the CS3 scoring pipeline."""

    SEC_SECTION_MAP = {
        "sec_item_1": ["business", "item_1_business", "item_1"],
        "sec_item_1a": ["risk_factors", "item_1a_risk_factors", "item_1a"],
        "sec_item_7": ["mda", "item_7_mda", "item_7"],
    }

    SEC_RUBRIC_MAP = {
        "sec_item_1": "use_case_portfolio",
        "sec_item_1a": "ai_governance",
        "sec_item_7": "leadership_vision",
    }

    REQUIRED_SIGNAL_CATEGORIES = [
        "technology_hiring",
        "innovation_activity",
        "digital_presence",
        "leadership_signals",
    ]

    def __init__(self, company_repo=None, scoring_repo=None, signal_repo=None,
                 document_repo=None, chunk_repo=None):
        self.mapper = EvidenceMapper()
        self.rubric_scorer = RubricScorer()
        self.scoring_repo = scoring_repo or ScoringRepository()
        self.signal_repo = signal_repo or SignalRepository()
        self.company_repo = company_repo or CompanyRepository()
        self.document_repo = document_repo or DocumentRepository()
        self.chunk_repo = chunk_repo or ChunkRepository()
        self._s3_chunk_cache: Dict[str, List[Dict]] = {}

    def check_scoring_prerequisites(self, ticker: str) -> Dict[str, Any]:
        """Check whether all CS2 data required for scoring exists."""
        available = self.signal_repo.get_signal_categories_for_ticker(ticker)
        missing = [f"signal:{c}" for c in self.REQUIRED_SIGNAL_CATEGORIES if c not in available]

        chunk_count = self.document_repo.get_chunk_count_for_ticker(ticker)
        if chunk_count == 0:
            missing.append("document_chunks")

        return {
            "ready": len(missing) == 0,
            "missing": missing,
            "available_signals": available,
            "chunk_count": chunk_count,
        }

    def score_company(self, ticker: str) -> Dict[str, Any]:
        """Full scoring pipeline for a company."""
        ticker = ticker.upper()
        self._s3_chunk_cache.clear()

        start = time.perf_counter()
        logger.info("scoring_started", ticker=ticker)

        # Phase 3B prerequisite check
        prereqs = self.check_scoring_prerequisites(ticker)
        logger.info("scoring_prerequisites_checked", ticker=ticker, ready=prereqs["ready"])
        if not prereqs["ready"]:
            raise PipelineIncompleteError(ticker=ticker, missing_steps=prereqs["missing"])

        # Phase 3A run tracking
        run_id = str(uuid.uuid4())
        self.scoring_repo.create_scoring_run(run_id, ticker)
        try:
            result = self._run_scoring_pipeline(ticker)
            dimensions_written = len(result.get("dimension_scores") or [])
            self.scoring_repo.complete_scoring_run(run_id, dimensions_written)
            for row in (result.get("dimension_scores") or []):
                logger.info("dimension_scored", ticker=ticker,
                            dimension=row["dimension"], score=row["score"])
            duration = time.perf_counter() - start
            logger.info("scoring_completed", ticker=ticker,
                        duration_seconds=round(duration, 2))
            return result
        except Exception as exc:
            self.scoring_repo.fail_scoring_run(run_id, str(exc))
            raise

    def _run_scoring_pipeline(self, ticker: str) -> Dict[str, Any]:
        """Internal: execute the full scoring pipeline (already upper-cased ticker)."""
        company = self.company_repo.get_by_ticker(ticker)
        if not company:
            raise NotFoundError("company", ticker)
        company_id = str(company["id"])

        # Step 1: CS2 signals
        cs2_evidence = self._fetch_cs2_signals(company_id, ticker)
        logger.info("pipeline_step", ticker=ticker, step="cs2_signals",
                    evidence_count=len(cs2_evidence))

        # Step 2: SEC rubric scores
        sec_evidence, sec_details = self._fetch_and_score_sec_sections(ticker)
        logger.info("pipeline_step", ticker=ticker, step="sec_rubric",
                    evidence_count=len(sec_evidence))

        # Step 2.5a: Board governance
        board_evidence = self._fetch_board_governance(ticker)
        logger.info("pipeline_step", ticker=ticker, step="board_governance",
                    found=board_evidence is not None,
                    score=float(board_evidence.raw_score) if board_evidence else None)

        # Step 2.5b: Culture signal
        culture_evidence = self._fetch_culture_signal(ticker)
        logger.info("pipeline_step", ticker=ticker, step="culture_signal",
                    found=culture_evidence is not None,
                    score=float(culture_evidence.raw_score) if culture_evidence else None)

        # Step 3: Combine all evidence
        all_evidence = cs2_evidence + sec_evidence
        if board_evidence:
            all_evidence.append(board_evidence)
        if culture_evidence:
            all_evidence.append(culture_evidence)
        logger.info("pipeline_step", ticker=ticker, step="evidence_combined",
                    total_sources=len(all_evidence))

        # Step 4: Map to dimensions
        dim_scores = self.mapper.map_evidence_to_dimensions(all_evidence)

        # Step 5: Build outputs
        mapping_matrix = self.mapper.build_mapping_matrix(all_evidence, ticker)
        dimension_summary = self.mapper.build_dimension_summary(all_evidence, ticker)
        coverage = self.mapper.get_coverage_report(all_evidence)

        # Step 6: Persist
        persisted = False
        try:
            self.scoring_repo.upsert_mapping_matrix(mapping_matrix)
            self.scoring_repo.upsert_dimension_scores(dimension_summary)
            persisted = True
            logger.info("pipeline_step", ticker=ticker, step="persisted",
                        mapping_rows=len(mapping_matrix), dimension_scores=len(dimension_summary))
        except Exception as e:
            logger.error("persistence_failed", ticker=ticker, error=str(e))

        result = {
            "ticker": ticker,
            "company_id": company_id,
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "mapping_matrix": mapping_matrix,
            "dimension_scores": dimension_summary,
            "coverage": {dim.value: info for dim, info in coverage.items()},
            "evidence_sources": {
                "cs2_signals": len(cs2_evidence),
                "sec_sections": len(sec_evidence),
                "board_composition": board_evidence is not None,
                "glassdoor_reviews": culture_evidence is not None,
                "sec_details": sec_details,
                "total": len(all_evidence),
            },
            "persisted": persisted,
        }

        return result

    def score_all_companies(self) -> List[Dict[str, Any]]:
        """Score all companies that have CS2 signal data."""
        summaries = self.signal_repo.get_all_summaries()
        results = []
        for summary in summaries:
            ticker = summary.get("ticker")
            if not ticker:
                continue
            try:
                result = self.score_company(ticker)
                results.append(result)
            except Exception as e:
                logger.error("score_company_failed", ticker=ticker, error=str(e))
                results.append({"ticker": ticker, "error": str(e), "persisted": False})
        return results

    # ------------------------------------------------------------------
    # CS2 signals
    # ------------------------------------------------------------------

    def _fetch_cs2_signals(self, company_id: str, ticker: str) -> List[EvidenceScore]:
        summary = self.signal_repo.get_summary_by_ticker(ticker)
        if not summary:
            logger.warning("no_signal_summary", ticker=ticker)
            return []

        evidence = []
        signal_map = {
            "technology_hiring_score": SignalSource.TECHNOLOGY_HIRING,
            "innovation_activity_score": SignalSource.INNOVATION_ACTIVITY,
            "digital_presence_score": SignalSource.DIGITAL_PRESENCE,
            "leadership_signals_score": SignalSource.LEADERSHIP_SIGNALS,
        }
        
        SIGNAL_CONFIDENCE = {
            SignalSource.TECHNOLOGY_HIRING:   Decimal("0.80"),
            SignalSource.INNOVATION_ACTIVITY: Decimal("0.85"),
            SignalSource.DIGITAL_PRESENCE:    Decimal("0.85"),
            SignalSource.LEADERSHIP_SIGNALS:  Decimal("0.80"),  # was 0.65 — raised for v3 analyzer
        }

        signals = self.signal_repo.get_signals_by_company(company_id)
        category_counts = {}
        for s in signals:
            cat = s.get("category", "")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        for score_key, source_enum in signal_map.items():
            score_val = summary.get(score_key)
            if score_val is not None:
                ev_count = category_counts.get(source_enum.value, 1)
                evidence.append(EvidenceScore(
                    source=source_enum,
                    raw_score=Decimal(str(round(float(score_val), 2))),
                    # confidence=Decimal("0.85"),
                    # confidence=Decimal("0.75") if source_enum == SignalSource.LEADERSHIP_SIGNALS else Decimal("0.85"),
                    confidence=SIGNAL_CONFIDENCE.get(source_enum, Decimal("0.80")),
                    evidence_count=ev_count,
                    metadata={"from": "company_signal_summaries"},
                ))
                logger.info("cs2_signal_loaded", source=source_enum.value, score=round(float(score_val), 1))
            else:
                logger.warning("cs2_signal_missing", source=source_enum.value)

        return evidence

    # ------------------------------------------------------------------
    # Board governance from S3
    # ------------------------------------------------------------------

    def _fetch_board_governance(self, ticker: str) -> Optional[EvidenceScore]:
        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()

            prefix = f"signals/board_composition/{ticker.upper()}/"
            keys = s3.list_files(prefix)
            if not keys:
                return None

            latest_key = sorted(keys)[-1]
            raw = s3.get_file(latest_key)
            if raw is None:
                return None

            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            gov_score = data.get("governance_score")
            if gov_score is None:
                return None

            confidence = data.get("confidence", 0.85)
            meta = {
                "from": f"s3://{latest_key}",
                "has_tech_committee": data.get("has_tech_committee", False),
                "has_ai_expertise": data.get("has_ai_expertise", False),
                "has_data_officer": data.get("has_data_officer", False),
                "has_risk_tech_oversight": data.get("has_risk_tech_oversight", False),
                "has_ai_in_strategy": data.get("has_ai_in_strategy", False),
                "independent_ratio": data.get("independent_ratio"),
                "tech_expertise_count": data.get("tech_expertise_count", 0),
                "board_members": data.get("board_member_count", 0),
            }

            logger.info("board_governance_loaded", ticker=ticker, score=gov_score,
                        tech_committee=meta['has_tech_committee'],
                        ai_expertise=meta['has_ai_expertise'],
                        data_officer=meta['has_data_officer'])

            return EvidenceScore(
                source=SignalSource.BOARD_COMPOSITION,
                raw_score=Decimal(str(round(float(gov_score), 2))),
                confidence=Decimal(str(round(float(confidence), 3))),
                evidence_count=1,
                metadata=meta,
            )
        except Exception as e:
            logger.warning("board_governance_load_failed", ticker=ticker, error=str(e))
            return None

    # ------------------------------------------------------------------
    # Culture signal from S3
    # ------------------------------------------------------------------

    def _fetch_culture_signal(self, ticker: str) -> Optional[EvidenceScore]:
        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()

            ticker_upper = ticker.upper()
            data = None
            source_key = None

            # Attempt 1: timestamped subfolder
            prefix = f"glassdoor_signals/output/{ticker_upper}/"
            keys = s3.list_files(prefix)
            if keys:
                latest_key = sorted(keys)[-1]
                raw = s3.get_file(latest_key)
                if raw is not None:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    source_key = latest_key

            # Attempt 2: flat file
            if data is None:
                flat_key = f"glassdoor_signals/output/{ticker_upper}_culture.json"
                raw = s3.get_file(flat_key)
                if raw is not None:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    source_key = flat_key

            if data is None:
                return None

            overall = data.get("overall_score")
            if overall is None:
                return None

            confidence = data.get("confidence", 0.70)
            review_count = data.get("review_count", 0)

            meta = {
                "from": f"s3://{source_key}",
                "innovation_score": data.get("innovation_score"),
                "data_driven_score": data.get("data_driven_score"),
                "ai_awareness_score": data.get("ai_awareness_score"),
                "change_readiness_score": data.get("change_readiness_score"),
                "review_count": review_count,
                "avg_rating": data.get("avg_rating"),
                "current_employee_ratio": data.get("current_employee_ratio"),
                "source_breakdown": data.get("source_breakdown", {}),
            }

            logger.info("culture_signal_loaded", ticker=ticker, score=overall,
                        review_count=review_count,
                        innovation=meta['innovation_score'],
                        data_driven=meta['data_driven_score'],
                        ai_awareness=meta['ai_awareness_score'],
                        change_readiness=meta['change_readiness_score'])

            return EvidenceScore(
                source=SignalSource.GLASSDOOR_REVIEWS,
                raw_score=Decimal(str(round(float(overall), 2))),
                confidence=Decimal(str(round(float(confidence), 3))),
                evidence_count=max(1, review_count),
                metadata=meta,
            )
        except Exception as e:
            logger.warning("culture_signal_load_failed", ticker=ticker, error=str(e))
            return None

    # ------------------------------------------------------------------
    # SEC sections + rubric scoring
    # ------------------------------------------------------------------

    def _fetch_and_score_sec_sections(
        self, ticker: str
    ) -> tuple[List[EvidenceScore], Dict[str, Any]]:
        evidence = []
        details = {}

        # One Snowflake connection for all three SEC section lookups
        s3_keys_by_section = self.chunk_repo.get_s3_keys_for_section_map(
            ticker, self.SEC_SECTION_MAP
        )

        for signal_source_key, section_names in self.SEC_SECTION_MAP.items():
            section_text = self._get_section_text(
                ticker, section_names, s3_keys=s3_keys_by_section.get(signal_source_key)
            )

            # ── GE FIX: fallback to ALL chunks if section text too short ──
            # GE Aerospace's 10-K chunks may have NULL or non-standard
            # section names, resulting in only ~1600 words per section.
            # When text is too short for reliable rubric scoring, fall back
            # to ALL chunks from that filing to get more signal.
            if section_text and len(section_text.split()) < _MIN_SECTION_WORDS:
                logger.info("sec_section_too_short", ticker=ticker, section=signal_source_key,
                            word_count=len(section_text.split()), threshold=_MIN_SECTION_WORDS)
                fallback_text = self._get_all_chunks_text(ticker)
                if fallback_text and len(fallback_text.split()) > len(section_text.split()):
                    section_text = fallback_text
                    logger.info("sec_section_fallback_used", ticker=ticker,
                                section=signal_source_key, word_count=len(section_text.split()))

            if not section_text:
                # Also try all-chunks fallback when no section text at all
                fallback_text = self._get_all_chunks_text(ticker)
                if fallback_text:
                    section_text = fallback_text
                    logger.info("sec_section_fallback_used", ticker=ticker,
                                section=signal_source_key, word_count=len(section_text.split()))

            if not section_text:
                logger.warning("sec_section_not_found", ticker=ticker, section=signal_source_key)
                details[signal_source_key] = {"found": False, "word_count": 0}
                continue

            word_count = len(section_text.split())
            logger.info("sec_section_loaded", ticker=ticker, section=signal_source_key,
                        word_count=word_count)

            rubric_dimension = self.SEC_RUBRIC_MAP[signal_source_key]
            rubric_result = self.rubric_scorer.score_dimension(
                dimension=rubric_dimension,
                evidence_text=section_text,
            )

            source_enum = SignalSource(signal_source_key)
            evidence.append(EvidenceScore(
                source=source_enum,
                raw_score=rubric_result.score,
                confidence=rubric_result.confidence,
                evidence_count=1,
                metadata={
                    "rubric_dimension": rubric_dimension,
                    "rubric_level": rubric_result.level.label,
                    "matched_keywords": rubric_result.matched_keywords[:10],
                    "word_count": word_count,
                    "rationale": rubric_result.rationale,
                },
            ))

            details[signal_source_key] = {
                "found": True,
                "word_count": word_count,
                "rubric_score": float(rubric_result.score),
                "rubric_level": rubric_result.level.label,
                "rubric_confidence": float(rubric_result.confidence),
                "matched_keywords": rubric_result.matched_keywords[:10],
            }

            logger.info("sec_rubric_scored", ticker=ticker, section=signal_source_key,
                        rubric_dimension=rubric_dimension,
                        score=float(rubric_result.score), level=rubric_result.level.label)

        return evidence, details

    def _get_section_text(
        self,
        ticker: str,
        section_names: List[str],
        s3_keys: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Get concatenated section text from S3 chunk files."""
        if s3_keys is None:
            s3_keys = self.chunk_repo.get_s3_keys_by_sections(ticker, section_names)

        if not s3_keys:
            return None

        logger.info("s3_section_files_found", ticker=ticker, file_count=len(s3_keys))

        section_names_lower = {s.lower() for s in section_names}
        text_parts = []

        for s3_key in s3_keys:
            chunks = self._load_chunks_from_s3(s3_key)
            if not chunks:
                continue
            for chunk in chunks:
                chunk_section = (chunk.get("section") or "").lower()
                if chunk_section in section_names_lower:
                    content = chunk.get("content", "")
                    if content and content.strip():
                        text_parts.append(content)

        if text_parts:
            combined = "\n\n".join(text_parts)
            logger.info("s3_chunks_extracted", ticker=ticker, chunk_count=len(text_parts),
                        word_count=len(combined.split()))
            return combined

        return None

    def _get_all_chunks_text(self, ticker: str) -> Optional[str]:
        """
        Fallback: get ALL 10-K chunk text for a ticker regardless of section.
        Used when section-specific extraction yields too little text (e.g. GE).
        Cached per ticker to avoid re-downloading across multiple section queries.
        """
        cache_key = f"__all_chunks_{ticker}"
        if cache_key in self._s3_chunk_cache:
            chunks = self._s3_chunk_cache[cache_key]
            if chunks:
                return "\n\n".join(c.get("content", "") for c in chunks if c.get("content"))
            return None

        s3_keys = self.chunk_repo.get_all_s3_keys(ticker)

        if not s3_keys:
            self._s3_chunk_cache[cache_key] = []
            return None

        all_chunks = []
        for s3_key in s3_keys:
            chunks = self._load_chunks_from_s3(s3_key)
            all_chunks.extend(chunks)

        self._s3_chunk_cache[cache_key] = all_chunks

        if all_chunks:
            text = "\n\n".join(c.get("content", "") for c in all_chunks if c.get("content"))
            logger.info("all_chunks_fallback_loaded", ticker=ticker,
                        chunk_count=len(all_chunks), word_count=len(text.split()))
            return text

        return None

    def _load_chunks_from_s3(self, s3_key: str) -> List[Dict]:
        """Download a chunks JSON file from S3."""
        if s3_key in self._s3_chunk_cache:
            return self._s3_chunk_cache[s3_key]

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()

            data = s3.get_file(s3_key)
            if data is None:
                logger.warning("s3_file_not_found", s3_key=s3_key)
                self._s3_chunk_cache[s3_key] = []
                return []

            text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
            parsed = json.loads(text)

            if isinstance(parsed, list):
                chunks = parsed
            elif isinstance(parsed, dict):
                if "chunks" in parsed:
                    chunks = parsed["chunks"]
                elif "content" in parsed:
                    chunks = [parsed]
                else:
                    chunks = []
            else:
                chunks = []

            logger.info("s3_chunks_loaded", s3_key=s3_key, chunk_count=len(chunks))
            self._s3_chunk_cache[s3_key] = chunks
            return chunks

        except json.JSONDecodeError as e:
            logger.warning("s3_json_parse_failed", s3_key=s3_key, error=str(e))
            self._s3_chunk_cache[s3_key] = []
            return []
        except Exception as e:
            logger.warning("s3_load_failed", s3_key=s3_key, error=str(e))
            self._s3_chunk_cache[s3_key] = []
            return []


get_scoring_service = make_singleton_factory(ScoringService)
