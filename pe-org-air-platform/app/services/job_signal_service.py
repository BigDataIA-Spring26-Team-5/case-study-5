# app/services/job_signal_service.py
"""
Job Signal Service — Technology Hiring

ALIGNED WITH:
  - CS2 PDF pages 14-16: technology_hiring signal, weight 0.30
  - CS3 PDF page 7: Maps to Talent(0.70), Technology_Stack(0.20), Culture(0.10)

FIX: Confidence now uses the value from calculate_job_score() in the pipeline
     which implements the CS2 PDF formula: min(0.5 + total_tech_jobs/100, 0.95)
     Previously this service had its own custom confidence formula that
     overrode the PDF-compliant one.
"""
import logging
from typing import Dict
from datetime import datetime, timezone

from app.services.base_signal_service import BaseSignalService
from app.services.job_data_service import get_job_data_service
from app.services.s3_storage import get_s3_service
from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import get_signal_repository
from app.services.utils import make_singleton_factory
from app.core.errors import NotFoundError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class JobSignalService(BaseSignalService):
    """Service to extract technology hiring signals from job postings."""

    signal_category = "technology_hiring"
    summary_field = "hiring_score"

    def __init__(self):
        self.job_data_service = get_job_data_service()
        self.s3_service = get_s3_service()
        self.company_repo = CompanyRepository()
        self.signal_repo = get_signal_repository()

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

        # FIX: Use CS2 PDF confidence formula (page 15 line 80)
        confidence = analysis_result.get("confidence", min(0.5 + total_tech_jobs / 100, 0.95))

        return {
            "source": "jobspy",
            "signal_date": datetime.now(timezone.utc),
            "raw_value": (
                f"Job market analysis: {ai_jobs} AI jobs out of "
                f"{total_tech_jobs} tech jobs ({total_jobs} total)"
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
                "ai_skills_found": analysis_result.get("ai_skills", []),
                "data_collected_at": job_data.get("collected_at"),
                "analysis_method": "job_market_scoring",
            },
            # extra fields used by _build_response
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
