"""
Job Signal Service — Technology Hiring

Fallback chain:
  1. JobSpy (LinkedIn + Indeed scraping) — primary, high confidence
  2. JSearch API (Google Jobs via RapidAPI) — real job data fallback, confidence 0.70
  3. Groq LLM estimate — last resort, confidence capped at 0.30
"""
import json
import structlog
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import httpx
from app.services.signals.base_signal_service import BaseSignalService
from app.services.signals.job_data_service import get_job_data_service
from app.services.s3_storage import get_s3_service
from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import SignalRepository
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError
from app.core.settings import settings

logger = structlog.get_logger()

# AI/ML keywords for classifying job postings from JSearch
AI_KEYWORDS = {
    "machine learning", "deep learning", "artificial intelligence", "neural network",
    "nlp", "natural language processing", "computer vision", "llm", "large language model",
    "generative ai", "gen ai", "pytorch", "tensorflow", "data science", "ml engineer",
    "ai engineer", "ai/ml", "ml ops", "mlops", "reinforcement learning", "transformer",
    "gpt", "bert", "diffusion", "rag", "retrieval augmented", "vector database",
    "embedding", "fine-tuning", "prompt engineering", "ai infrastructure",
}


class JobSignalService(BaseSignalService):
    """Service to extract technology hiring signals from job postings."""

    signal_category = "technology_hiring"
    summary_field = "hiring_score"

    def __init__(self, company_repo=None, signal_repo=None):
        self.job_data_service = get_job_data_service()
        self.s3_service = get_s3_service()
        self.company_repo = company_repo or CompanyRepository()
        self.signal_repo = signal_repo or SignalRepository()

    # ── Fallback 1: JSearch API (real job data from Google Jobs) ───────────

    def _jsearch_fallback(self, ticker: str, company_name: str) -> Optional[Dict[str, Any]]:
        """Search real job postings via JSearch (RapidAPI). Returns scored result or None."""
        api_key = settings.JSEARCH_API_KEY.get_secret_value() if settings.JSEARCH_API_KEY else ""
        if not api_key:
            logger.info(f"  [{ticker}] JSearch API key not configured — skipping")
            return None

        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": settings.JSEARCH_HOST,
        }

        # Search for AI/ML jobs at this company
        queries = [
            f"{company_name} artificial intelligence machine learning",
            f"{company_name} AI engineer data science",
        ]

        all_jobs: List[Dict] = []
        seen_ids: set = set()

        for query in queries:
            try:
                resp = httpx.get(
                    f"https://{settings.JSEARCH_HOST}/search",
                    headers=headers,
                    params={
                        "query": query,
                        "num_pages": "2",
                        "date_posted": "month",
                    },
                    timeout=20.0,
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for job in data:
                    job_id = job.get("job_id", "")
                    if job_id not in seen_ids:
                        seen_ids.add(job_id)
                        all_jobs.append(job)
            except Exception as e:
                logger.warning(f"  [{ticker}] JSearch query failed: {e}")

        if not all_jobs:
            logger.info(f"  [{ticker}] JSearch returned 0 jobs")
            return None

        # Classify AI jobs
        ai_jobs = []
        for job in all_jobs:
            title = (job.get("job_title") or "").lower()
            desc = (job.get("job_description") or "")[:2000].lower()
            text = f"{title} {desc}"
            if any(kw in text for kw in AI_KEYWORDS):
                ai_jobs.append(job)

        total_jobs = len(all_jobs)
        ai_count = len(ai_jobs)
        ai_ratio = ai_count / total_jobs if total_jobs > 0 else 0

        # Score: ratio-based + volume bonus (same logic as JobSpy pipeline)
        ratio_score = min(ai_ratio * 100 * 1.5, 50)  # up to 50 pts from ratio
        volume_bonus = min(ai_count * 3, 30)           # up to 30 pts from volume
        diversity_bonus = min(len({j.get("job_title", "").lower().split()[0] for j in ai_jobs}), 20)
        score = min(ratio_score + volume_bonus + diversity_bonus, 100)

        # Confidence based on data volume (higher than LLM but lower than full JobSpy)
        confidence = min(0.50 + total_jobs / 200, 0.75)

        logger.info(
            f"  [{ticker}] JSearch fallback: {ai_count} AI jobs / {total_jobs} total → "
            f"score={score:.1f}, confidence={confidence:.2f}"
        )

        # Extract AI skills found
        skill_counts: Dict[str, int] = {}
        for job in ai_jobs:
            text = f"{job.get('job_title', '')} {(job.get('job_description') or '')[:2000]}".lower()
            for kw in AI_KEYWORDS:
                if kw in text:
                    skill_counts[kw] = skill_counts.get(kw, 0) + 1
        top_skills = sorted(skill_counts, key=skill_counts.get, reverse=True)[:15]

        return {
            "score": round(score, 1),
            "ai_jobs": ai_count,
            "total_jobs": total_jobs,
            "ai_ratio": round(ai_ratio, 3),
            "confidence": round(confidence, 2),
            "ai_skills": top_skills,
            "source": "jsearch",
            "sample_titles": [j.get("job_title", "") for j in ai_jobs[:5]],
        }

    # ── Fallback 2: Groq LLM estimate (last resort) ──────────────────────

    def _llm_hiring_fallback(self, ticker: str, company_name: str) -> Dict[str, Any]:
        """LLM fallback when both JobSpy and JSearch fail. Confidence capped at 0.30."""
        api_key = settings.GROQ_API_KEY.get_secret_value() if settings.GROQ_API_KEY else ""
        if not api_key:
            return {"score": 0.0, "ai_jobs_estimate": 0, "rationale": "No GROQ_API_KEY"}

        prompt = (
            f'For "{company_name}" (ticker: {ticker}), estimate their AI/ML hiring intensity.\n\n'
            f'Based on your knowledge of this company\'s public job postings, engineering blog, '
            f'and AI strategy, estimate:\n'
            f'1. What percentage of their tech job postings are AI/ML related?\n'
            f'2. How many AI/ML roles do they typically have open?\n'
            f'3. Score their technology hiring signal from 0-100.\n\n'
            f'Respond ONLY with valid JSON, no markdown:\n'
            f'{{"score": <0-100>, "ai_jobs_estimate": <number>, '
            f'"ai_ratio_pct": <0-100>, "rationale": "<one sentence>"}}'
        )

        try:
            resp = httpx.post(
                settings.GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            result = json.loads(raw)
            score = min(max(float(result.get("score", 0)), 0), 100)
            logger.warning(
                f"  ⚠️  {ticker}: LLM hiring fallback score {score}/100 "
                f"(confidence: 0.30, estimate: ~{result.get('ai_jobs_estimate', '?')} AI roles)"
            )
            return result
        except Exception as e:
            logger.warning(f"  ⚠️  {ticker}: LLM hiring fallback failed: {e}")
            return {"score": 0.0, "ai_jobs_estimate": 0, "rationale": f"LLM fallback failed: {e}"}

    # ── Main collection with fallback chain ───────────────────────────────

    async def _collect(self, ticker: str, company_id: str, company: dict,
                       force_refresh: bool = False) -> dict:
        logger.info("📊 Getting job data for analysis...")
        job_data = await self.job_data_service.collect_job_data(ticker, force_refresh=force_refresh)
        if not job_data or "job_postings" not in job_data:
            raise NotFoundError("job_data", ticker)

        logger.info("📈 Analyzing job market...")
        analysis_result = self.job_data_service.analyze_job_market(job_data)

        job_market_score = analysis_result["job_market_scores"].get(company_id, 0.0)
        total_jobs = analysis_result["total_jobs"]
        total_tech_jobs = analysis_result.get("total_tech_jobs", total_jobs)
        ai_jobs = analysis_result["ai_jobs"]
        confidence = analysis_result.get("confidence", min(0.5 + total_tech_jobs / 100, 0.95))

        fallback_source = None
        fallback_data = {}

        # Fallback chain when JobSpy finds no AI jobs
        if job_market_score < 5.0 and ai_jobs == 0:
            company_name = company.get("name", ticker)
            logger.warning(f"  ⚠️  {ticker}: JobSpy returned 0 AI jobs — trying JSearch fallback")

            # Fallback 1: JSearch API (real job data)
            jsearch_result = self._jsearch_fallback(ticker, company_name)
            if jsearch_result and jsearch_result.get("score", 0) > 0:
                job_market_score = jsearch_result["score"]
                ai_jobs = jsearch_result["ai_jobs"]
                total_jobs = jsearch_result["total_jobs"]
                total_tech_jobs = total_jobs
                confidence = jsearch_result["confidence"]
                fallback_source = "jsearch"
                fallback_data = jsearch_result
            else:
                # Fallback 2: Groq LLM estimate (last resort)
                logger.warning(f"  ⚠️  {ticker}: JSearch also failed — invoking LLM fallback")
                llm_result = self._llm_hiring_fallback(ticker, company_name)
                fallback_score = float(llm_result.get("score", 0))
                if fallback_score > job_market_score:
                    job_market_score = fallback_score
                    ai_jobs = int(llm_result.get("ai_jobs_estimate", 0))
                    confidence = 0.30
                    fallback_source = "llm"
                    fallback_data = llm_result

        source = "jobspy"
        if fallback_source == "jsearch":
            source = "jsearch"
        elif fallback_source == "llm":
            source = "jobspy+llm_fallback"

        return {
            "source": source,
            "signal_date": datetime.now(timezone.utc),
            "raw_value": (
                f"Job market analysis: {ai_jobs} AI jobs out of "
                f"{total_tech_jobs} tech jobs ({total_jobs} total)"
                + (f" [{fallback_source} fallback]" if fallback_source else "")
            ),
            "normalized_score": job_market_score,
            "confidence": confidence,
            "metadata": {
                "job_market_score": job_market_score,
                "ai_jobs_count": ai_jobs,
                "total_tech_jobs": total_tech_jobs,
                "total_jobs_count": total_jobs,
                "job_postings_analyzed": total_jobs,
                "score_breakdown": analysis_result.get("score_breakdown", {}),
                "ai_skills_found": fallback_data.get("ai_skills", analysis_result.get("ai_skills", [])),
                "data_collected_at": job_data.get("collected_at"),
                "analysis_method": fallback_source or "job_market_scoring",
                "fallback_source": fallback_source,
                "fallback_data": fallback_data if fallback_source else None,
            },
            "breakdown": {"job_market_score": round(job_market_score, 1)},
            "job_metrics": {
                "total_jobs": total_jobs,
                "total_tech_jobs": total_tech_jobs,
                "ai_jobs": ai_jobs,
                "ai_job_ratio": round(ai_jobs / total_tech_jobs * 100, 1) if total_tech_jobs > 0 else 0,
            },
            "data_freshness": job_data.get("collected_at"),
            "job_postings_analyzed": total_jobs,
        }

    def _build_response(self, ticker: str, company: dict, result: dict) -> dict:
        return {
            "ticker": ticker,
            "company_id": str(company["id"]),
            "company_name": company.get("name", ticker),
            "normalized_score": round(result["normalized_score"], 2),
            "confidence": round(result["confidence"], 3),
            "breakdown": result["breakdown"],
            "job_metrics": result["job_metrics"],
            "data_freshness": result["data_freshness"],
            "job_postings_analyzed": result["job_postings_analyzed"],
        }

    async def analyze_company(self, ticker: str, force_refresh: bool = False) -> Dict:
        ticker = ticker.upper()
        logger.info("=" * 60)
        logger.info(f"🎯 ANALYZING TECHNOLOGY HIRING SIGNALS FOR: {ticker}")
        logger.info("=" * 60)
        try:
            result = await super().analyze_company(ticker, force_refresh=force_refresh)
            logger.info("=" * 60)
            logger.info(f"📊 TECHNOLOGY HIRING ANALYSIS COMPLETE FOR: {ticker}")
            logger.info("=" * 60)
            return result
        except Exception as e:
            logger.error(f"❌ Error analyzing job signals for {ticker}: {e}")
            raise

    async def analyze_all_companies(self, force_refresh: bool = False) -> Dict:
        """Analyze technology hiring signals for all target companies."""
        target_tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]
        logger.info("=" * 60)
        logger.info("🎯 ANALYZING TECHNOLOGY HIRING SIGNALS FOR ALL COMPANIES")
        logger.info(f"   Force refresh: {force_refresh}")
        logger.info("=" * 60)
        results, success_count, failed_count = [], 0, 0
        for ticker in target_tickers:
            try:
                result = await self.analyze_company(ticker, force_refresh=force_refresh)
                results.append({
                    "ticker": ticker, "status": "success",
                    "score": result["normalized_score"],
                    "confidence": result["confidence"],
                    "jobs_analyzed": result["job_postings_analyzed"],
                    "tech_jobs": result["job_metrics"]["total_tech_jobs"],
                    "ai_jobs": result["job_metrics"]["ai_jobs"],
                })
                success_count += 1
            except Exception as e:
                logger.error(f"❌ Failed to analyze {ticker}: {e}")
                results.append({"ticker": ticker, "status": "failed", "error": str(e)})
                failed_count += 1
        logger.info("=" * 60)
        logger.info("📊 ALL COMPANIES TECHNOLOGY HIRING ANALYSIS COMPLETE")
        logger.info(f"   Successful: {success_count}")
        logger.info(f"   Failed: {failed_count}")
        logger.info("=" * 60)
        return {
            "total_companies": len(target_tickers),
            "successful": success_count,
            "failed": failed_count,
            "force_refresh": force_refresh,
            "results": results,
        }


get_job_signal_service = make_singleton_factory(JobSignalService)
