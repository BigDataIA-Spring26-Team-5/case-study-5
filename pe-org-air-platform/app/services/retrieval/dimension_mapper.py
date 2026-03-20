"""Dimension Mapper — maps signal categories to CS3 dimension weights."""
from __future__ import annotations

from typing import Dict, List, Optional

# Exact weights from CS3 Task 5.0a mapping matrix
SIGNAL_TO_DIMENSION_MAP: Dict[str, Dict[str, float]] = {
    "technology_hiring": {
        "talent": 0.70,
        "technology_stack": 0.20,
        "culture": 0.10,
    },
    "innovation_activity": {
        "technology_stack": 0.50,
        "use_case_portfolio": 0.30,
        "data_infrastructure": 0.20,
    },
    "digital_presence": {
        "data_infrastructure": 0.60,
        "technology_stack": 0.40,
    },
    "leadership_signals": {
        "leadership": 0.45,
        "use_case_portfolio": 0.25,
        "ai_governance": 0.20,
        "culture": 0.10,
    },
    "culture_signals": {
        "culture": 0.80,
        "talent": 0.10,
        "leadership": 0.10,
    },
    "governance_signals": {
        "ai_governance": 0.70,
        "leadership": 0.30,
    },
}

# Maps source_type → signal_category.
#
# FIX: Previously all SEC sections funneled through only 3 signal categories,
# leaving talent / leadership / use_case_portfolio / culture with zero primary
# chunks in ChromaDB. The fix maps SEC sections to the signal categories whose
# PRIMARY dimension best matches what that section actually discusses:
#
#   sec_10k_item_1  = Business description  → leadership_signals
#                     (primary dim: leadership, covers use_case_portfolio too)
#   sec_10k_item_1a = Risk factors          → governance_signals (unchanged)
#                     (primary dim: ai_governance)
#   sec_10k_item_7  = MD&A                  → innovation_activity (unchanged)
#                     (primary dim: technology_stack, covers use_case_portfolio)
#
# This ensures all 7 dimensions receive some primary coverage from SEC filings,
# while job postings (talent), glassdoor (culture), and patents (technology_stack)
# continue to fill in the remaining dimensions.
SOURCE_TO_SIGNAL: Dict[str, str] = {
    # SEC filings
    "sec_10k_item_1":       "leadership_signals",  # Business desc → leadership + use_cases
    "sec_10k_item_1a":      "governance_signals",  # Risk factors  → ai_governance
    "sec_10k_item_7":       "innovation_activity", # MD&A          → technology_stack + use_cases
    # External signals
    "job_posting_linkedin": "technology_hiring",   # → talent (primary)
    "job_posting_indeed":   "technology_hiring",   # → talent (primary)
    "patent_uspto":         "innovation_activity", # → technology_stack (primary)
    "glassdoor_review":     "culture_signals",     # → culture (primary)
    "board_proxy_def14a":   "governance_signals",  # → ai_governance (primary)
    # CS4 analyst notes
    "analyst_interview":    "leadership_signals",  # → leadership (primary)
    "dd_data_room":         "digital_presence",    # → data_infrastructure (primary)
}

# All 7 dimensions — used for coverage validation
ALL_DIMENSIONS = {
    "data_infrastructure",
    "ai_governance",
    "technology_stack",
    "talent",
    "leadership",
    "use_case_portfolio",
    "culture",
}

_FALLBACK_DIMENSION = "data_infrastructure"


class DimensionMapper:
    """Maps evidence signal categories to CS3 dimension weights."""

    def get_dimension_weights(self, signal_category: str) -> Dict[str, float]:
        """Return dimension → weight mapping for a signal category."""
        return SIGNAL_TO_DIMENSION_MAP.get(signal_category, {_FALLBACK_DIMENSION: 1.0})

    def get_primary_dimension(self, signal_category: str) -> str:
        """Return the highest-weighted dimension for a signal category."""
        weights = self.get_dimension_weights(signal_category)
        return max(weights, key=weights.get)

    def get_all_dimensions_for_evidence(
        self, signal_category: str, min_weight: float = 0.1
    ) -> List[str]:
        """Return all dimensions with weight >= min_weight."""
        weights = self.get_dimension_weights(signal_category)
        return [dim for dim, w in weights.items() if w >= min_weight]

    def signal_from_source(self, source_type: str) -> str:
        """Infer signal category from source type."""
        return SOURCE_TO_SIGNAL.get(source_type, "digital_presence")

    def get_coverage(self, evidence_list: list) -> Dict[str, int]:
        """
        Returns how many evidence items map to each dimension as primary.
        Call this before indexing to spot coverage gaps.

        Example:
            mapper = DimensionMapper()
            coverage = mapper.get_coverage(evidence_list)
            # {"data_infrastructure": 120, "talent": 0, ...}
        """
        from collections import Counter
        counts: Counter = Counter()
        for ev in evidence_list:
            sig_cat = getattr(ev, "signal_category", "digital_presence")
            primary = self.get_primary_dimension(sig_cat)
            counts[primary] += 1
        return {dim: counts.get(dim, 0) for dim in ALL_DIMENSIONS}