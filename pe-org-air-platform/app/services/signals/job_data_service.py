
"""
Job Data Service
app/services/job_data_service.py

Collects and caches raw job data for JobSignalService.

FIX: analyze_job_market() now returns total_tech_jobs, confidence,
     score_breakdown, and ai_skills from the pipeline's
     state.job_market_analyses dict (computed by calculate_job_score).
"""
from __future__ import annotations

import structlog
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.pipelines.job_signals import (
    step1_init,
    step2_fetch_job_postings,
    step3_classify_ai_jobs,
    step4_score_job_market,
)
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError, ValidationError
from app.pipelines.signal_pipeline_state import SignalPipelineState
from app.services.s3_storage import get_s3_service
from app.repositories.company_repository import CompanyRepository

logger = structlog.get_logger()


class JobDataService:
    """Service to collect and cache raw job data for JobSignalService."""

    def __init__(self, company_repo=None):
        self.s3_service = get_s3_service()
        self.company_repo = company_repo or CompanyRepository()
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = timedelta(hours=1)

    def _deduplicate(self, postings: list) -> list:
        """Remove duplicate job postings using URL and title+location keys."""
        seen = {}
        unique = []
        for p in postings:
            url = (p.get("url") or "").strip().lower()
            title = p.get("title", "").strip().lower()
            company = p.get("company_name", "").strip().lower()
            location = (p.get("location") or "").strip().lower()

            if url and "jk=" in url:
                jk = url.split("jk=")[-1].split("&")[0]
                url_key = f"indeed|{jk}"
            elif url:
                url_key = url.split("?")[0]
            else:
                url_key = None

            content_key = f"{title}|{company}|{location}"

            if url_key and url_key in seen:
                continue
            if content_key in seen:
                continue

            if url_key:
                seen[url_key] = True
            seen[content_key] = True
            unique.append(p)

        return unique

    async def collect_job_data(
        self, ticker: str, force_refresh: bool = False
    ) -> Dict[str, Any]:
        ticker = ticker.upper()

        cache_key = f"job_data_{ticker}"
        if not force_refresh and cache_key in self._cache:
            cached = self._cache[cache_key]
            cache_time = cached.get("collected_at")
            if cache_time and datetime.fromisoformat(cache_time) > datetime.now(timezone.utc) - self._cache_ttl:
                logger.info(f"Using cached job data for {ticker}")
                return cached

        logger.info(f"Collecting fresh job data for {ticker}")

        company = self.company_repo.get_by_ticker(ticker)
        if not company:
            raise NotFoundError("company", ticker)

        company_id = str(company["id"])
        company_name = company["name"]

        state = SignalPipelineState(
            companies=[{"id": company_id, "name": company_name, "ticker": ticker}],
        )

        try:
            state = step1_init(state)
            state = await step2_fetch_job_postings(state)

            before_dedup = len(state.job_postings)
            state.job_postings = self._deduplicate(state.job_postings)
            dupes_removed = before_dedup - len(state.job_postings)
            logger.info(
                f"  Dedup: {before_dedup} -> {len(state.job_postings)} "
                f"({dupes_removed} duplicates removed)"
            )

            state = step3_classify_ai_jobs(state)

            job_data = {
                "company_id": company_id,
                "company_name": company_name,
                "ticker": ticker,
                "job_postings": state.job_postings,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "total_jobs": len(state.job_postings),
                "ai_jobs": sum(1 for p in state.job_postings if p.get("is_ai_role")),
                "pipeline_state": {
                    "job_postings_count": len(state.job_postings),
                    "companies": state.companies,
                    "summary": state.summary,
                },
            }

            self.s3_service.store_signal_data(
                signal_type="jobs", ticker=ticker, data=job_data
            )

            self._cache[cache_key] = job_data
            logger.info(f"Collected {len(state.job_postings)} jobs for {ticker}")
            return job_data

        except Exception as e:
            logger.error(f"Error collecting job data for {ticker}: {e}")
            raise

    def get_job_data(
        self, ticker: str, max_age_hours: int = 24
    ) -> Optional[Dict]:
        ticker = ticker.upper()
        cache_key = f"job_data_{ticker}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            cache_time = cached.get("collected_at")
            if cache_time:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(cache_time)
                if age < timedelta(hours=max_age_hours):
                    return cached
        return None

    def analyze_job_market(self, job_data: Dict) -> Dict[str, Any]:
        """
        Run step4 scoring on collected job data and return full analysis.

        Returns dict with:
          - job_market_scores: {company_id: score}
          - total_jobs: int
          - total_tech_jobs: int        ← NEW (from calculate_job_score)
          - ai_jobs: int
          - confidence: float           ← NEW (CS2 PDF formula)
          - score_breakdown: dict       ← NEW (ratio/volume/diversity)
          - ai_skills: list             ← NEW (for CS3 diversity)
          - ai_keywords: list           ← NEW
          - company_id, ticker
        """
        if not job_data or "job_postings" not in job_data:
            raise ValidationError("Invalid job data")

        state = SignalPipelineState(
            companies=job_data.get("pipeline_state", {}).get("companies", []),
        )
        state.job_postings = job_data["job_postings"]
        state.summary = job_data.get("pipeline_state", {}).get("summary", {})

        state = step4_score_job_market(state)

        # Extract full analysis from pipeline state
        company_id = job_data["company_id"]
        analysis = state.job_market_analyses.get(company_id, {})

        return {
            "job_market_scores": state.job_market_scores,
            "total_jobs": len(state.job_postings),
            "total_tech_jobs": analysis.get("total_tech_jobs", 0),
            "ai_jobs": analysis.get("ai_jobs", 0),
            "ai_ratio": analysis.get("ai_ratio", 0),
            "confidence": analysis.get("confidence", 0.5),
            "score_breakdown": analysis.get("score_breakdown", {}),
            "ai_skills": analysis.get("ai_skills", []),
            "ai_keywords": analysis.get("ai_keywords", []),
            "company_id": company_id,
            "ticker": job_data["ticker"],
        }

    def clear_cache(self, ticker: Optional[str] = None):
        if ticker:
            key = f"job_data_{ticker.upper()}"
            self._cache.pop(key, None)
            logger.info(f"Cleared cache for {ticker}")
        else:
            self._cache.clear()
            logger.info("Cleared all job data cache")


get_job_data_service = make_singleton_factory(JobDataService)
