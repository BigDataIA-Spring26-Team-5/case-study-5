"""
Services/composite_scoring_service.py — Phase 4 Extraction

Holds all computation models and orchestration for the composite scoring
pipeline: TC → V^R → PF → H^R → Synergy → Org-AI-R.

Replaces the private compute + save helpers that were scattered across four
scoring routers (tc_vr_scoring, position_factor, hr_scoring, orgair_scoring)
and eliminates the TEMPORARY duplicate _compute_tc_vr blocks introduced in
Phase 3 to break cross-router imports.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError, PipelineIncompleteError

logger = logging.getLogger(__name__)


# =====================================================================
# Constants
# =====================================================================

from app.config.company_mappings import CS3_PORTFOLIO  # noqa: E402

EXPECTED_TC_VR_RANGES = {
    "NVDA": {"tc": (0.05, 0.20), "pf": (0.7, 1.0),  "vr": (80, 100)},
    "JPM":  {"tc": (0.10, 0.25), "pf": (0.3, 0.7),  "vr": (60, 80)},
    "WMT":  {"tc": (0.12, 0.28), "pf": (0.1, 0.5),  "vr": (50, 70)},
    "GE":   {"tc": (0.18, 0.35), "pf": (-0.2, 0.2), "vr": (40, 60)},
    "DG":   {"tc": (0.22, 0.40), "pf": (-0.5, -0.1), "vr": (30, 50)},
}

MARKET_CAP_PERCENTILES: Dict[str, float] = {
    "NVDA": 0.95,
    "JPM": 0.85,
    "WMT": 0.60,
    "GE": 0.50,
    "DG": 0.30,
}

COMPANY_SECTORS: Dict[str, str] = {
    "NVDA": "technology",
    "JPM": "financial_services",
    "WMT": "retail",
    "GE": "manufacturing",
    "DG": "retail",
}

EXPECTED_PF_RANGES: Dict[str, tuple] = {
    "NVDA": (0.7, 1.0),
    "JPM": (0.3, 0.7),
    "WMT": (0.1, 0.5),
    "GE": (-0.2, 0.2),
    "DG": (-0.5, -0.1),
}

EXPECTED_HR_RANGES: Dict[str, tuple] = {
    "NVDA": (82.9, 86.3),
    "JPM": (71.1, 75.1),
    "WMT": (55.8, 59.1),
    "GE": (50.4, 53.6),
    "DG": (50.9, 54.2),
}

EXPECTED_ORGAIR_RANGES: Dict[str, tuple] = {
    "NVDA": (85.0, 95.0),
    "JPM":  (65.0, 75.0),
    "WMT":  (55.0, 65.0),
    "GE":   (45.0, 55.0),
    "DG":   (35.0, 45.0),
}

EXPECTED_TC_RANGES: Dict[str, tuple] = {
    "NVDA": (0.05, 0.20),
    "JPM":  (0.10, 0.25),
    "WMT":  (0.12, 0.28),
    "GE":   (0.18, 0.35),
    "DG":   (0.22, 0.40),
}

# Sector-specific timing factors (CS3 §6.3: TimingFactor ∈ [0.8, 1.2])
SECTOR_TIMING: Dict[str, float] = {
    "technology": 1.20,
    "financial_services": 1.05,
    "retail": 1.00,
    "manufacturing": 1.00,
}

COMPANY_NAMES: Dict[str, str] = {
    "NVDA": "NVIDIA Corporation",
    "JPM": "JPMorgan Chase & Co.",
    "WMT": "Walmart Inc.",
    "GE": "GE Aerospace",
    "DG": "Dollar General Corporation",
}


# =====================================================================
# Computation Response Models (moved from routers)
# =====================================================================

class JobAnalysisOutput(BaseModel):
    total_ai_jobs: int
    senior_ai_jobs: int
    mid_ai_jobs: int
    entry_ai_jobs: int
    unique_skills: List[str]


class TCBreakdown(BaseModel):
    leadership_ratio: float
    team_size_factor: float
    skill_concentration: float
    individual_factor: float


class VRBreakdownOutput(BaseModel):
    vr_score: float
    weighted_dim_score: float
    talent_risk_adj: float


class ValidationOutput(BaseModel):
    tc_in_range: bool
    tc_expected: str
    vr_in_range: bool
    vr_expected: str


class TCVRResponse(BaseModel):
    ticker: str
    status: str  # "success" or "failed"

    # TC outputs
    talent_concentration: Optional[float] = None
    tc_breakdown: Optional[TCBreakdown] = None

    # Job analysis
    job_analysis: Optional[JobAnalysisOutput] = None

    # Glassdoor
    individual_mentions: Optional[int] = None
    review_count: Optional[int] = None
    ai_mentions: Optional[int] = None

    # VR outputs
    vr_result: Optional[VRBreakdownOutput] = None

    # Dimension scores used
    dimension_scores: Optional[Dict[str, float]] = None

    # Validation against CS3 Table 5
    validation: Optional[ValidationOutput] = None

    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    scored_at: Optional[str] = None


class PFBreakdown(BaseModel):
    """Position Factor calculation breakdown."""
    vr_score: float
    sector_avg_vr: float
    vr_diff: float
    vr_component: float
    market_cap_percentile: float
    mcap_component: float
    position_factor: float


class PFValidation(BaseModel):
    """Validation against expected PF ranges."""
    pf_in_range: bool
    pf_expected: str
    status: str  # "✅", "⚠️", or "—"


class PFResponse(BaseModel):
    """Single company Position Factor response."""
    ticker: str
    status: str  # "success" or "failed"

    position_factor: Optional[float] = None
    pf_breakdown: Optional[PFBreakdown] = None
    validation: Optional[PFValidation] = None

    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    scored_at: Optional[str] = None


class HRBreakdown(BaseModel):
    """H^R calculation breakdown."""
    hr_score: float
    hr_base: float
    position_factor: float
    position_adjustment: float
    sector: str
    interpretation: str


class HRValidation(BaseModel):
    """Validation against expected HR ranges."""
    hr_in_range: bool
    hr_expected: str
    status: str  # "✅", "⚠️", or "—"


class HRResponse(BaseModel):
    """Single company H^R response."""
    ticker: str
    status: str  # "success" or "failed"

    hr_score: Optional[float] = None
    hr_breakdown: Optional[HRBreakdown] = None
    validation: Optional[HRValidation] = None

    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    scored_at: Optional[str] = None


class CIBreakdown(BaseModel):
    ci_lower: float
    ci_upper: float
    sem: float
    reliability: float
    score_type: str


class OrgAIRBreakdown(BaseModel):
    org_air_score: float
    vr_score: float
    hr_score: float
    synergy_score: float
    weighted_base: float
    synergy_contribution: float
    vr_weighted: float
    hr_weighted: float
    alpha: float
    beta: float
    vr_ci: Optional[CIBreakdown] = None
    hr_ci: Optional[CIBreakdown] = None
    orgair_ci: Optional[CIBreakdown] = None


class OrgAIRValidation(BaseModel):
    orgair_in_range: bool
    orgair_expected: str
    status: str


class OrgAIRResponse(BaseModel):
    ticker: str
    status: str

    org_air_score: Optional[float] = None
    breakdown: Optional[OrgAIRBreakdown] = None
    validation: Optional[OrgAIRValidation] = None

    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    scored_at: Optional[str] = None


# =====================================================================
# CompositeScoringService
# =====================================================================

class CompositeScoringService:
    """
    Orchestrates the composite scoring pipeline:
      TC → V^R → PF → H^R → Synergy → Org-AI-R

    All seven calculators are instantiated once and reused across requests.
    Each public compute_* method handles both computation and persistence
    (S3 + Snowflake), making every route handler a thin validate → call → return.
    """

    def __init__(self):
        from app.scoring.talent_concentration import TalentConcentrationCalculator
        from app.scoring.vr_calculator import VRCalculator
        from app.scoring.position_factor import PositionFactorCalculator
        from app.scoring.hr_calculator import HRCalculator
        from app.scoring.synergy_calculator import SynergyCalculator
        from app.scoring.orgair_calculator import OrgAIRCalculator
        from app.scoring.confidence_calculator import ConfidenceCalculator

        self._tc_calc = TalentConcentrationCalculator()
        self._vr_calc = VRCalculator()
        self._pf_calc = PositionFactorCalculator()
        self._hr_calc = HRCalculator()
        self._synergy = SynergyCalculator()
        self._orgair = OrgAIRCalculator()
        self._ci = ConfidenceCalculator()

    # ------------------------------------------------------------------
    # Shared S3 load helper (single copy — previously duplicated 4×)
    # ------------------------------------------------------------------

    def _load_jobs_s3(self, ticker: str, s3) -> list:
        """Load job postings from S3 — latest file under signals/jobs/{ticker}/."""
        prefix = f"signals/jobs/{ticker}/"
        try:
            keys = s3.list_files(prefix)
            for key in sorted(keys, reverse=True):
                raw = s3.get_file(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                postings = data.get("job_postings", [])
                if postings:
                    for p in postings:
                        if "ai_skills_found" not in p:
                            p["ai_skills_found"] = p.get("ai_keywords_found", [])
                    return postings
        except Exception as exc:
            logger.warning(f"[{ticker}] Job S3 load failed: {exc}")
        return []

    # ------------------------------------------------------------------
    # compute_tc_vr
    # ------------------------------------------------------------------

    def compute_tc_vr(self, ticker: str) -> TCVRResponse:
        """
        Load job + Glassdoor data from S3, compute TC + V^R, save to
        S3 + Snowflake, and return a TCVRResponse.
        """
        start = time.time()
        ticker = ticker.upper()

        try:
            from app.services.scoring_service import get_scoring_service
            scoring_svc = get_scoring_service()

            logger.info("=" * 60)
            logger.info(f"🎯 TC + V^R SCORING: {ticker}")
            logger.info("=" * 60)

            # ---- 1. Base dimension scores (5.0a + 5.0b) ----
            base_result = scoring_svc.score_company(ticker)
            dim_scores_list = base_result.get("dimension_scores", [])

            logger.info(f"[{ticker}] Base scoring complete — {len(dim_scores_list)} dimensions")
            for ds in dim_scores_list:
                logger.info(f"  {ds['dimension']:25s} = {ds['score']:6.2f}")

            # ---- 2. Load job postings from S3 ----
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()

            job_postings = self._load_jobs_s3(ticker, s3)
            logger.info(f"[{ticker}] Loaded {len(job_postings)} job postings from S3")

            job_analysis = self._tc_calc.analyze_job_postings(job_postings)
            logger.info(f"[{ticker}] Job Analysis:")
            logger.info(f"  Total AI jobs:  {job_analysis.total_ai_jobs}")
            logger.info(f"  Senior AI jobs: {job_analysis.senior_ai_jobs}")
            logger.info(f"  Mid AI jobs:    {job_analysis.mid_ai_jobs}")
            logger.info(f"  Entry AI jobs:  {job_analysis.entry_ai_jobs}")
            logger.info(
                f"  Unique skills:  {len(job_analysis.unique_skills)} "
                f"→ {sorted(job_analysis.unique_skills)[:10]}"
            )

            # ---- 3. Load Glassdoor reviews from S3 ----
            glassdoor_reviews = self._tc_calc.load_glassdoor_reviews(ticker, s3)
            logger.info(f"[{ticker}] Loaded {len(glassdoor_reviews)} Glassdoor reviews from S3")

            indiv_mentions, rev_count = self._tc_calc.count_individual_mentions(glassdoor_reviews)
            ai_mentions, _ = self._tc_calc.count_ai_mentions(glassdoor_reviews)
            logger.info(
                f"[{ticker}] Glassdoor: {indiv_mentions} individual mentions, "
                f"{ai_mentions} AI mentions out of {rev_count} reviews"
            )

            # ---- 4. Calculate TC ----
            tc = self._tc_calc.calculate_tc(job_analysis, indiv_mentions, rev_count)

            total = job_analysis.total_ai_jobs
            senior = job_analysis.senior_ai_jobs
            leadership_ratio = senior / total if total > 0 else 0.5
            team_size_factor = (
                min(1.0, 1.0 / (total ** 0.5 + 0.1)) if total > 0
                else min(1.0, 1.0 / 0.1)
            )
            skill_concentration = max(0.0, 1.0 - len(job_analysis.unique_skills) / 15)
            individual_factor = indiv_mentions / rev_count if rev_count > 0 else 0.5

            logger.info(f"[{ticker}] TC Breakdown:")
            logger.info(
                f"  leadership_ratio   = {leadership_ratio:.4f}  "
                f"(× 0.40 = {0.4 * leadership_ratio:.4f})"
            )
            logger.info(
                f"  team_size_factor   = {team_size_factor:.4f}  "
                f"(× 0.30 = {0.3 * team_size_factor:.4f})"
            )
            logger.info(
                f"  skill_concentration= {skill_concentration:.4f}  "
                f"(× 0.20 = {0.2 * skill_concentration:.4f})"
            )
            logger.info(
                f"  individual_factor  = {individual_factor:.4f}  "
                f"(× 0.10 = {0.1 * individual_factor:.4f})"
            )
            logger.info(f"  ───────────────────────────────────────")
            logger.info(f"  TC = {float(tc):.4f}")

            # ---- 5. Calculate V^R ----
            dim_score_dict = {row["dimension"]: row["score"] for row in dim_scores_list}
            vr_result = self._vr_calc.calculate(dim_score_dict, float(tc))

            logger.info(f"[{ticker}] V^R Calculation:")
            logger.info(f"  Weighted Dim Score = {vr_result.weighted_dim_score}")
            logger.info(f"  TalentRiskAdj      = {vr_result.talent_risk_adj}")
            logger.info(f"  V^R Score          = {vr_result.vr_score}")

            # ---- 6. Validate against CS3 Table 5 ----
            validation = None
            if ticker in EXPECTED_TC_VR_RANGES:
                exp = EXPECTED_TC_VR_RANGES[ticker]
                tc_ok = exp["tc"][0] <= float(tc) <= exp["tc"][1]
                vr_ok = exp["vr"][0] <= float(vr_result.vr_score) <= exp["vr"][1]
                validation = ValidationOutput(
                    tc_in_range=tc_ok,
                    tc_expected=f"{exp['tc'][0]:.2f} - {exp['tc'][1]:.2f}",
                    vr_in_range=vr_ok,
                    vr_expected=f"{exp['vr'][0]} - {exp['vr'][1]}",
                )
                tc_status = "✅" if tc_ok else "⚠️  OUT OF RANGE"
                vr_status = "✅" if vr_ok else "⚠️  OUT OF RANGE"
                logger.info(f"[{ticker}] Validation (CS3 Table 5):")
                logger.info(f"  TC  = {float(tc):.4f}  expected {exp['tc']}  {tc_status}")
                logger.info(
                    f"  V^R = {float(vr_result.vr_score):.2f}  "
                    f"expected {exp['vr']}  {vr_status}"
                )

            logger.info("=" * 60)

            result = TCVRResponse(
                ticker=ticker,
                status="success",
                talent_concentration=float(tc),
                tc_breakdown=TCBreakdown(
                    leadership_ratio=round(leadership_ratio, 4),
                    team_size_factor=round(team_size_factor, 4),
                    skill_concentration=round(skill_concentration, 4),
                    individual_factor=round(individual_factor, 4),
                ),
                job_analysis=JobAnalysisOutput(
                    total_ai_jobs=job_analysis.total_ai_jobs,
                    senior_ai_jobs=job_analysis.senior_ai_jobs,
                    mid_ai_jobs=job_analysis.mid_ai_jobs,
                    entry_ai_jobs=job_analysis.entry_ai_jobs,
                    unique_skills=sorted(job_analysis.unique_skills),
                ),
                individual_mentions=indiv_mentions,
                review_count=rev_count,
                ai_mentions=ai_mentions,
                vr_result=VRBreakdownOutput(
                    vr_score=float(vr_result.vr_score),
                    weighted_dim_score=float(vr_result.weighted_dim_score),
                    talent_risk_adj=float(vr_result.talent_risk_adj),
                ),
                dimension_scores=dim_score_dict,
                validation=validation,
                duration_seconds=round(time.time() - start, 2),
                scored_at=datetime.now(timezone.utc).isoformat(),
            )

            self._save_tc_vr(result)
            return result

        except Exception as e:
            logger.error(f"TC+VR scoring failed for {ticker}: {e}", exc_info=True)
            return TCVRResponse(
                ticker=ticker,
                status="failed",
                error=str(e),
                duration_seconds=round(time.time() - start, 2),
            )

    # ------------------------------------------------------------------
    # compute_pf
    # ------------------------------------------------------------------

    def compute_pf(self, ticker: str) -> PFResponse:
        """
        Compute Position Factor: call compute_tc_vr to get V^R, run PF
        calculator, save, and return PFResponse.
        """
        start = time.time()
        ticker = ticker.upper()

        try:
            logger.info("=" * 60)
            logger.info(f"📍 POSITION FACTOR CALCULATION: {ticker}")
            logger.info("=" * 60)

            # ---- 1. Get V^R (saves TC+VR as a side effect) ----
            tc_vr_result = self.compute_tc_vr(ticker)
            if tc_vr_result.status != "success":
                raise PipelineIncompleteError(ticker, ["tc_vr"])

            vr_score = tc_vr_result.vr_result.vr_score

            # ---- 2. Manual inputs ----
            market_cap_percentile = MARKET_CAP_PERCENTILES.get(ticker)
            if market_cap_percentile is None:
                raise NotFoundError("market_cap_percentile", ticker)

            sector = COMPANY_SECTORS.get(ticker)
            if sector is None:
                raise NotFoundError("sector", ticker)

            logger.info(f"[{ticker}] VR Score: {vr_score:.2f}")
            logger.info(
                f"[{ticker}] Market Cap Percentile (manual): {market_cap_percentile:.2f}"
            )
            logger.info(f"[{ticker}] Sector: {sector}")

            # ---- 3. Calculate PF ----
            pf = self._pf_calc.calculate_position_factor(
                vr_score=float(vr_score),
                sector=sector,
                market_cap_percentile=market_cap_percentile,
            )

            sector_avg = self._pf_calc.SECTOR_AVG_VR.get(sector.lower(), 50.0)
            vr_diff = vr_score - sector_avg
            vr_component = max(-1, min(1, vr_diff / 50))
            mcap_component = (market_cap_percentile - 0.5) * 2

            logger.info(f"[{ticker}] Position Factor Breakdown:")
            logger.info(f"  VR Score           = {vr_score:.2f}")
            logger.info(f"  Sector Avg VR      = {sector_avg:.2f}")
            logger.info(f"  VR Difference      = {vr_diff:.2f}")
            logger.info(
                f"  VR Component       = {vr_component:.4f}  "
                f"(× 0.60 = {0.6 * vr_component:.4f})"
            )
            logger.info(f"  MCap Percentile    = {market_cap_percentile:.2f}")
            logger.info(
                f"  MCap Component     = {mcap_component:.4f}  "
                f"(× 0.40 = {0.4 * mcap_component:.4f})"
            )
            logger.info(f"  ───────────────────────────────────────")
            logger.info(f"  Position Factor    = {float(pf):.4f}")

            # ---- 4. Validate against expected range ----
            validation = None
            if ticker in EXPECTED_PF_RANGES:
                exp_lo, exp_hi = EXPECTED_PF_RANGES[ticker]
                pf_ok = exp_lo <= float(pf) <= exp_hi
                status = "✅" if pf_ok else "⚠️"
                validation = PFValidation(
                    pf_in_range=pf_ok,
                    pf_expected=f"{exp_lo:.1f} to {exp_hi:.1f}",
                    status=status,
                )
                logger.info(f"[{ticker}] Validation (CS3 Table 5):")
                logger.info(
                    f"  PF = {float(pf):.4f}  "
                    f"expected [{exp_lo:.1f}, {exp_hi:.1f}]  {status}"
                )

            logger.info("=" * 60)

            result = PFResponse(
                ticker=ticker,
                status="success",
                position_factor=float(pf),
                pf_breakdown=PFBreakdown(
                    vr_score=vr_score,
                    sector_avg_vr=sector_avg,
                    vr_diff=vr_diff,
                    vr_component=vr_component,
                    market_cap_percentile=market_cap_percentile,
                    mcap_component=mcap_component,
                    position_factor=float(pf),
                ),
                validation=validation,
                duration_seconds=round(time.time() - start, 2),
                scored_at=datetime.now(timezone.utc).isoformat(),
            )

            self._save_pf(result)
            return result

        except Exception as e:
            logger.error(
                f"Position Factor calculation failed for {ticker}: {e}", exc_info=True
            )
            return PFResponse(
                ticker=ticker,
                status="failed",
                error=str(e),
                duration_seconds=round(time.time() - start, 2),
            )

    # ------------------------------------------------------------------
    # compute_hr
    # ------------------------------------------------------------------

    def compute_hr(self, ticker: str) -> HRResponse:
        """
        Compute H^R: call compute_tc_vr to get V^R, compute PF inline
        (not saved separately), run HR calculator, save, return HRResponse.
        """
        start = time.time()
        ticker = ticker.upper()

        try:
            logger.info("=" * 60)
            logger.info(f"🌐 H^R CALCULATION: {ticker}")
            logger.info("=" * 60)

            # ---- 1. Get V^R (saves TC+VR as a side effect) ----
            tc_vr_result = self.compute_tc_vr(ticker)
            if tc_vr_result.status != "success":
                raise PipelineIncompleteError(ticker, ["tc_vr"])

            vr_score = tc_vr_result.vr_result.vr_score

            # ---- 2. Compute PF inline ----
            sector = COMPANY_SECTORS.get(ticker)
            if sector is None:
                raise NotFoundError("sector", ticker)

            market_cap_percentile = MARKET_CAP_PERCENTILES.get(ticker, 0.50)
            position_factor = float(
                self._pf_calc.calculate_position_factor(
                    vr_score=float(vr_score),
                    sector=sector,
                    market_cap_percentile=market_cap_percentile,
                )
            )

            logger.info(f"[{ticker}] Sector: {sector}")
            logger.info(
                f"[{ticker}] Position Factor (computed inline): {position_factor:.4f}"
            )

            # ---- 3. Calculate H^R ----
            hr_result = self._hr_calc.calculate(
                sector=sector, position_factor=position_factor
            )
            interpretation = self._hr_calc.interpret_hr_score(float(hr_result.hr_score))

            logger.info(f"[{ticker}] H^R Breakdown:")
            logger.info(f"  Sector             = {sector}")
            logger.info(f"  HR Base            = {float(hr_result.hr_base):.2f}")
            logger.info(f"  Position Factor    = {position_factor:.4f}")
            logger.info(
                f"  Position Adj (δ×PF)= {float(hr_result.position_adjustment):.4f}"
            )
            logger.info(f"  ───────────────────────────────────────")
            logger.info(f"  H^R Score          = {float(hr_result.hr_score):.2f}")
            logger.info(f"  Interpretation     = {interpretation}")

            # ---- 4. Validate against expected range ----
            validation = None
            if ticker in EXPECTED_HR_RANGES:
                exp_lo, exp_hi = EXPECTED_HR_RANGES[ticker]
                hr_ok = exp_lo <= float(hr_result.hr_score) <= exp_hi
                status = "✅" if hr_ok else "⚠️"
                validation = HRValidation(
                    hr_in_range=hr_ok,
                    hr_expected=f"{exp_lo:.1f} to {exp_hi:.1f}",
                    status=status,
                )
                logger.info(f"[{ticker}] Validation (CS3 Expected Range):")
                logger.info(
                    f"  H^R = {float(hr_result.hr_score):.2f}  "
                    f"expected [{exp_lo:.1f}, {exp_hi:.1f}]  {status}"
                )

            logger.info("=" * 60)

            result = HRResponse(
                ticker=ticker,
                status="success",
                hr_score=float(hr_result.hr_score),
                hr_breakdown=HRBreakdown(
                    hr_score=float(hr_result.hr_score),
                    hr_base=float(hr_result.hr_base),
                    position_factor=position_factor,
                    position_adjustment=float(hr_result.position_adjustment),
                    sector=sector,
                    interpretation=interpretation,
                ),
                validation=validation,
                duration_seconds=round(time.time() - start, 2),
                scored_at=datetime.now(timezone.utc).isoformat(),
            )

            self._save_hr(result)
            return result

        except Exception as e:
            logger.error(f"H^R calculation failed for {ticker}: {e}", exc_info=True)
            return HRResponse(
                ticker=ticker,
                status="failed",
                error=str(e),
                duration_seconds=round(time.time() - start, 2),
            )

    # ------------------------------------------------------------------
    # compute_orgair
    # ------------------------------------------------------------------

    def compute_orgair(
        self,
        ticker: str,
        precomputed_vr: Optional[TCVRResponse] = None,
    ) -> OrgAIRResponse:
        """
        Compute Org-AI-R.  Uses precomputed_vr if provided to avoid a second
        TC+VR pipeline execution (critical invariant for compute_full_pipeline).
        """
        start = time.time()
        ticker = ticker.upper()

        try:
            logger.info("=" * 60)
            logger.info(f"Org-AI-R CALCULATION: {ticker}")
            logger.info("=" * 60)

            # ---- 1. Get V^R ----
            vr_response = (
                precomputed_vr if precomputed_vr is not None
                else self.compute_tc_vr(ticker)
            )
            if vr_response.status != "success":
                raise PipelineIncompleteError(ticker, ["vr"])

            vr_score = vr_response.vr_result.vr_score if vr_response.vr_result else None
            if vr_score is None:
                raise PipelineIncompleteError(ticker, ["vr"])

            logger.info(f"[{ticker}] V^R = {vr_score:.2f}")

            # ---- 2. H^R (direct — avoids second pipeline run) ----
            sector = COMPANY_SECTORS.get(ticker, "")
            mcap = MARKET_CAP_PERCENTILES.get(ticker, 0.50)

            pf = self._pf_calc.calculate_position_factor(vr_score, sector, mcap)
            hr_result = self._hr_calc.calculate(sector, float(pf))
            hr_score = float(hr_result.hr_score)

            logger.info(f"[{ticker}] PF = {float(pf):.4f}")
            logger.info(f"[{ticker}] H^R = {hr_score:.2f}")

            # ---- 3. Synergy ----
            timing = SECTOR_TIMING.get(sector, 1.0)
            synergy_result = self._synergy.calculate(
                vr_score, hr_score, timing_factor=timing
            )
            synergy_score = float(synergy_result.synergy_score)
            logger.info(f"[{ticker}] Synergy = {synergy_score:.2f}")

            # ---- 4. Org-AI-R ----
            orgair_result = self._orgair.calculate(vr_score, hr_score, synergy_score)
            org_air_score = float(orgair_result.org_air_score)
            logger.info(f"[{ticker}] Org-AI-R = {org_air_score:.2f}")

            # ---- 5. Confidence Intervals ----
            vr_ci_result = self._ci.calculate(vr_score, 7, "vr")
            hr_ci_result = self._ci.calculate(hr_score, 7, "hr")
            orgair_ci_result = self._ci.calculate(org_air_score, 7, "org_air")

            vr_ci = CIBreakdown(
                ci_lower=float(vr_ci_result.ci_lower),
                ci_upper=float(vr_ci_result.ci_upper),
                sem=float(vr_ci_result.sem),
                reliability=float(vr_ci_result.reliability),
                score_type="vr",
            )
            hr_ci = CIBreakdown(
                ci_lower=float(hr_ci_result.ci_lower),
                ci_upper=float(hr_ci_result.ci_upper),
                sem=float(hr_ci_result.sem),
                reliability=float(hr_ci_result.reliability),
                score_type="hr",
            )
            orgair_ci = CIBreakdown(
                ci_lower=float(orgair_ci_result.ci_lower),
                ci_upper=float(orgair_ci_result.ci_upper),
                sem=float(orgair_ci_result.sem),
                reliability=float(orgair_ci_result.reliability),
                score_type="org_air",
            )

            logger.info(
                f"[{ticker}] Org-AI-R CI = [{float(orgair_ci_result.ci_lower):.2f}, "
                f"{float(orgair_ci_result.ci_upper):.2f}]  "
                f"reliability={float(orgair_ci_result.reliability):.4f}"
            )

            # ---- 6. Validate ----
            validation = None
            if ticker in EXPECTED_ORGAIR_RANGES:
                exp_lo, exp_hi = EXPECTED_ORGAIR_RANGES[ticker]
                in_range = exp_lo <= org_air_score <= exp_hi
                status = "✅" if in_range else "⚠️"
                validation = OrgAIRValidation(
                    orgair_in_range=in_range,
                    orgair_expected=f"{exp_lo:.1f} to {exp_hi:.1f}",
                    status=status,
                )
                logger.info(
                    f"[{ticker}] Validation: Org-AI-R={org_air_score:.2f} "
                    f"expected [{exp_lo:.1f}, {exp_hi:.1f}]  {status}"
                )

            breakdown = OrgAIRBreakdown(
                org_air_score=org_air_score,
                vr_score=vr_score,
                hr_score=hr_score,
                synergy_score=synergy_score,
                weighted_base=float(orgair_result.weighted_base),
                synergy_contribution=float(orgair_result.synergy_contribution),
                vr_weighted=float(orgair_result.vr_weighted),
                hr_weighted=float(orgair_result.hr_weighted),
                alpha=float(orgair_result.alpha),
                beta=float(orgair_result.beta),
                vr_ci=vr_ci,
                hr_ci=hr_ci,
                orgair_ci=orgair_ci,
            )

            logger.info("=" * 60)

            result = OrgAIRResponse(
                ticker=ticker,
                status="success",
                org_air_score=org_air_score,
                breakdown=breakdown,
                validation=validation,
                duration_seconds=round(time.time() - start, 2),
                scored_at=datetime.now(timezone.utc).isoformat(),
            )

            self._save_orgair(result)
            return result

        except Exception as e:
            logger.error(
                f"Org-AI-R calculation failed for {ticker}: {e}", exc_info=True
            )
            return OrgAIRResponse(
                ticker=ticker,
                status="failed",
                error=str(e),
                duration_seconds=round(time.time() - start, 2),
            )

    # ------------------------------------------------------------------
    # compute_full_pipeline
    # ------------------------------------------------------------------

    def compute_full_pipeline(self, tickers: List[str]) -> dict:
        """
        Run the full pipeline for each ticker:
          compute_tc_vr once → compute_orgair(precomputed_vr=…) → write results JSON.

        Critical invariant: compute_tc_vr is called exactly once per ticker.

        Returns a dict with keys: files_generated, local_files, s3_files,
        summary, duration_seconds — suitable for ResultsGenerationResponse.
        """
        start = time.time()

        logger.info("=" * 70)
        logger.info("GENERATING CS3 RESULTS JSON FILES")
        logger.info("=" * 70)

        results_dir = Path("results")
        results_dir.mkdir(exist_ok=True)

        local_files = []
        s3_files = []
        summary = []
        files_generated = 0

        for ticker in tickers:
            logger.info(f"\n{'─'*50}")
            logger.info(f"Scoring {ticker}...")

            # 1. TC+VR once — reuse for Org-AI-R
            tc_vr = self.compute_tc_vr(ticker)

            tc_val = tc_vr.talent_concentration if tc_vr else None
            tc_breakdown = tc_vr.tc_breakdown if tc_vr else None
            dim_scores = tc_vr.dimension_scores if tc_vr else None
            job_analysis = tc_vr.job_analysis if tc_vr else None

            # 2. Org-AI-R (pass pre-computed VR — no second pipeline run)
            response = self.compute_orgair(ticker, precomputed_vr=tc_vr)
            if response.status != "success":
                logger.error(f"[{ticker}] FAILED: {response.error}")
                continue

            b = response.breakdown

            # 3. Get PF for result JSON
            sector = COMPANY_SECTORS.get(ticker, "")
            mcap = MARKET_CAP_PERCENTILES.get(ticker, 0.50)
            pf = float(
                self._pf_calc.calculate_position_factor(b.vr_score, sector, mcap)
            )

            # 4. Validation ranges
            org_air_range = EXPECTED_ORGAIR_RANGES.get(ticker, (0, 100))
            tc_range = EXPECTED_TC_RANGES.get(ticker, (0, 1))
            pf_range = EXPECTED_PF_RANGES.get(ticker, (-1, 1))

            # 5. Build result JSON
            result_data = {
                "ticker": ticker,
                "company_name": COMPANY_NAMES.get(ticker, ticker),
                "sector": sector,
                "scored_at": datetime.now(timezone.utc).isoformat(),

                "org_air_score": b.org_air_score,
                "org_air_ci": {
                    "lower": b.orgair_ci.ci_lower if b.orgair_ci else None,
                    "upper": b.orgair_ci.ci_upper if b.orgair_ci else None,
                    "sem": b.orgair_ci.sem if b.orgair_ci else None,
                    "reliability": b.orgair_ci.reliability if b.orgair_ci else None,
                },

                "vr_score": b.vr_score,
                "vr_ci": {
                    "lower": b.vr_ci.ci_lower if b.vr_ci else None,
                    "upper": b.vr_ci.ci_upper if b.vr_ci else None,
                },

                "hr_score": b.hr_score,
                "hr_ci": {
                    "lower": b.hr_ci.ci_lower if b.hr_ci else None,
                    "upper": b.hr_ci.ci_upper if b.hr_ci else None,
                },

                "synergy_score": b.synergy_score,

                "formula": {
                    "alpha": b.alpha,
                    "beta": b.beta,
                    "vr_weighted": b.vr_weighted,
                    "hr_weighted": b.hr_weighted,
                    "weighted_base": b.weighted_base,
                    "synergy_contribution": b.synergy_contribution,
                },

                "position_factor": pf,
                "market_cap_percentile": mcap,

                "talent_concentration": float(tc_val) if tc_val is not None else None,
                "tc_breakdown": tc_breakdown.model_dump() if tc_breakdown else None,
                "dimension_scores": dim_scores if dim_scores else None,
                "job_analysis": job_analysis.model_dump() if job_analysis else None,

                "validation": {
                    "org_air_in_range": org_air_range[0] <= b.org_air_score <= org_air_range[1],
                    "org_air_expected": f"{org_air_range[0]:.1f} - {org_air_range[1]:.1f}",
                    "tc_in_range": (
                        tc_range[0] <= float(tc_val) <= tc_range[1]
                        if tc_val is not None else None
                    ),
                    "tc_expected": f"{tc_range[0]} - {tc_range[1]}",
                    "pf_in_range": pf_range[0] <= pf <= pf_range[1],
                    "pf_expected": f"{pf_range[0]} - {pf_range[1]}",
                },
            }

            # 6. Save locally
            local_path = results_dir / f"{ticker.lower()}.json"
            local_path.write_text(
                json.dumps(result_data, indent=2, default=str), encoding="utf-8"
            )
            local_files.append(str(local_path))
            logger.info(f"[{ticker}] ✅ Local: {local_path}")

            # 7. Save to S3
            try:
                from app.services.s3_storage import get_s3_service
                s3 = get_s3_service()
                s3_key = f"scoring/results/{ticker.lower()}.json"
                s3.upload_json(result_data, s3_key)
                s3_files.append(s3_key)
                logger.info(f"[{ticker}] ✅ S3: {s3_key}")
            except Exception as e:
                logger.warning(f"[{ticker}] S3 upload failed (non-fatal): {e}")

            files_generated += 1

            summary.append({
                "ticker": ticker,
                "org_air_score": b.org_air_score,
                "vr_score": b.vr_score,
                "hr_score": b.hr_score,
                "synergy_score": b.synergy_score,
                "tc": float(tc_val) if tc_val is not None else None,
                "pf": pf,
                "in_range": result_data["validation"]["org_air_in_range"],
            })

        # 8. Portfolio summary
        portfolio_summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "CS3 Org-AI-R Scoring Engine",
            "companies": files_generated,
            "all_in_range": all(s["in_range"] for s in summary),
            "results": summary,
        }

        summary_local = results_dir / "portfolio_summary.json"
        summary_local.write_text(
            json.dumps(portfolio_summary, indent=2, default=str), encoding="utf-8"
        )
        local_files.append(str(summary_local))

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            s3.upload_json(portfolio_summary, "scoring/results/portfolio_summary.json")
            s3_files.append("scoring/results/portfolio_summary.json")
        except Exception:
            pass

        # 9. Final summary table
        logger.info(f"\n{'='*70}")
        logger.info(f"CS3 RESULTS — FINAL SCORES")
        logger.info(f"{'='*70}")
        logger.info(
            f"{'Ticker':<6} {'Org-AI-R':>9} {'V^R':>7} {'H^R':>7} "
            f"{'Syn':>7} {'TC':>7} {'PF':>7} {'Range':>12} {'✓':>3}"
        )
        logger.info(
            f"{'-'*6} {'-'*9} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*12} {'-'*3}"
        )
        for s in summary:
            exp = EXPECTED_ORGAIR_RANGES.get(s["ticker"], (0, 100))
            status = "✅" if s["in_range"] else "⚠️"
            tc_display = s['tc'] if s['tc'] is not None else 0.0
            logger.info(
                f"{s['ticker']:<6} {s['org_air_score']:>9.2f} {s['vr_score']:>7.2f} "
                f"{s['hr_score']:>7.2f} {s['synergy_score']:>7.2f} "
                f"{tc_display:>7.4f} {s['pf']:>7.4f} "
                f"{exp[0]:.0f}-{exp[1]:.0f}:>12 {status:>3}"
            )
        logger.info(f"{'='*70}")

        passed = sum(1 for s in summary if s["in_range"])
        logger.info(f"Validation: {passed}/{len(summary)} within expected range")
        logger.info(f"Files: {len(local_files)} local, {len(s3_files)} S3")
        logger.info(f"Duration: {time.time() - start:.2f}s")

        return {
            "files_generated": files_generated,
            "local_files": local_files,
            "s3_files": s3_files,
            "summary": summary,
            "duration_seconds": round(time.time() - start, 2),
        }

    # ------------------------------------------------------------------
    # Save helpers (private)
    # ------------------------------------------------------------------

    def _save_tc_vr(self, result: TCVRResponse) -> None:
        """Save TC+VR result to S3 and upsert into SCORING, TC_SCORING, VR_SCORING."""
        ticker = result.ticker

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            s3_key = f"scoring/tc_vr/{ticker}/{ts}.json"
            s3.upload_json(result.model_dump(), s3_key)
            logger.info(f"[{ticker}] TC+VR result saved to S3: {s3_key}")
        except Exception as e:
            logger.warning(f"[{ticker}] S3 save failed (non-fatal): {e}")

        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        repo = CompositeScoringRepository()

        try:
            bd = result.tc_breakdown
            ja = result.job_analysis
            val = result.validation
            vr_r = result.vr_result
            dims = result.dimension_scores or {}
            repo.upsert_tc_vr_batch(
                ticker=ticker,
                tc=result.talent_concentration,
                vr=vr_r.vr_score if vr_r else None,
                leadership_ratio=bd.leadership_ratio if bd else None,
                team_size_factor=bd.team_size_factor if bd else None,
                skill_concentration=bd.skill_concentration if bd else None,
                individual_factor=bd.individual_factor if bd else None,
                total_ai_jobs=ja.total_ai_jobs if ja else None,
                senior_ai_jobs=ja.senior_ai_jobs if ja else None,
                mid_ai_jobs=ja.mid_ai_jobs if ja else None,
                entry_ai_jobs=ja.entry_ai_jobs if ja else None,
                unique_skills_cnt=len(ja.unique_skills) if ja else None,
                individual_mentions=result.individual_mentions,
                review_count=result.review_count,
                ai_mentions=result.ai_mentions,
                tc_in_range=val.tc_in_range if val else None,
                tc_expected=val.tc_expected if val else None,
                vr_score=vr_r.vr_score if vr_r else None,
                weighted_dim_score=vr_r.weighted_dim_score if vr_r else None,
                talent_risk_adj=vr_r.talent_risk_adj if vr_r else None,
                dim_data_infra=dims.get("data_infrastructure"),
                dim_ai_gov=dims.get("ai_governance"),
                dim_tech_stack=dims.get("technology_stack"),
                dim_talent=dims.get("talent_skills"),
                dim_leadership=dims.get("leadership_vision"),
                dim_use_case=dims.get("use_case_portfolio"),
                dim_culture=dims.get("culture_change"),
                vr_in_range=val.vr_in_range if val else None,
                vr_expected=val.vr_expected if val else None,
            )
            logger.info(f"[{ticker}] SCORING + TC_SCORING + VR_SCORING upserted")
        except Exception as e:
            logger.warning(f"[{ticker}] TC+VR Snowflake upsert failed (non-fatal): {e}")

    def _save_pf(self, result: PFResponse) -> None:
        """Save PF result to S3 and upsert into SCORING and PF_SCORING."""
        ticker = result.ticker

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            s3_key = f"scoring/pf/{ticker}/{ts}.json"
            s3.upload_json(result.model_dump(), s3_key)
            logger.info(f"[{ticker}] PF result saved to S3: {s3_key}")
        except Exception as e:
            logger.warning(f"[{ticker}] S3 save failed (non-fatal): {e}")

        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        repo = CompositeScoringRepository()

        try:
            repo.upsert_scoring_pf(ticker, result.position_factor)
            logger.info(f"[{ticker}] SCORING table upserted: PF={result.position_factor}")
        except Exception as e:
            logger.warning(f"[{ticker}] Snowflake SCORING upsert failed (non-fatal): {e}")

        try:
            bd = result.pf_breakdown
            val = result.validation
            repo.upsert_pf_result(
                ticker,
                position_factor=result.position_factor,
                vr_score_used=bd.vr_score if bd else None,
                sector=COMPANY_SECTORS.get(ticker.upper()),
                sector_avg_vr=bd.sector_avg_vr if bd else None,
                vr_diff=bd.vr_diff if bd else None,
                vr_component=bd.vr_component if bd else None,
                market_cap_percentile=bd.market_cap_percentile if bd else None,
                mcap_component=bd.mcap_component if bd else None,
                pf_in_range=val.pf_in_range if val else None,
                pf_expected=val.pf_expected if val else None,
            )
            logger.info(f"[{ticker}] PF_SCORING table upserted")
        except Exception as e:
            logger.warning(f"[{ticker}] PF_SCORING upsert failed (non-fatal): {e}")

    def _save_hr(self, result: HRResponse) -> None:
        """Save H^R result to S3 and upsert into SCORING and HR_SCORING."""
        ticker = result.ticker

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            s3_key = f"scoring/hr/{ticker}/{ts}.json"
            s3.upload_json(result.model_dump(), s3_key)
            logger.info(f"[{ticker}] H^R result saved to S3: {s3_key}")
        except Exception as e:
            logger.warning(f"[{ticker}] S3 save failed (non-fatal): {e}")

        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        repo = CompositeScoringRepository()

        try:
            repo.upsert_scoring_hr(ticker, result.hr_score)
            logger.info(f"[{ticker}] SCORING table upserted: HR={result.hr_score}")
        except Exception as e:
            logger.warning(f"[{ticker}] Snowflake SCORING upsert failed (non-fatal): {e}")

        try:
            bd = result.hr_breakdown
            val = result.validation
            repo.upsert_hr_result(
                ticker,
                hr_score=result.hr_score,
                hr_base=bd.hr_base if bd else None,
                position_factor_used=bd.position_factor if bd else None,
                position_adjustment=bd.position_adjustment if bd else None,
                sector=bd.sector if bd else None,
                interpretation=bd.interpretation if bd else None,
                hr_in_range=val.hr_in_range if val else None,
                hr_expected=val.hr_expected if val else None,
            )
            logger.info(f"[{ticker}] HR_SCORING table upserted")
        except Exception as e:
            logger.warning(f"[{ticker}] HR_SCORING upsert failed (non-fatal): {e}")

    def _save_orgair(self, result: OrgAIRResponse) -> None:
        """Save Org-AI-R result to S3 and upsert into SCORING table."""
        ticker = result.ticker

        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            s3_key = f"scoring/orgair/{ticker}/{ts}.json"
            s3.upload_json(result.model_dump(), s3_key)
            logger.info(f"  ✅ Uploaded to S3: {s3_key}")
        except Exception as e:
            logger.warning(f"[{ticker}] S3 save failed (non-fatal): {e}")

        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        repo = CompositeScoringRepository()

        try:
            b = result.breakdown
            if b:
                ci_lower = b.orgair_ci.ci_lower if b.orgair_ci else None
                ci_upper = b.orgair_ci.ci_upper if b.orgair_ci else None
                repo.upsert_orgair_result(
                    ticker,
                    org_air=b.org_air_score,
                    vr_score=b.vr_score,
                    hr_score=b.hr_score,
                    synergy_score=b.synergy_score,
                    ci_lower=ci_lower,
                    ci_upper=ci_upper,
                )
            logger.info(f"[{ticker}] SCORING table upserted: org_air={result.org_air_score}")
        except Exception as e:
            logger.warning(f"[{ticker}] Snowflake SCORING upsert failed (non-fatal): {e}")


get_composite_scoring_service = make_singleton_factory(CompositeScoringService)
