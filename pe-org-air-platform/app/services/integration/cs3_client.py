"""CS3 Client — Scores and rubric data from the PE Org-AI-R platform."""
from __future__ import annotations

import json
import logging
from enum import Enum

import httpx
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.models.enumerations import Dimension, DIMENSION_ALIAS_MAP
from app.prompts.rag_prompts import (
    CS3_KEYWORD_EXPANSION_USER,
    CS3_SCORE_ESTIMATION_USER,
    CS3_COMPANY_ENRICHMENT_USER,
)

logger = logging.getLogger(__name__)

from app.core.settings import settings as _settings

GROQ_API_KEY = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_API_URL = _settings.GROQ_API_URL

# Short dimension names used in CS3 RAG context (matches stored rubric keys)
DIMENSIONS = [
    "data_infrastructure",
    "ai_governance",
    "technology_stack",
    "talent",
    "leadership",
    "use_case_portfolio",
    "culture",
]

SCORE_LEVELS = {
    1: ("Nascent", 0, 19),
    2: ("Developing", 20, 39),
    3: ("Adequate", 40, 59),
    4: ("Good", 60, 79),
    5: ("Excellent", 80, 100),
}


class ScoreLevel(int, Enum):
    """Score levels (1–5)."""
    LEVEL_5 = 5  # 80-100 Excellent
    LEVEL_4 = 4  # 60-79  Good
    LEVEL_3 = 3  # 40-59  Adequate
    LEVEL_2 = 2  # 20-39  Developing
    LEVEL_1 = 1  # 0-19   Nascent

    @property
    def name_label(self) -> str:
        return {5: "Excellent", 4: "Good", 3: "Adequate", 2: "Developing", 1: "Nascent"}[self.value]

    @property
    def score_range(self) -> Tuple[int, int]:
        return {5: (80, 100), 4: (60, 79), 3: (40, 59), 2: (20, 39), 1: (0, 19)}[self.value]


class AssessmentStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class AssessmentType(str, Enum):
    INITIAL = "initial"
    FOLLOW_UP = "follow_up"
    ANNUAL = "annual"


# ---------------------------------------------------------------------------
# GroqScoreEstimate dataclass
# ---------------------------------------------------------------------------

@dataclass
class GroqScoreEstimate:
    """Groq-generated score estimate for a missing/zero dimension."""
    dimension: str
    ticker: str
    estimated_score: float
    level: int
    level_name: str
    rationale: str
    confidence: float  # Groq's self-reported confidence (0–1)
    keywords: List[str]


# ---------------------------------------------------------------------------
# Static rubric / keyword data
# ---------------------------------------------------------------------------

