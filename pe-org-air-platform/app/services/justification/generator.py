"""Score Justification Generator — cited evidence for IC-ready summaries."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

from app.services.integration.cs3_client import (
    DimensionScore, RubricCriteria, get_rubric_static, score_to_level, _DIM_ALIAS_MAP,
)
from app.services.retrieval.hybrid import HybridRetriever, RetrievedDocument
from app.services.llm.router import ModelRouter
from app.prompts.rag_prompts import JUSTIFICATION_SYSTEM, JUSTIFICATION_TEMPLATE


@dataclass
class CitedEvidence:
    evidence_id: str
    content: str  # ≤500 chars
    source_type: str
    source_url: str
    confidence: float
    matched_keywords: List[str]
    relevance_score: float


@dataclass
class ScoreJustification:
    company_id: str
    dimension: str
    score: float
    level: int
    level_name: str
    confidence_interval: tuple[float, float]
    rubric_criteria: str
    rubric_keywords: List[str]
    supporting_evidence: List[CitedEvidence]
    gaps_identified: List[str]
    generated_summary: str
    evidence_strength: str  # "strong", "moderate", "weak"


class JustificationGenerator:
    """Generates IC-ready score justifications with cited evidence."""

    def __init__(
        self,
        scoring_repo=None,
        retriever: Optional[HybridRetriever] = None,
        router: Optional[ModelRouter] = None,
    ):
        if scoring_repo is None:
            from app.repositories.scoring_repository import ScoringRepository
            scoring_repo = ScoringRepository()
        self.scoring_repo = scoring_repo
        self.retriever = retriever or HybridRetriever()
        self.router = router or ModelRouter()

    def generate_justification(
        self, ticker: str, dimension: str
    ) -> ScoreJustification:
        """Full pipeline: fetch score → retrieve evidence → generate summary."""
        # Step 1: Fetch dimension score directly from DB (no HTTP)
        rows = self.scoring_repo.get_dimension_scores(ticker)
        dim_score = self._find_dim_score(rows, dimension)
        if dim_score is None:
            dim_score = DimensionScore(
                dimension=dimension, score=50.0, level=3, level_name="Adequate"
            )

        # Step 2: Get rubric criteria from local static data (no HTTP)
        rubric = get_rubric_static(dimension, dim_score.level)
        rubric_text = rubric[0].criteria if rubric else f"Level {dim_score.level} criteria"
        rubric_keywords = rubric[0].keywords if rubric else dim_score.rubric_keywords[:5]

        # Step 3: Build search query from rubric keywords
        query = f"{dimension} " + " ".join(rubric_keywords[:5])

        # Step 4: Retrieve evidence
        # ChromaDB stores short dimension names; map API names to stored names
        _DIM_NORMALIZE = {
            "talent_skills": "talent", "talent_management": "talent",
            "leadership_vision": "leadership",
            "culture_change": "culture",
        }
        chroma_dim = _DIM_NORMALIZE.get(dimension, dimension)
        filter_meta: Dict[str, Any] = {"ticker": ticker}
        if chroma_dim:
            filter_meta["dimension"] = chroma_dim
        raw_results = self.retriever.retrieve(query, k=15, filter_metadata=filter_meta)

        # Step 5: Match to rubric keywords
        cited = self._match_to_rubric(raw_results, rubric_keywords)

        # Step 6: Identify gaps for next level (local static data)
        next_level = min(dim_score.level + 1, 5)
        next_rubric = get_rubric_static(dimension, next_level)
        gaps = self._identify_gaps(cited, next_rubric)

        # Step 7: Call DeepSeek for IC summary
        ci = dim_score.confidence_interval
        evidence_text = "\n".join(
            f"- [{e.source_type}] {e.content[:1000]}..." for e in cited[:5]
        ) or "No specific evidence retrieved."
        gaps_text = "\n".join(f"- {g}" for g in gaps) or "None identified."

        logger.debug(
            "LLM evidence_text for %s/%s (%d pieces):\n%s",
            ticker, dimension, len(cited), evidence_text
        )
        prompt = JUSTIFICATION_TEMPLATE.format(
            company_id=ticker,
            dimension=dimension,
            score=dim_score.score,
            level=dim_score.level,
            level_name=dim_score.level_name,
            ci_low=ci[0] if ci else 0.0,
            ci_high=ci[1] if ci else 0.0,
            rubric_criteria=rubric_text,
            n_evidence=len(cited),
            evidence_text=evidence_text,
            gaps_text=gaps_text,
            next_level=next_level,
        )
        messages = [
            {"role": "system", "content": JUSTIFICATION_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        try:
            summary = self.router.complete_sync("justification_generation", messages)
        except Exception as e:
            summary = f"[Summary generation unavailable: {e}]"
        summary = self._verify_citations(summary, cited)

        return ScoreJustification(
            company_id=ticker,
            dimension=dimension,
            score=dim_score.score,
            level=dim_score.level,
            level_name=dim_score.level_name,
            confidence_interval=dim_score.confidence_interval or (0.0, 0.0),
            rubric_criteria=rubric_text,
            rubric_keywords=rubric_keywords,
            supporting_evidence=cited,
            gaps_identified=gaps,
            generated_summary=summary.strip(),
            evidence_strength=self._assess_strength(cited),
        )

    @staticmethod
    def _find_dim_score(rows: list, dimension: str) -> Optional[DimensionScore]:
        """Find dimension score from repo rows using alias-aware matching."""
        if not rows:
            return None
        dim_norm = _DIM_ALIAS_MAP.get(dimension, dimension)
        for row in rows:
            row_norm = _DIM_ALIAS_MAP.get(row["dimension"], row["dimension"])
            if row["dimension"] == dimension or row_norm == dim_norm:
                score = float(row.get("score", 50.0))
                level, level_name = score_to_level(score)
                return DimensionScore(
                    dimension=row["dimension"],
                    score=score,
                    level=level,
                    level_name=level_name,
                )
        return None

    @staticmethod
    def _match_to_rubric(
        results: List[RetrievedDocument], keywords: List[str]
    ) -> List[CitedEvidence]:
        """Filter and rank evidence by keyword overlap and relevance."""
        cited = []
        for r in results:
            content_lower = r.content.lower()
            matched = [kw for kw in keywords if kw.lower() in content_lower]
            cited.append(
                CitedEvidence(
                    evidence_id=r.doc_id,
                    content=r.content[:1000],
                    source_type=r.metadata.get("source_type", ""),
                    source_url=r.metadata.get("source_url", ""),
                    confidence=float(r.metadata.get("confidence", 0.5)),
                    matched_keywords=matched,
                    relevance_score=r.score,
                )
            )
        return sorted(cited, key=lambda x: x.relevance_score, reverse=True)

    @staticmethod
    def _verify_citations(summary: str, cited: List[CitedEvidence]) -> str:
        """Append a note if evidence is empty or summary references phantom source types."""
        if not cited:
            return summary + (
                "\n\n[Note: No supporting evidence was retrieved. "
                "Summary is based on scoring metadata only.]"
            )
        valid_types = {e.source_type for e in cited if e.source_type}
        mentioned = set(re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', summary))
        phantom = mentioned - valid_types
        if phantom:
            return summary + (
                f"\n\n[Verification note: Summary references source type(s) "
                f"{sorted(phantom)} not present in retrieved evidence.]"
            )
        return summary

    @staticmethod
    def _identify_gaps(
        cited: List[CitedEvidence], next_rubric: List[RubricCriteria]
    ) -> List[str]:
        """Find next-level criteria not covered by current evidence."""
        if not next_rubric:
            return []
        found_keywords: set = set()
        for ev in cited:
            found_keywords.update(k.lower() for k in ev.matched_keywords)
        gaps = []
        for rubric in next_rubric:
            for kw in rubric.keywords:
                if kw.lower() not in found_keywords:
                    gaps.append(f"Missing evidence of: {kw} ({rubric.criteria[:80]})")
        return gaps[:5]  # Top 5 gaps

    @staticmethod
    def _assess_strength(cited: List[CitedEvidence]) -> str:
        if len(cited) >= 5 and any(e.confidence >= 0.7 for e in cited):
            return "strong"
        if len(cited) >= 2:
            return "moderate"
        return "weak"
