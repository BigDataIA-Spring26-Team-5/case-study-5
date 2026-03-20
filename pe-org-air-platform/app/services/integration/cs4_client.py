"""CS4 Client — Justification and RAG from the PE Org-AI-R platform."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

from app.services.justification.generator import (
    JustificationGenerator,
    ScoreJustification,
)
from app.services.retrieval.hybrid import HybridRetriever, RetrievedDocument
from app.services.integration.cs3_client import DIMENSIONS

logger = logging.getLogger(__name__)


@dataclass
class JustificationResult:
    """Simplified justification result for MCP / agent consumption."""
    company_id: str
    dimension: str
    score: float
    level: int
    level_name: str
    summary: str
    evidence_count: int
    evidence_strength: str
    gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CS4Client:
    """Wraps JustificationGenerator + HybridRetriever for CS5 consumption."""

    def __init__(
        self,
        justification_generator: JustificationGenerator,
        hybrid_retriever: HybridRetriever,
    ):
        self.generator = justification_generator
        self.retriever = hybrid_retriever

    def generate_justification(
        self, ticker: str, dimension: str
    ) -> JustificationResult:
        """Generate an evidence-backed justification for a single dimension."""
        justification: ScoreJustification = self.generator.generate_justification(
            ticker, dimension
        )
        return JustificationResult(
            company_id=justification.company_id,
            dimension=justification.dimension,
            score=justification.score,
            level=justification.level,
            level_name=justification.level_name,
            summary=justification.generated_summary,
            evidence_count=len(justification.supporting_evidence),
            evidence_strength=justification.evidence_strength,
            gaps=justification.gaps_identified,
        )

    def generate_all_justifications(
        self, ticker: str
    ) -> Dict[str, JustificationResult]:
        """Generate justifications for all 7 dimensions."""
        results: Dict[str, JustificationResult] = {}
        for dim in DIMENSIONS:
            try:
                results[dim] = self.generate_justification(ticker, dim)
            except Exception as e:
                logger.warning(
                    "justification_failed ticker=%s dimension=%s error=%s",
                    ticker, dim, e,
                )
        return results

    def search_evidence(
        self,
        query: str,
        ticker: Optional[str] = None,
        k: int = 10,
    ) -> List[Dict[str, Any]]:
        """RAG search over indexed evidence."""
        filter_meta: Dict[str, Any] = {}
        if ticker:
            filter_meta["ticker"] = ticker.upper()
        results: List[RetrievedDocument] = self.retriever.retrieve(
            query, k=k, filter_metadata=filter_meta
        )
        return [
            {
                "doc_id": r.doc_id,
                "content": r.content[:500],
                "score": r.score,
                "source_type": r.metadata.get("source_type", ""),
                "dimension": r.metadata.get("dimension", ""),
            }
            for r in results
        ]