_RUBRIC_TEXT: Dict[str, Dict[int, str]] = {
    "data_infrastructure": {
        5: "Enterprise-grade, real-time data platform with ML-ready pipelines and data governance.",
        4: "Robust data warehouse with good pipeline coverage and partial ML readiness.",
        3: "Functional data infrastructure with some gaps in pipeline automation.",
        2: "Basic data storage with limited pipeline automation.",
        1: "Minimal or ad-hoc data infrastructure; no ML-ready pipelines.",
    },
    "ai_governance": {
        5: "Comprehensive AI ethics framework, model governance, and regulatory compliance.",
        4: "Formal AI governance policies with bias monitoring and explainability practices.",
        3: "Some AI governance in place; policies defined but inconsistently applied.",
        2: "Ad-hoc AI oversight; limited formal governance structure.",
        1: "No formal AI governance or ethics framework.",
    },
    "technology_stack": {
        5: "Best-in-class cloud-native ML platform with full MLOps and CI/CD for models.",
        4: "Modern cloud stack with MLOps tooling and containerised deployments.",
        3: "Cloud adoption with partial MLOps; some manual deployment steps.",
        2: "Hybrid on-premise/cloud with limited ML tooling.",
        1: "Legacy on-premise stack with no ML infrastructure.",
    },
    "talent": {
        5: "Deep AI/ML talent pool with specialised researchers and broad skills coverage.",
        4: "Strong data science and ML engineering team with diverse skills.",
        3: "Adequate ML team; some skill gaps in emerging areas.",
        2: "Small ML team; heavy reliance on a few individuals.",
        1: "Minimal AI talent; no dedicated ML roles.",
    },
    "leadership": {
        5: "C-suite AI champion with published strategy, dedicated AI budget, and innovation labs.",
        4: "Strong executive sponsorship of AI with a defined roadmap.",
        3: "AI strategy exists but leadership engagement is inconsistent.",
        2: "Limited leadership visibility on AI initiatives.",
        1: "No clear AI strategy or executive sponsorship.",
    },
    "use_case_portfolio": {
        5: "Broad portfolio of production AI use cases generating measurable business value.",
        4: "Several production AI use cases with clear ROI.",
        3: "Mix of production and pilot AI use cases.",
        2: "A few pilot AI projects; limited production deployments.",
        1: "Exploratory AI discussions only; no production use cases.",
    },
    "culture": {
        5: "Data-driven culture embedded across all functions; continuous experimentation norm.",
        4: "Strong data-driven culture with active AI adoption programmes.",
        3: "Growing data culture; AI adoption varies across business units.",
        2: "Emerging data awareness; limited AI culture.",
        1: "Traditional culture; resistance to data-driven decision making.",
    },
}

_BASE_KEYWORDS: Dict[str, List[str]] = {
    "data_infrastructure": ["data lake", "data warehouse", "ETL", "data pipeline", "real-time data", "cloud storage"],
    "ai_governance": ["AI ethics", "model governance", "responsible AI", "bias detection", "explainability", "AI policy"],
    "technology_stack": ["machine learning platform", "MLOps", "Kubernetes", "cloud-native", "microservices", "API gateway"],
    "talent": ["machine learning engineer", "data scientist", "AI researcher", "NLP", "computer vision", "deep learning"],
    "leadership": ["Chief AI Officer", "AI strategy", "digital transformation", "technology roadmap", "innovation lab"],
    "use_case_portfolio": ["AI use case", "automation", "predictive analytics", "recommendation system", "computer vision"],
    "culture": ["data-driven", "experimentation", "agile", "innovation culture", "AI adoption", "continuous learning"],
}

# Map both canonical and short aliases to the short rubric key names used
# in _RUBRIC_TEXT and _BASE_KEYWORDS (talent, leadership, culture).
_DIM_ALIAS_MAP: Dict[str, str] = {
    "data_infrastructure": "data_infrastructure",
    "ai_governance": "ai_governance",
    "technology_stack": "technology_stack",
    "talent": "talent",
    "talent_skills": "talent",
    "leadership": "leadership",
    "leadership_vision": "leadership",
    "use_case_portfolio": "use_case_portfolio",
    "culture": "culture",
    "culture_change": "culture",
}


# ---------------------------------------------------------------------------
# Groq async helpers (module-level)
# ---------------------------------------------------------------------------

