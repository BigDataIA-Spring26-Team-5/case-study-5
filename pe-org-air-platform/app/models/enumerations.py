from enum import Enum


class AssessmentType(str, Enum):
    SCREENING = "screening"          # Quick external assessment
    DUE_DILIGENCE = "due_diligence"  # Deep dive with internal access
    QUARTERLY = "quarterly"          # Regular portfolio monitoring
    EXIT_PREP = "exit_prep"          # Pre-exit assessment


class AssessmentStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class Dimension(str, Enum):
    """The 7 V^R dimensions — canonical source of truth.

    Stored format uses full names (talent_skills, leadership_vision, culture_change).
    CS3/RAG code may use short aliases (talent, leadership, culture) — use
    DIMENSION_ALIAS_MAP to normalize.
    """
    DATA_INFRASTRUCTURE = "data_infrastructure"
    AI_GOVERNANCE = "ai_governance"
    TECHNOLOGY_STACK = "technology_stack"
    TALENT_SKILLS = "talent_skills"
    LEADERSHIP_VISION = "leadership_vision"
    USE_CASE_PORTFOLIO = "use_case_portfolio"
    CULTURE_CHANGE = "culture_change"


# All valid dimension string values (canonical + short aliases)
VALID_DIMENSIONS: frozenset[str] = frozenset(d.value for d in Dimension)

# Map short CS3/RAG aliases to canonical dimension values
DIMENSION_ALIAS_MAP: dict[str, str] = {
    "data_infrastructure": "data_infrastructure",
    "ai_governance": "ai_governance",
    "technology_stack": "technology_stack",
    "talent": "talent_skills",
    "talent_skills": "talent_skills",
    "leadership": "leadership_vision",
    "leadership_vision": "leadership_vision",
    "use_case_portfolio": "use_case_portfolio",
    "culture": "culture_change",
    "culture_change": "culture_change",
}