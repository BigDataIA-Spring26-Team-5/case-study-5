"""
Patent Signal Service
app/services/patent_signal_service.py

ALIGNED WITH CASE STUDY 2 PDF SPEC (pages 17-19).
Confidence is fixed 0.90 per PDF. Scoring done in patent_signals.py.
"""
import structlog
from typing import Dict
from datetime import datetime, timezone

from app.pipelines.patent_signals import run_patent_signals
from app.pipelines.signal_pipeline_state import SignalPipelineState as Pipeline2State
from app.services.signals.base_signal_service import BaseSignalService
from app.services.s3_storage import get_s3_service
from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import SignalRepository
from app.services.utils import make_singleton_factory

logger = structlog.get_logger()


class PatentSignalService(BaseSignalService):
    """Service to extract innovation activity signals from patent analysis."""

    signal_category = "innovation_activity"
    summary_field = "innovation_score"

    def __init__(self, company_repo=None, signal_repo=None):
        self.s3_service = get_s3_service()
        self.company_repo = company_repo or CompanyRepository()
        self.signal_repo = signal_repo or SignalRepository()

    async def _collect(self, ticker: str, company_id: str, company: dict, years_back: int = 5) -> dict:
        company_name = company["name"]
        logger.info("📊 Running patent signals pipeline...")
        state = Pipeline2State(
            companies=[{"id": company_id, "name": company_name, "ticker": ticker}],
        )
        state = await run_patent_signals(state, years_back=years_back, results_per_company=100)

        patent_score = state.patent_scores.get(company_id, 0.0)
        ai_patents = sum(1 for p in state.patents if p.get("is_ai_related"))
        total_patents = len(state.patents)
        all_categories = set()
        for p in state.patents:
            if p.get("is_ai_related"):
                all_categories.update(p.get("ai_categories", []))

        logger.info(f"   Total patents analyzed: {total_patents}")
        logger.info(f"   AI patents found: {ai_patents}")
        logger.info(f"   Patent Portfolio Score: {patent_score:.1f}/100")

        return {
            "source": "patentsview",
            "signal_date": datetime.now(timezone.utc),
            "raw_value": f"Patent analysis: {ai_patents} AI patents out of {total_patents} total",
            "normalized_score": patent_score,
            "confidence": 0.90,  # fixed per CS2 PDF page 19 line 71
            "metadata": {
                "patent_portfolio_score": patent_score,
                "ai_patents_count": ai_patents,
                "total_patents_count": total_patents,
                "ai_categories": sorted(all_categories),
                "patents_analyzed": total_patents,
                "years_back": years_back,
            },
            # extra fields used by _build_response
            "breakdown": {"patent_portfolio_score": round(patent_score, 1)},
            "patent_metrics": {
                "total_patents": total_patents,
                "ai_patents": ai_patents,
                "ai_patent_ratio": round(ai_patents / total_patents * 100, 1) if total_patents > 0 else 0,
                "ai_categories": sorted(all_categories),
                "analysis_period_years": years_back,
            },
            "patents_analyzed": total_patents,
        }

    def _build_response(self, ticker: str, company: dict, result: dict) -> dict:
        return {
            "ticker": ticker,
            "company_id": str(company["id"]),
            "company_name": company.get("name", ticker),
            "normalized_score": round(result["normalized_score"], 2),
            "confidence": result["confidence"],
            "breakdown": result["breakdown"],
            "patent_metrics": result["patent_metrics"],
            "patents_analyzed": result["patents_analyzed"],
        }

    async def analyze_company(self, ticker: str, years_back: int = 5) -> Dict:
        ticker = ticker.upper()
        logger.info("=" * 60)
        logger.info(f"🎯 ANALYZING INNOVATION ACTIVITY SIGNALS FOR: {ticker}")
        logger.info("=" * 60)
        try:
            result = await super().analyze_company(ticker, years_back=years_back)
            logger.info("=" * 60)
            logger.info(f"📊 INNOVATION ACTIVITY ANALYSIS COMPLETE FOR: {ticker}")
            logger.info("=" * 60)
            return result
        except Exception as e:
            logger.error(f"❌ Error analyzing patent signals for {ticker}: {e}")
            raise

    async def analyze_all_companies(self, years_back: int = 5) -> Dict:
        tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]

        logger.info("=" * 60)
        logger.info("🎯 ANALYZING INNOVATION ACTIVITY FOR ALL COMPANIES")
        logger.info("=" * 60)

        results, ok, fail = [], 0, 0
        for t in tickers:
            try:
                r = await self.analyze_company(t, years_back)
                results.append({"ticker": t, "status": "success", "score": r["normalized_score"],
                                "ai_patents": r["patent_metrics"]["ai_patents"],
                                "total_patents": r["patent_metrics"]["total_patents"]})
                ok += 1
            except Exception as e:
                logger.error(f"❌ {t}: {e}")
                results.append({"ticker": t, "status": "failed", "error": str(e)})
                fail += 1

        return {"total": len(tickers), "successful": ok, "failed": fail, "results": results}


get_patent_signal_service = make_singleton_factory(PatentSignalService)