async def _groq_post(prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> Optional[str]:
    """Call Groq and return the response text, or None on failure."""
    if not GROQ_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("groq_call_failed: %s", e)
        return None


async def expand_keywords(ticker: str, dimension_name: str) -> List[str]:
    """
    Expand base keywords for a dimension using Groq, company-specific context.
    Falls back to static base keywords if Groq is unavailable.
    """
    norm = _DIM_ALIAS_MAP.get(dimension_name, dimension_name)
    base = _BASE_KEYWORDS.get(norm, [])
    prompt = CS3_KEYWORD_EXPANSION_USER.format(
        ticker=ticker,
        dimension_name=dimension_name,
        base_keywords=", ".join(base),
    )
    text = await _groq_post(prompt, max_tokens=200)
    if not text:
        return base
    expanded = [k.strip() for k in text.split(",") if k.strip()]
    return list(set(base + expanded))


async def estimate_missing_score(ticker: str, dimension_name: str, company_name: str = "") -> GroqScoreEstimate:
    """
    When a dimension has no evidence (score=0, evidence_count=0), use Groq to
    generate a plausible score estimate from public knowledge about the company.
    """
    norm = _DIM_ALIAS_MAP.get(dimension_name, dimension_name)
    dim_label = dimension_name.replace("_", " ").title()
    name_hint = f"({company_name})" if company_name else ""
    rubric_data = _RUBRIC_TEXT.get(norm, {})
    prompt = CS3_SCORE_ESTIMATION_USER.format(
        ticker=ticker,
        name_hint=name_hint,
        dim_label=dim_label,
        rubric_data=json.dumps(rubric_data),
    )
    text = await _groq_post(prompt, max_tokens=400, temperature=0.4)
    estimated_score = 0.0
    confidence = 0.3
    rationale = "Groq unavailable — score estimated as 0."
    keywords: List[str] = _BASE_KEYWORDS.get(norm, [])

    if text:
        try:
            json_str = text
            if "```" in text:
                json_str = text.split("```")[1].lstrip("json").strip()
            data = json.loads(json_str)
            estimated_score = float(data.get("score", 0))
            confidence = float(data.get("confidence", 0.3))
            rationale = str(data.get("rationale", ""))
            keywords = data.get("keywords", keywords)
        except Exception:
            import re
            m = re.search(r'"score"\s*:\s*([\d.]+)', text)
            if m:
                estimated_score = float(m.group(1))
            rationale = text[:300]

    level, level_name = score_to_level(estimated_score)
    return GroqScoreEstimate(
        dimension=dimension_name,
        ticker=ticker,
        estimated_score=estimated_score,
        level=level,
        level_name=level_name,
        rationale=rationale,
        confidence=confidence,
        keywords=keywords,
    )


async def enrich_company_fields(ticker: str, company_name: str) -> Dict[str, Any]:
    """
    Use Groq to fill in missing company metadata fields
    (sector, sub_sector, revenue, employee_count, fiscal_year_end).
    """
    prompt = CS3_COMPANY_ENRICHMENT_USER.format(
        ticker=ticker,
        company_name=company_name,
    )
    text = await _groq_post(prompt, max_tokens=200, temperature=0.2)
    if not text:
        return {}
    try:
        json_str = text
        if "```" in text:
            json_str = text.split("```")[1].lstrip("json").strip()
        return json.loads(json_str)
    except Exception:
        return {}


def score_to_level(score: float) -> tuple[int, str]:
    """Convert numeric score to (level_int, level_name)."""
    for level, (name, lo, hi) in SCORE_LEVELS.items():
        if lo <= score <= hi:
            return level, name
    return 5, "Excellent"


@dataclass
class DimensionScore:
    dimension: str
    score: float
    level: int
    level_name: str
    confidence_interval: tuple[float, float] = (0.0, 0.0)
    rubric_keywords: List[str] = field(default_factory=list)


@dataclass
class CompanyAssessment:
    company_id: str
    ticker: str
    dimension_scores: Dict[str, DimensionScore] = field(default_factory=dict)
    talent_concentration: float = 0.0
    valuation_risk: float = 0.0
    position_factor: float = 0.0
    human_capital_risk: float = 0.0
    synergy: float = 0.0
    org_air_score: float = 0.0
    assessment_id: Optional[str] = None


@dataclass
class RubricCriteria:
    dimension: str
    level: int
    level_name: str
    criteria: str
    keywords: List[str] = field(default_factory=list)


def get_rubric_static(dimension: str, level: Optional[int] = None) -> List[RubricCriteria]:
    """Return RubricCriteria from local static data — no HTTP, no CS3Client needed."""
    norm = _DIM_ALIAS_MAP.get(dimension, dimension)
    rubric_text = _RUBRIC_TEXT.get(norm, {})
    keywords = _BASE_KEYWORDS.get(norm, [])
    results = []
    for lvl, criteria_text in rubric_text.items():
        if level is None or lvl == level:
            level_name = SCORE_LEVELS.get(lvl, ("Unknown", 0, 0))[0]
            results.append(RubricCriteria(
                dimension=dimension,
                level=lvl,
                level_name=level_name,
                criteria=criteria_text,
                keywords=keywords,
            ))
    return results


class CS3Client:
    """Fetches scoring and rubric data from CS3 API endpoints."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self._client = httpx.Client(timeout=30.0)

    def get_assessment(self, company_id: str) -> Optional[CompanyAssessment]:
        """Fetch the full composite assessment for a company (company_id is ticker)."""
        resp = self._client.get(f"{self.base_url}/scoring/{company_id}/dimensions")
        if resp.status_code == 200:
            return self._parse_assessment(resp.json(), company_id)
        return None

    def get_dimension_score(
        self, company_id: str, dimension: str
    ) -> Optional[DimensionScore]:
        """Fetch score for one specific dimension (company_id is ticker)."""
        assessment = self.get_assessment(company_id)
        if not assessment:
            return None
        scores = assessment.dimension_scores
        # Exact match first
        if dimension in scores:
            return scores[dimension]
        # Prefix/substring match (e.g. "talent" matches "talent_skills")
        dim_lower = dimension.lower().replace("_management", "").replace("_", "")
        for key, val in scores.items():
            if key.startswith(dimension) or dim_lower in key.replace("_", ""):
                return val
        return None

    def get_rubric(
        self, dimension: str, level: Optional[int] = None
    ) -> List[RubricCriteria]:
        """Fetch rubric criteria for a dimension (optionally filtered by level).

        NOTE: /api/v1/scoring/rubrics is not a registered route in the current API.
        The HTTP call will 404 and this method always falls back to the local static
        rubric data defined in _default_rubric().
        """
        params: dict = {"dimension": dimension}
        if level is not None:
            params["level"] = level
        resp = self._client.get(f"{self.base_url}/scoring/rubrics", params=params)
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("rubrics", [])
            return [self._parse_rubric(r) for r in items]
        # Return default rubric from hardcoded data
        return self._default_rubric(dimension, level)

    def _parse_assessment(self, data: dict, company_id: str) -> CompanyAssessment:
        dim_scores: Dict[str, DimensionScore] = {}
        # /scoring/{ticker}/dimensions returns {"ticker": ..., "scores": [...]}
        raw_dims = data.get("scores", data.get("dimension_scores", data.get("dimensions", {})))
        if isinstance(raw_dims, list):
            for d in raw_dims:
                dim = d.get("dimension", d.get("dimension_name", ""))
                if dim:
                    dim_scores[dim] = self._parse_dimension_score(dim, d)
        elif isinstance(raw_dims, dict):
            for dim, val in raw_dims.items():
                if isinstance(val, (int, float)):
                    level, name = score_to_level(val)
                    dim_scores[dim] = DimensionScore(
                        dimension=dim, score=val, level=level, level_name=name
                    )
                elif isinstance(val, dict):
                    dim_scores[dim] = self._parse_dimension_score(dim, val)
        return CompanyAssessment(
            company_id=company_id,
            ticker=data.get("ticker", company_id),
            dimension_scores=dim_scores,
            talent_concentration=float(data.get("talent_concentration", 0.0)),
            valuation_risk=float(data.get("valuation_risk", 0.0)),
            position_factor=float(data.get("position_factor", 0.0)),
            human_capital_risk=float(data.get("human_capital_risk", 0.0)),
            synergy=float(data.get("synergy", 0.0)),
            org_air_score=float(data.get("org_air_score", data.get("orgair_score", 0.0))),
            assessment_id=str(data.get("assessment_id", data.get("id", ""))),
        )

    @staticmethod
    def _parse_dimension_score(dimension: str, data: dict) -> DimensionScore:
        score = float(data.get("score", data.get("value", 0.0)))
        level, level_name = score_to_level(score)
        ci = data.get("confidence_interval", [0.0, 0.0])
        if isinstance(ci, dict):
            ci = [ci.get("lower", 0.0), ci.get("upper", 0.0)]
        return DimensionScore(
            dimension=dimension,
            score=score,
            level=level,
            level_name=level_name,
            confidence_interval=tuple(ci[:2]) if len(ci) >= 2 else (0.0, 0.0),
            rubric_keywords=data.get("rubric_keywords", []),
        )

    @staticmethod
    def _parse_rubric(data: dict) -> RubricCriteria:
        level = int(data.get("level", 3))
        _, level_name = score_to_level(level * 20)
        return RubricCriteria(
            dimension=data.get("dimension", ""),
            level=level,
            level_name=data.get("level_name", level_name),
            criteria=data.get("criteria", data.get("description", "")),
            keywords=data.get("keywords", []),
        )

    @staticmethod
    def _default_rubric(dimension: str, level: Optional[int]) -> List[RubricCriteria]:
        """Minimal fallback rubric when API is unavailable."""
        _rubrics = {
            "data_infrastructure": {
                1: ("Basic data storage, no cloud architecture", ["storage", "database"]),
                2: ("Cloud data warehouse, basic pipelines", ["warehouse", "pipeline", "cloud"]),
                3: ("Modern data stack, real-time ingestion", ["streaming", "lakehouse", "ETL"]),
                4: ("AI-ready platform, feature store, MLOps", ["feature store", "MLflow", "Airflow"]),
                5: ("Unified AI data fabric, automated governance", ["data fabric", "automated", "governance"]),
            },
        }
        dim_rubrics = _rubrics.get(dimension, {})
        results = []
        for lvl, (criteria, keywords) in dim_rubrics.items():
            if level is None or lvl == level:
                _, level_name = score_to_level(lvl * 20)
                results.append(
                    RubricCriteria(
                        dimension=dimension,
                        level=lvl,
                        level_name=level_name,
                        criteria=criteria,
                        keywords=keywords,
                    )
                )
        return results

    # -----------------------------------------------------------------------
    # Groq-enhanced async methods
    # -----------------------------------------------------------------------

    async def get_dimension_keywords(self, ticker: str, dimension_name: str) -> List[str]:
        """Platform endpoint first → local Groq fallback."""
        try:
            resp = self._client.get(
                f"{self.base_url}/companies/{ticker.upper()}/dimension-keywords",
                params={"dimension": dimension_name},
            )
            if resp.status_code == 200:
                return resp.json().get("keywords", [])
        except Exception:
            pass
        return await expand_keywords(ticker, dimension_name)

    async def get_all_dimension_estimates(self, ticker: str) -> Dict[str, GroqScoreEstimate]:
        """Groq estimates for all 7 dimensions."""
        company_name = ""
        try:
            assessment = self.get_assessment(ticker)
            if assessment:
                company_name = assessment.ticker
        except Exception:
            pass
        results: Dict[str, GroqScoreEstimate] = {}
        for dim in DIMENSIONS:
            results[dim] = await estimate_missing_score(ticker, dim, company_name)
        return results

    async def get_enriched_company(self, ticker: str) -> Optional[Any]:
        """Company assessment + Groq-filled missing metadata fields (does not write back)."""
        try:
            company = self.get_assessment(ticker)
        except Exception:
            return None
        if company and not any([
            getattr(company, "sector", None),
            getattr(company, "revenue_millions", None),
            getattr(company, "employee_count", None),
        ]):
            enriched = await enrich_company_fields(ticker, ticker)
            for attr in ("sector", "sub_sector", "revenue_millions", "employee_count", "fiscal_year_end"):
                if enriched.get(attr) and hasattr(company, attr):
                    setattr(company, attr, enriched[attr])
        return company

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
