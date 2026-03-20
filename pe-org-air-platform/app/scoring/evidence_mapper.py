"""
Evidence-to-Dimension Mapper — Task 5.0a (CS3)
app/scoring/evidence_mapper.py

Maps CS2 evidence (4 external signals + 3 SEC sections) to 7 V^R dimensions
using the Signal-to-Dimension Mapping Matrix (Table 1, CS3 p.7).

Pipeline:
  CS2 signals (company_signal_summaries) ──┐
  SEC section rubric scores (rubric_scorer) ──┤──► EvidenceMapper ──► 7 DimensionScores
  [Future] Glassdoor reviews ──────────────┤
  [Future] Board composition ──────────────┘

Naming convention: Uses existing Dimension enum values from app.models.enumerations:
  data_infrastructure, ai_governance, technology_stack,
  talent_skills, leadership_vision, use_case_portfolio, culture_change
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from decimal import Decimal, ROUND_HALF_UP

from app.models.enumerations import Dimension


class SignalSource(str, Enum):
    """All evidence sources — CS2 external signals + SEC sections + CS3 new."""
    # CS2 External Signals (from company_signal_summaries)
    TECHNOLOGY_HIRING = "technology_hiring"
    INNOVATION_ACTIVITY = "innovation_activity"
    DIGITAL_PRESENCE = "digital_presence"
    LEADERSHIP_SIGNALS = "leadership_signals"
    # CS2 SEC Sections (scored via rubric_scorer)
    SEC_ITEM_1 = "sec_item_1"        # Item 1 — Business
    SEC_ITEM_1A = "sec_item_1a"      # Item 1A — Risk Factors
    SEC_ITEM_7 = "sec_item_7"        # Item 7 — MD&A
    # CS3 New Sources (placeholder for later)
    GLASSDOOR_REVIEWS = "glassdoor_reviews"
    BOARD_COMPOSITION = "board_composition"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DimensionMapping:
    """Maps a signal source to dimensions with weights (Table 1 row)."""
    source: SignalSource
    primary_dimension: Dimension
    primary_weight: Decimal
    secondary_mappings: Dict[Dimension, Decimal] = field(default_factory=dict)
    reliability: Decimal = Decimal("0.80")    # Source reliability factor


@dataclass
class EvidenceScore:
    """A score from a single evidence source (input to the mapper)."""
    source: SignalSource
    raw_score: Decimal          # 0-100
    confidence: Decimal         # 0-1
    evidence_count: int         # number of evidence items behind this score
    metadata: Dict = field(default_factory=dict)


@dataclass
class DimensionScore:
    """Aggregated score for one dimension (output of the mapper)."""
    dimension: Dimension
    score: Decimal                              # 0-100
    contributing_sources: List[SignalSource]
    total_weight: Decimal
    confidence: Decimal


# ---------------------------------------------------------------------------
# THE CRITICAL MAPPING TABLE  (Table 1, CS3 p.7)
#
# Each row: source → primary dimension (bold weight) + secondaries
# Weights within a source sum to 1.0
#
# CS2 Source              | Data  Gov   Tech  Talent Lead  Use   Culture
# ────────────────────────┼──────────────────────────────────────────────
# technology_hiring       | 0.10  —     0.20  *0.70* —     —     0.10
# innovation_activity     | 0.20  —     *0.50* —     —     0.30  —
# digital_presence        | *0.60* —    0.40  —     —     —     —
# leadership_signals      | —     0.25  —     —     *0.60* —     0.15
# sec_item_1 (Business)   | —     —     0.30  —     —     *0.70* —
# sec_item_1a (Risk)      | 0.20  *0.80* —    —     —     —     —
# sec_item_7 (MD&A)       | 0.20  —     —     —     *0.50* 0.30  —
# glassdoor_reviews [NEW] | —     —     —     0.10  0.10  —     *0.80*
# board_composition [NEW] | —     *0.70* —    —     0.30  —     —
# ---------------------------------------------------------------------------

SIGNAL_TO_DIMENSION_MAP: Dict[SignalSource, DimensionMapping] = {

    # ── CS2 External Signals ──────────────────────────────────────────

    SignalSource.TECHNOLOGY_HIRING: DimensionMapping(
        source=SignalSource.TECHNOLOGY_HIRING,
        primary_dimension=Dimension.TALENT_SKILLS,
        primary_weight=Decimal("0.70"),
        secondary_mappings={
            Dimension.TECHNOLOGY_STACK: Decimal("0.20"),
            Dimension.DATA_INFRASTRUCTURE: Decimal("0.10"),
            Dimension.CULTURE_CHANGE: Decimal("0.10"),  # ← ADD THIS (CS3 Table 1)
        },
        reliability=Decimal("0.85"),
    ),

    SignalSource.INNOVATION_ACTIVITY: DimensionMapping(
        source=SignalSource.INNOVATION_ACTIVITY,
        primary_dimension=Dimension.TECHNOLOGY_STACK,
        primary_weight=Decimal("0.50"),
        secondary_mappings={
            Dimension.USE_CASE_PORTFOLIO: Decimal("0.30"),
            Dimension.DATA_INFRASTRUCTURE: Decimal("0.20"),
        },
        reliability=Decimal("0.80"),
    ),

    SignalSource.DIGITAL_PRESENCE: DimensionMapping(
        source=SignalSource.DIGITAL_PRESENCE,
        primary_dimension=Dimension.DATA_INFRASTRUCTURE,
        primary_weight=Decimal("0.60"),
        secondary_mappings={
            Dimension.TECHNOLOGY_STACK: Decimal("0.40"),
        },
        reliability=Decimal("0.85"),
    ),

    SignalSource.LEADERSHIP_SIGNALS: DimensionMapping(
        source=SignalSource.LEADERSHIP_SIGNALS,
        primary_dimension=Dimension.LEADERSHIP_VISION,
        primary_weight=Decimal("0.60"),
        secondary_mappings={
            Dimension.AI_GOVERNANCE: Decimal("0.25"),
            Dimension.CULTURE_CHANGE: Decimal("0.15"),
        },
        reliability=Decimal("0.80"),
    ),

    # ── CS2 SEC Section Sources ───────────────────────────────────────

    SignalSource.SEC_ITEM_1: DimensionMapping(
        source=SignalSource.SEC_ITEM_1,
        primary_dimension=Dimension.USE_CASE_PORTFOLIO,
        primary_weight=Decimal("0.70"),
        secondary_mappings={
            Dimension.TECHNOLOGY_STACK: Decimal("0.30"),
        },
        reliability=Decimal("0.75"),
    ),

    SignalSource.SEC_ITEM_1A: DimensionMapping(
        source=SignalSource.SEC_ITEM_1A,
        primary_dimension=Dimension.AI_GOVERNANCE,
        primary_weight=Decimal("0.80"),
        secondary_mappings={
            Dimension.DATA_INFRASTRUCTURE: Decimal("0.20"),
        },
        reliability=Decimal("0.75"),
    ),

    SignalSource.SEC_ITEM_7: DimensionMapping(
        source=SignalSource.SEC_ITEM_7,
        primary_dimension=Dimension.LEADERSHIP_VISION,
        primary_weight=Decimal("0.50"),
        secondary_mappings={
            Dimension.USE_CASE_PORTFOLIO: Decimal("0.30"),
            Dimension.DATA_INFRASTRUCTURE: Decimal("0.20"),
        },
        reliability=Decimal("0.75"),
    ),

    # ── CS3 New Sources (placeholders — implement later) ──────────────

    SignalSource.GLASSDOOR_REVIEWS: DimensionMapping(
        source=SignalSource.GLASSDOOR_REVIEWS,
        primary_dimension=Dimension.CULTURE_CHANGE,
        primary_weight=Decimal("0.80"),
        secondary_mappings={
            Dimension.TALENT_SKILLS: Decimal("0.10"),
            Dimension.LEADERSHIP_VISION: Decimal("0.10"),
        },
        reliability=Decimal("0.70"),
    ),

    SignalSource.BOARD_COMPOSITION: DimensionMapping(
        source=SignalSource.BOARD_COMPOSITION,
        primary_dimension=Dimension.AI_GOVERNANCE,
        primary_weight=Decimal("0.70"),
        secondary_mappings={
            Dimension.LEADERSHIP_VISION: Decimal("0.30"),
        },
        reliability=Decimal("0.75"),
    ),
}


# ---------------------------------------------------------------------------
# EvidenceMapper
# ---------------------------------------------------------------------------

class EvidenceMapper:
    """
    Maps CS2 evidence to 7 V^R dimensions using weighted contributions.

    Usage:
        mapper = EvidenceMapper()
        evidence = [
            EvidenceScore(source=SignalSource.TECHNOLOGY_HIRING, raw_score=Decimal("72"), ...),
            EvidenceScore(source=SignalSource.SEC_ITEM_1A, raw_score=Decimal("55"), ...),
            ...
        ]
        dimension_scores = mapper.map_evidence_to_dimensions(evidence)
        # Returns Dict[Dimension, DimensionScore] with all 7 dimensions
    """

    def __init__(self):
        self.mappings = SIGNAL_TO_DIMENSION_MAP

    def map_evidence_to_dimensions(
        self,
        evidence_scores: List[EvidenceScore],
    ) -> Dict[Dimension, DimensionScore]:
        """
        Convert CS2 evidence scores to 7 dimension scores.

        Algorithm:
          1. Initialize accumulators for each dimension
          2. For each evidence source:
             a. Look up its mapping
             b. Add weighted contribution to primary dimension
             c. Add weighted contributions to secondary dimensions
          3. Calculate weighted average for each dimension
          4. Dimensions with NO evidence default to 50.0

        Args:
            evidence_scores: List of scores from CS2 + CS3 sources

        Returns:
            Dict mapping each Dimension to its aggregated DimensionScore
        """
        # Step 1 — accumulators
        dimension_sums: Dict[Dimension, Decimal] = {d: Decimal("0") for d in Dimension}
        dimension_weights: Dict[Dimension, Decimal] = {d: Decimal("0") for d in Dimension}
        dimension_sources: Dict[Dimension, List[SignalSource]] = {d: [] for d in Dimension}
        dimension_conf_sum: Dict[Dimension, Decimal] = {d: Decimal("0") for d in Dimension}
        dimension_conf_count: Dict[Dimension, int] = {d: 0 for d in Dimension}

        # Step 2 — process each evidence score
        for ev in evidence_scores:
            mapping = self.mappings.get(ev.source)
            if not mapping:
                continue

            effective_score = ev.raw_score * ev.confidence * mapping.reliability

            # Helper to accumulate a (dimension, weight) contribution
            def _add(dim: Dimension, weight: Decimal):
                dimension_sums[dim] += effective_score * weight
                dimension_weights[dim] += weight * ev.confidence * mapping.reliability
                if ev.source not in dimension_sources[dim]:
                    dimension_sources[dim].append(ev.source)
                dimension_conf_sum[dim] += ev.confidence
                dimension_conf_count[dim] += 1

            # Primary contribution
            _add(mapping.primary_dimension, mapping.primary_weight)

            # Secondary contributions
            for dim, weight in mapping.secondary_mappings.items():
                _add(dim, weight)

        # Step 3 — weighted averages & defaults
        results: Dict[Dimension, DimensionScore] = {}
        for dim in Dimension:
            total_w = dimension_weights[dim]
            if total_w > 0:
                score = (dimension_sums[dim] / total_w).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                # Clamp to [0, 100]
                score = max(Decimal("0"), min(Decimal("100"), score))
                conf = (
                    dimension_conf_sum[dim]
                    / Decimal(str(dimension_conf_count[dim]))
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            else:
                # No evidence → default 50.0
                score = Decimal("50.00")
                conf = Decimal("0.000")

            results[dim] = DimensionScore(
                dimension=dim,
                score=score,
                contributing_sources=dimension_sources[dim],
                total_weight=total_w.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
                confidence=conf,
            )

        return results

    def get_coverage_report(
        self,
        evidence_scores: List[EvidenceScore],
    ) -> Dict[Dimension, Dict]:
        """
        Report which dimensions have evidence and which have gaps.

        Returns dict per dimension:
          - has_evidence: bool
          - source_count: int
          - total_weight: float
          - confidence: float
          - sources: list[str]
        """
        dim_scores = self.map_evidence_to_dimensions(evidence_scores)
        report: Dict[Dimension, Dict] = {}
        for dim, ds in dim_scores.items():
            report[dim] = {
                "has_evidence": len(ds.contributing_sources) > 0,
                "source_count": len(ds.contributing_sources),
                "total_weight": float(ds.total_weight),
                "confidence": float(ds.confidence),
                "sources": [s.value for s in ds.contributing_sources],
            }
        return report

    def build_mapping_matrix(
        self,
        evidence_scores: List[EvidenceScore],
        ticker: str,
    ) -> List[Dict]:
        """
        Build the mapping matrix view (Table 1) with actual scores for a company.

        Returns a list of rows matching the CS3 mapping table format:
        [
          {
            "ticker": "JPM",
            "source": "technology_hiring",
            "raw_score": 72.0,
            "confidence": 0.85,
            "data_infrastructure": 0.10,
            "ai_governance": null,
            "technology_stack": 0.20,
            "talent_skills": 0.70,
            "leadership_vision": null,
            "use_case_portfolio": null,
            "culture_change": 0.10,
          },
          ...
        ]
        This can be inserted directly into a Snowflake table for the matrix view.
        """
        # Build lookup of evidence scores by source
        ev_lookup: Dict[SignalSource, EvidenceScore] = {
            ev.source: ev for ev in evidence_scores
        }

        rows = []
        for source, mapping in self.mappings.items():
            ev = ev_lookup.get(source)
            row = {
                "ticker": ticker,
                "source": source.value,
                "raw_score": float(ev.raw_score) if ev else None,
                "confidence": float(ev.confidence) if ev else None,
                "evidence_count": ev.evidence_count if ev else 0,
            }
            # Add weight columns for each dimension
            for dim in Dimension:
                weight = None
                if dim == mapping.primary_dimension:
                    weight = float(mapping.primary_weight)
                elif dim in mapping.secondary_mappings:
                    weight = float(mapping.secondary_mappings[dim])
                row[dim.value] = weight

            rows.append(row)

        return rows

    def build_dimension_summary(
        self,
        evidence_scores: List[EvidenceScore],
        ticker: str,
    ) -> List[Dict]:
        """
        Build the final 7-dimension score summary for a company.

        Returns list of dicts ready for Snowflake insert:
        [
          {
            "ticker": "JPM",
            "dimension": "data_infrastructure",
            "score": 62.5,
            "confidence": 0.82,
            "source_count": 3,
            "sources": "digital_presence,innovation_activity,technology_hiring",
            "total_weight": 0.45
          },
          ...
        ]
        """
        dim_scores = self.map_evidence_to_dimensions(evidence_scores)
        rows = []
        for dim in Dimension:
            ds = dim_scores[dim]
            rows.append({
                "ticker": ticker,
                "dimension": dim.value,
                "score": float(ds.score),
                "confidence": float(ds.confidence),
                "source_count": len(ds.contributing_sources),
                "sources": ",".join(s.value for s in ds.contributing_sources),
                "total_weight": float(ds.total_weight),
            })
        return rows