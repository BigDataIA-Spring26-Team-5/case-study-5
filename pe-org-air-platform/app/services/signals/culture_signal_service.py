"""
Culture Signal Service
app/services/culture_signal_service.py

Wraps CultureCollector for use in the HTTP signals pipeline.
Exposes async analyze_company() matching the pattern of other signal services,
plus a get(ticker) helper for reading S3-cached results (used by rag.py).

Module-level singleton accessed via get_culture_signal_service() so that the
existing import in app/routers/rag.py:972 continues to work.
"""

import asyncio
import json
import structlog
from typing import Optional, Tuple

from app.core.errors import NotFoundError
from app.pipelines.glassdoor_collector import (
    CultureCollector,
    validate_ticker,
)
from app.repositories.company_repository import CompanyRepository

logger = structlog.get_logger()


class CultureSignalService:
    def __init__(self, company_repo=None):
        self._collector = CultureCollector()
        self._company_repo = company_repo or CompanyRepository()

    async def analyze_company(self, ticker: str, force_refresh: bool = False, **kwargs) -> dict:
        ticker = ticker.upper()

        company = self._company_repo.get_by_ticker(ticker)
        if not company:
            raise NotFoundError("company", ticker)

        # validate_ticker auto-registers unknown tickers in COMPANY_REGISTRY
        validate_ticker(ticker)

        signal = await asyncio.to_thread(
            self._collector.collect_and_analyze,
            ticker,
            use_cache=not force_refresh,
        )

        return {
            "normalized_score": float(signal.overall_score),
            "confidence": float(signal.confidence),
            "innovation_score": float(signal.innovation_score),
            "data_driven_score": float(signal.data_driven_score),
            "change_readiness_score": float(signal.change_readiness_score),
            "ai_awareness_score": float(signal.ai_awareness_score),
            "review_count": signal.review_count,
            "avg_rating": float(signal.avg_rating),
        }

    def get(self, ticker: str) -> Tuple[Optional[dict], None]:
        """Read the latest culture signal from S3 (non-async, for rag.py enrichment)."""
        try:
            from app.services.s3_storage import get_s3_service
            s3 = get_s3_service()
            ticker_upper = ticker.upper()

            # Attempt 1: timestamped subfolder written by CultureCollector
            prefix = f"glassdoor_signals/output/{ticker_upper}/"
            keys = s3.list_files(prefix)
            if keys:
                raw = s3.get_file(sorted(keys)[-1])
                if raw is not None:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    return data, None

            # Attempt 2: flat file
            flat_key = f"glassdoor_signals/output/{ticker_upper}_culture.json"
            raw = s3.get_file(flat_key)
            if raw is not None:
                data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                return data, None

        except Exception as e:
            logger.warning("culture_signal_service.get_failed", extra={"ticker": ticker, "error": str(e)})

        return None, None


# Module-level singleton — imported by app/routers/rag.py
_instance: Optional[CultureSignalService] = None


def get_culture_signal_service() -> CultureSignalService:
    global _instance
    if _instance is None:
        _instance = CultureSignalService()
    return _instance
