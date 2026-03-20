"""
Board Composition Service
app/services/board_composition_service.py

Wraps BoardCompositionAnalyzer for use in the HTTP signals pipeline.
Exposes async analyze_company() matching the pattern of other signal services.
"""

import asyncio
import structlog
from typing import Optional

from app.core.errors import NotFoundError
from app.pipelines.board_analyzer import BoardCompositionAnalyzer, save_signal_to_s3
from app.config.company_mappings import CompanyRegistry
from app.repositories.company_repository import CompanyRepository

logger = structlog.get_logger()


class BoardCompositionService:
    def __init__(self, company_repo=None):
        self._analyzer = BoardCompositionAnalyzer()
        self._company_repo = company_repo or CompanyRepository()

    async def analyze_company(self, ticker: str, **kwargs) -> dict:
        ticker = ticker.upper()

        company = self._company_repo.get_by_ticker(ticker)
        if not company:
            raise NotFoundError("company", ticker)

        company_id = str(company["id"])
        name = company.get("name", ticker)
        sector = company.get("sector") or "unknown"

        if ticker not in CompanyRegistry.COMPANIES:
            # Resolve CIK from EDGAR; fall back to zeros if unavailable
            try:
                from app.pipelines.sec_edgar import get_sec_collector
                cik = get_sec_collector().get_cik(ticker) or "0000000000"
            except Exception:
                cik = "0000000000"
            CompanyRegistry.register(ticker, cik, name, sector)

        signal = await asyncio.to_thread(
            self._analyzer.scrape_and_analyze, ticker, company_id, True
        )

        save_signal_to_s3(signal, self._analyzer.get_last_evidence_trail())

        return {
            "normalized_score": float(signal.governance_score),
            "confidence": float(signal.confidence),
            "has_tech_committee": signal.has_tech_committee,
            "has_ai_expertise": signal.has_ai_expertise,
            "has_data_officer": signal.has_data_officer,
            "has_risk_tech_oversight": signal.has_risk_tech_oversight,
            "has_ai_in_strategy": signal.has_ai_in_strategy,
            "tech_expertise_count": signal.tech_expertise_count,
            "independent_ratio": float(signal.independent_ratio),
            "ai_experts": signal.ai_experts,
        }
