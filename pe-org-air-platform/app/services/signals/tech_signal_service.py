"""
Tech Signal Service — Digital Presence
app/services/tech_signal_service.py

Service layer for digital_presence signals.
Uses BuiltWith + Wappalyzer to analyze actual company tech stacks.
NOW: Uses LLM (Groq) to suggest relevant subdomains based on company lookup.

Stores results in S3 (raw) + Snowflake (metadata/scores).
NO local file storage. NO job-posting-derived tech data.

FIXES:
  - BUG 1: _suggest_subdomains was sync but called async llm_router.complete()
    → now uses asyncio to properly await the coroutine
  - BUG 2: TechStackCollector.analyze_company() doesn't accept 'subdomains' kwarg
    → removed subdomains from call, collector handles its own domain scanning
"""
import asyncio
import structlog
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.pipelines.tech_signals import TechStackCollector, TechStackResult
from app.services.signals.base_signal_service import BaseSignalService
from app.services.s3_storage import get_s3_service
from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import SignalRepository
from app.services.utils import make_singleton_factory
from app.services.llm.router import get_llm_router

logger = structlog.get_logger()


class TechSignalService(BaseSignalService):
    """Service to extract digital presence signals from website tech stacks."""

    signal_category = "digital_presence"
    summary_field = "digital_score"

    def __init__(self, company_repo=None, signal_repo=None):
        self.collector = TechStackCollector()
        self.s3 = get_s3_service()
        self.company_repo = company_repo or CompanyRepository()
        self.signal_repo = signal_repo or SignalRepository()
        self.llm_router = get_llm_router()

    async def _suggest_subdomains_async(
        self,
        ticker: str,
        company_name: Optional[str],
        website: Optional[str]
    ) -> List[str]:
        """
        Use LLM to suggest relevant subdomains for a company.
        Now properly async — awaits the LLM router.
        """
        prompt = f"""You are analyzing the digital presence of a company to find their technology stack.

Company: {company_name or ticker}
Ticker: {ticker}
Primary Website: {website or 'unknown'}

Based on this company's likely business model and industry, suggest 5-10 relevant subdomains that might host technical infrastructure, developer tools, APIs, or cloud services.

Examples of good subdomain suggestions:
- For tech companies: developers, api, cloud, console, dashboard, platform, docs
- For enterprise software: portal, app, admin, analytics, insights
- For e-commerce: checkout, cart, shop, store
- For financial services: secure, online, mobile, wealth, trading

Return ONLY a JSON array of subdomain names (no protocol, no domain):
["subdomain1", "subdomain2", "subdomain3"]

Be specific to this company's likely digital infrastructure needs."""

        try:
            response = await self.llm_router.complete(
                task="subdomain_suggestion",
                messages=[{"role": "user", "content": prompt}],
            )

            # Handle response — could be a LiteLLM response object or string
            if hasattr(response, "choices"):
                response_text = response.choices[0].message.content.strip()
            elif isinstance(response, str):
                response_text = response.strip()
            else:
                response_text = str(response).strip()

            # Remove markdown code blocks if present
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()

            import json
            subdomains = json.loads(response_text)

            if isinstance(subdomains, list) and len(subdomains) > 0:
                logger.info(f"  🤖 LLM suggested {len(subdomains)} subdomains for {ticker}: {subdomains[:3]}...")
                return subdomains
            else:
                logger.warning(f"  ⚠️ LLM returned invalid subdomain list for {ticker}")
                return self._get_default_subdomains()

        except Exception as e:
            logger.warning(f"  ⚠️ LLM subdomain suggestion failed for {ticker}: {e}")
            return self._get_default_subdomains()

    @staticmethod
    def _get_default_subdomains() -> List[str]:
        """Fallback subdomains if LLM suggestion fails."""
        return [
            "developers", "api", "cloud", "console", "dashboard",
            "portal", "app", "platform", "docs", "careers"
        ]

    async def _collect(self, ticker: str, company_id: str, company: dict, **kwargs) -> dict:
        """
        Collect tech stack data with LLM-suggested subdomains.

        Flow:
        1. Get company context (name, website)
        2. Use LLM to suggest relevant subdomains (async)
        3. Pass to TechStackCollector for BuiltWith/Wappalyzer analysis
        4. Analyze and score results
        """
        company_name = company.get("name")
        website = kwargs.get("website")

        # Get LLM-suggested subdomains (now properly async)
        suggested_subdomains = await self._suggest_subdomains_async(
            ticker=ticker,
            company_name=company_name,
            website=website
        )

        import inspect
        sig = inspect.signature(self.collector.analyze_company)
        collector_kwargs = {
            "company_id": company_id,
            "ticker": ticker,
            "company_name": company_name,
            "website": website,
        }
        # Only pass subdomains if the collector actually accepts it
        if "subdomains" in sig.parameters:
            collector_kwargs["subdomains"] = suggested_subdomains

        result: TechStackResult = await self.collector.analyze_company(**collector_kwargs)

        self._store_to_s3(ticker, result)

        return {
            "source": "builtwith_wappalyzer",
            "signal_date": datetime.now(timezone.utc),
            "raw_value": (
                f"Tech stack analysis: {len(result.technologies)} techs detected "
                f"from {result.domain} (suggested {len(suggested_subdomains)} subdomains)"
            ),
            "normalized_score": result.score,
            "confidence": result.confidence,
            "metadata": {
                "domain": result.domain,
                "score": result.score,
                "ai_tools_score": result.ai_tools_score,
                "infra_score": result.infra_score,
                "breadth_score": result.breadth_score,
                "builtwith_live_count": result.builtwith_total_live,
                "wappalyzer_tech_count": len(result.wappalyzer_techs),
                "ai_technologies": [t.name for t in result.technologies if t.is_ai_related],
                "subdomains_suggested": suggested_subdomains,
                "analysis_sources": self._active_sources(result),
                "errors": result.errors,
            },
            # extra fields used by _build_response
            "breakdown": {
                "sophistication_score": round(result.ai_tools_score, 1),
                "infrastructure_score": round(result.infra_score, 1),
                "breadth_score": round(result.breadth_score, 1),
            },
            "tech_metrics": {
                "domain": result.domain,
                "total_technologies": len(result.technologies),
                "builtwith_live_count": result.builtwith_total_live,
                "wappalyzer_tech_count": len(result.wappalyzer_techs),
                "ai_technologies": [t.name for t in result.technologies if t.is_ai_related],
                "subdomains_analyzed": len(suggested_subdomains),
            },
            "data_sources": self._active_sources(result),
            "collected_at": result.collected_at,
            "errors": result.errors,
        }

    def _build_response(self, ticker: str, company: dict, result: dict) -> dict:
        return {
            "ticker": ticker,
            "company_id": str(company["id"]),
            "company_name": company.get("name", ticker),
            "normalized_score": round(result["normalized_score"], 2),
            "confidence": round(result["confidence"], 3),
            "breakdown": result["breakdown"],
            "tech_metrics": result["tech_metrics"],
            "data_sources": result["data_sources"],
            "collected_at": result["collected_at"],
            "errors": result["errors"],
        }

    async def analyze_company(
        self,
        ticker: str,
        force_refresh: bool = False,
        website: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticker = ticker.upper()
        logger.info("=" * 60)
        logger.info(f"🌐 ANALYZING DIGITAL PRESENCE FOR: {ticker}")
        logger.info("=" * 60)
        try:
            result = await super().analyze_company(ticker, website=website)
            logger.info("=" * 60)
            logger.info(f"📊 DIGITAL PRESENCE COMPLETE: {ticker}")
            logger.info("=" * 60)
            return result
        except Exception as e:
            logger.error(f"❌ Error analyzing digital presence for {ticker}: {e}")
            raise

    def _store_to_s3(self, ticker: str, result: TechStackResult) -> None:
        try:
            data = TechStackCollector.result_to_dict(result)
            self.s3.store_signal_data(signal_type="digital", ticker=ticker, data=data)
            logger.info(f"  📤 Stored tech stack data to S3 for {ticker}")
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to store to S3: {e}")

    @staticmethod
    def _active_sources(result: TechStackResult) -> List[str]:
        sources = []
        if result.builtwith_groups:
            sources.append("builtwith")
        if result.wappalyzer_techs:
            sources.append("wappalyzer")
        return sources or ["none"]

    async def analyze_all_companies(self, force_refresh: bool = False) -> Dict[str, Any]:
        tickers = ["CAT", "DE", "UNH", "HCA", "ADP", "PAYX", "WMT", "TGT", "JPM", "GS"]
        logger.info("=" * 60)
        logger.info("🌐 ANALYZING DIGITAL PRESENCE FOR ALL COMPANIES")
        logger.info("=" * 60)
        results, success, failed = [], 0, 0
        for ticker in tickers:
            try:
                r = await self.analyze_company(ticker, force_refresh)
                results.append({
                    "ticker": ticker, "status": "success",
                    "score": r["normalized_score"],
                    "technologies": r["tech_metrics"]["total_technologies"],
                })
                success += 1
            except Exception as e:
                logger.error(f"❌ {ticker}: {e}")
                results.append({"ticker": ticker, "status": "failed", "error": str(e)})
                failed += 1
        logger.info(f"✅ Done: {success} succeeded, {failed} failed")
        return {"total": len(tickers), "successful": success, "failed": failed, "results": results}


get_tech_signal_service = make_singleton_factory(TechSignalService)