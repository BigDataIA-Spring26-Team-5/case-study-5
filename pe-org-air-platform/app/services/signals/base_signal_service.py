"""
Base Signal Service - PE Org-AI-R Platform
app/services/base_signal_service.py

Abstract base class for the 5-step signal orchestration pattern:
  delete old → collect → create_signal → upsert_summary → return result dict
"""
from abc import ABC, abstractmethod
from typing import Any, Dict

from app.core.errors import NotFoundError


class BaseSignalService(ABC):
    """Shared orchestration: delete old → collect → persist → return result dict."""

    @property
    @abstractmethod
    def signal_category(self) -> str:
        """Snowflake / Redis signal category key (e.g. 'technology_hiring')."""
        ...

    @property
    @abstractmethod
    def summary_field(self) -> str:
        """Keyword argument name for upsert_summary (e.g. 'hiring_score')."""
        ...

    @abstractmethod
    async def _collect(self, ticker: str, company_id: str, company: dict, **kwargs) -> dict:
        """
        Collect raw signal data for one company.

        Must return a dict with at least these keys:
          source, signal_date, raw_value, normalized_score, confidence, metadata
        """
        ...

    def _build_response(self, ticker: str, company: dict, result: dict) -> dict:
        """Build the final API response dict. Override to add service-specific fields."""
        return {
            "ticker": ticker,
            "company_id": str(company["id"]),
            "company_name": company.get("name", ticker),
            "normalized_score": round(result["normalized_score"], 2),
            "confidence": result["confidence"],
            "breakdown": result.get("breakdown", {}),
        }

    async def analyze_company(self, ticker: str, **kwargs) -> Dict[str, Any]:
        ticker = ticker.upper()

        company = self.company_repo.get_by_ticker(ticker)
        if not company:
            raise NotFoundError("company", ticker)

        company_id = str(company["id"])

        self.signal_repo.delete_signals_by_category(company_id, self.signal_category)

        result = await self._collect(ticker, company_id, company, **kwargs)

        self.signal_repo.create_signal(
            company_id=company_id,
            category=self.signal_category,
            source=result["source"],
            signal_date=result["signal_date"],
            raw_value=result["raw_value"],
            normalized_score=result["normalized_score"],
            confidence=result["confidence"],
            metadata=result["metadata"],
        )
        self.signal_repo.upsert_summary(
            company_id=company_id,
            ticker=ticker,
            **{self.summary_field: result["normalized_score"]},
        )

        return self._build_response(ticker, company, result)
