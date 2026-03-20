"""Gap Analysis — identifies improvement areas to reach target Org-AI-R score."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional

from app.services.integration.cs3_client import (
    DIMENSIONS, SCORE_LEVELS, score_to_level, _RUBRIC_TEXT,
)

logger = logging.getLogger(__name__)

# Dimension improvement priority (cost-effectiveness ranking)
IMPROVEMENT_PRIORITY: Dict[str, int] = {
    "data_infrastructure": 1,
    "technology_stack": 2,
    "talent": 3,
    "ai_governance": 4,
    "use_case_portfolio": 5,
    "leadership": 6,
    "culture": 7,
}


@dataclass
class DimensionGap:
    """Gap analysis for a single dimension."""
    dimension: str
    current_score: float
    current_level: int
    current_level_name: str
    target_score: float
    target_level: int
    target_level_name: str
    gap: float
    priority: int
    next_level_criteria: str
    improvement_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GapAnalysisResult:
    """Complete gap analysis for a company."""
    company_id: str
    current_org_air: float
    target_org_air: float
    total_gap: float
    dimensions: List[DimensionGap] = field(default_factory=list)
    top_priorities: List[str] = field(default_factory=list)
    estimated_improvement_potential: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GapAnalyzer:
    """Analyzes gaps between current and target scores across dimensions."""

    def analyze(
        self,
        company_id: str,
        dimension_scores: Dict[str, float],
        current_org_air: float,
        target_org_air: float,
    ) -> GapAnalysisResult:
        """
        Run gap analysis for a company.

        Args:
            company_id: Company ticker
            dimension_scores: Current scores per dimension
            current_org_air: Current composite Org-AI-R score
            target_org_air: Target Org-AI-R score
        """
        total_gap = max(0, target_org_air - current_org_air)
        dimension_gaps: List[DimensionGap] = []

        for dim in DIMENSIONS:
            current = dimension_scores.get(dim, 0.0)
            current_level, current_name = score_to_level(current)

            # Calculate proportional target for this dimension
            if current_org_air > 0:
                ratio = current / current_org_air
                dim_target = min(100, current + (total_gap * ratio * 1.5))
            else:
                dim_target = min(100, current + total_gap / len(DIMENSIONS))

            target_level, target_name = score_to_level(dim_target)
            gap = max(0, dim_target - current)

            # Get next level criteria
            next_level = min(current_level + 1, 5)
            rubric = _RUBRIC_TEXT.get(dim, {})
            next_criteria = rubric.get(next_level, f"Advance to Level {next_level}")

            # Generate improvement actions
            actions = self._generate_actions(dim, current_level, next_level)

            dimension_gaps.append(DimensionGap(
                dimension=dim,
                current_score=round(current, 2),
                current_level=current_level,
                current_level_name=current_name,
                target_score=round(dim_target, 2),
                target_level=target_level,
                target_level_name=target_name,
                gap=round(gap, 2),
                priority=IMPROVEMENT_PRIORITY.get(dim, 5),
                next_level_criteria=next_criteria,
                improvement_actions=actions,
            ))

        # Sort by gap size (largest first), then by priority
        dimension_gaps.sort(key=lambda g: (-g.gap, g.priority))

        # Top 3 priorities
        top_priorities = [g.dimension for g in dimension_gaps[:3] if g.gap > 0]

        # Estimated improvement potential (weighted by priority)
        potential = sum(
            g.gap * (1.0 / g.priority) for g in dimension_gaps if g.gap > 0
        )

        return GapAnalysisResult(
            company_id=company_id,
            current_org_air=round(current_org_air, 2),
            target_org_air=round(target_org_air, 2),
            total_gap=round(total_gap, 2),
            dimensions=dimension_gaps,
            top_priorities=top_priorities,
            estimated_improvement_potential=round(potential, 2),
        )

    @staticmethod
    def _generate_actions(
        dimension: str, current_level: int, target_level: int
    ) -> List[str]:
        """Generate concrete improvement actions for a dimension."""
        actions_map: Dict[str, Dict[int, List[str]]] = {
            "data_infrastructure": {
                2: ["Implement cloud data warehouse", "Build basic ETL pipelines"],
                3: ["Add real-time data ingestion", "Implement data quality monitoring"],
                4: ["Deploy feature store", "Implement MLOps pipelines"],
                5: ["Build unified data fabric", "Automate data governance"],
            },
            "ai_governance": {
                2: ["Draft initial AI usage policies", "Assign AI oversight role"],
                3: ["Implement bias detection framework", "Create model registry"],
                4: ["Deploy explainability tools", "Establish AI ethics board"],
                5: ["Automate compliance monitoring", "Publish AI transparency reports"],
            },
            "technology_stack": {
                2: ["Migrate to cloud infrastructure", "Containerize ML workloads"],
                3: ["Implement CI/CD for models", "Deploy model serving platform"],
                4: ["Build full MLOps pipeline", "Implement A/B testing framework"],
                5: ["Deploy auto-scaling ML platform", "Implement feature flagging"],
            },
            "talent": {
                2: ["Hire initial ML engineers", "Create AI training program"],
                3: ["Build dedicated data science team", "Launch internal AI academy"],
                4: ["Recruit specialized ML researchers", "Create AI career ladders"],
                5: ["Establish AI research lab", "Build cross-functional AI pods"],
            },
            "leadership": {
                2: ["Appoint AI program owner", "Include AI in strategy reviews"],
                3: ["Create AI strategy document", "Establish AI steering committee"],
                4: ["Appoint Chief AI Officer", "Dedicate AI investment budget"],
                5: ["Launch innovation lab", "Publish AI-first company strategy"],
            },
            "use_case_portfolio": {
                2: ["Identify 3-5 pilot AI use cases", "Run proof-of-concept projects"],
                3: ["Deploy first production AI models", "Measure AI ROI"],
                4: ["Scale AI across business units", "Build use case pipeline"],
                5: ["Monetize AI capabilities", "License AI solutions externally"],
            },
            "culture": {
                2: ["Run data literacy workshops", "Create AI champions network"],
                3: ["Launch experimentation program", "Celebrate data-driven wins"],
                4: ["Embed AI in performance metrics", "Run hackathons"],
                5: ["Make experimentation the norm", "AI-first decision making"],
            },
        }

        dim_actions = actions_map.get(dimension, {})
        result = []
        for level in range(current_level + 1, min(target_level + 1, 6)):
            result.extend(dim_actions.get(level, [f"Advance {dimension} to Level {level}"]))
        return result[:5]
