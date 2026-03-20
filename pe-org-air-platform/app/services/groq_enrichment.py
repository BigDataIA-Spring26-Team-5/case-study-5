"""
Groq Enrichment Service
app/services/groq_enrichment.py

Uses the Groq API (OpenAI-compatible) to:
1. Auto-fill unknown company metadata fields (sub_sector, market_cap_percentile, etc.)
2. Generate dynamic scoring keywords for a given company and dimension.

Set GROQ_API_KEY in the environment before use.
"""

import json
import logging
from typing import Dict, List, Optional

import httpx

from app.core.errors import ExternalServiceError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory keyword cache — keyed by "{ticker}|{dimension}"
# Prevents repeated Groq API calls for the same (company, dimension) within
# one process lifetime (e.g. a single pipeline run).
# ---------------------------------------------------------------------------
_kw_cache: Dict[str, List[str]] = {}

from app.core.settings import settings as _settings

GROQ_API_URL = _settings.GROQ_API_URL
GROQ_MODEL = "llama-3.1-8b-instant"


def _get_api_key() -> str:
    key = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
    if not key:
        raise ExternalServiceError("groq", "GROQ_API_KEY is not configured")
    return key


def _chat(prompt: str, system: str = "You are a financial data analyst. Respond only with valid JSON.") -> str:
    """Send a chat completion request to Groq and return the response text."""
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    response = httpx.post(GROQ_API_URL, headers=headers, json=payload, timeout=30.0)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def enrich_company_metadata(ticker: str, company_name: str) -> Dict:
    """
    Ask Groq to fill in unknown company metadata fields.

    Returns a dict with keys:
        sector, sub_sector, market_cap_percentile, revenue_millions,
        employee_count, fiscal_year_end
    Missing or uncertain fields will be None.
    """
    prompt = f"""Given the publicly-traded company with ticker "{ticker}" and name "{company_name}",
provide the following fields as a JSON object:
{{
  "sector": "<one of: technology, financial_services, healthcare, manufacturing, retail, business_services, consumer>",
  "sub_sector": "<specific sub-industry, e.g. 'Streaming Media', 'Cloud Infrastructure'>",
  "market_cap_percentile": <float 0.0-1.0, approximate percentile within S&P 500>,
  "revenue_millions": <most recent annual revenue in USD millions, integer or float>,
  "employee_count": <approximate total employees, integer>,
  "fiscal_year_end": "<month name, e.g. December, March, September>"
}}
Use your best estimate based on well-known public information. Return ONLY the JSON object."""

    try:
        raw = _chat(prompt)
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {
            "sector": data.get("sector"),
            "sub_sector": data.get("sub_sector"),
            "market_cap_percentile": float(data["market_cap_percentile"]) if data.get("market_cap_percentile") is not None else None,
            "revenue_millions": float(data["revenue_millions"]) if data.get("revenue_millions") is not None else None,
            "employee_count": int(data["employee_count"]) if data.get("employee_count") is not None else None,
            "fiscal_year_end": data.get("fiscal_year_end"),
        }
    except Exception as exc:
        logger.warning("Groq company enrichment failed for %s: %s", ticker, exc)
        return {}


def enrich_portfolio_metadata(ticker: str, company_name: str) -> Dict:
    """
    Ask Groq for PE fund/portfolio metadata associated with a company.

    Returns a dict with keys:
        portfolio_name, fund_vintage
    Falls back to safe defaults on failure.
    """
    prompt = f"""For the publicly-traded company "{company_name}" (ticker: {ticker}),
provide information about any notable private equity fund, institutional investment portfolio,
or growth fund associated with it as a JSON object:
{{
  "portfolio_name": "<name of the PE fund or investment portfolio, e.g. 'Sequoia Growth Fund IV'>",
  "fund_vintage": <year the fund was established or first invested, integer like 2019, or null if unknown>
}}
If no specific PE fund is publicly known, use "{company_name} Growth Portfolio" as portfolio_name
and estimate a plausible vintage year based on when the company became prominent.
Return ONLY the JSON object."""

    try:
        raw = _chat(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {
            "portfolio_name": data.get("portfolio_name") or f"{company_name} Growth Portfolio",
            "fund_vintage": int(data["fund_vintage"]) if data.get("fund_vintage") else None,
        }
    except Exception as exc:
        logger.warning("Groq portfolio enrichment failed for %s: %s", ticker, exc)
        return {"portfolio_name": f"{company_name} Growth Portfolio", "fund_vintage": None}


def get_dimension_keywords(ticker: str, company_name: str, dimension: str, base_keywords: List[str]) -> List[str]:
    """
    Ask Groq to expand a list of base keywords for a specific company and scoring dimension.
    Returns the combined deduplicated list of original + Groq-generated keywords.

    Results are cached in-memory by (ticker, dimension) so Groq is called only once
    per company/dimension per process lifetime.

    Args:
        ticker: Company ticker symbol
        company_name: Company full name
        dimension: Scoring dimension name (e.g. 'data_infrastructure', 'ai_job_keywords')
        base_keywords: The hardcoded base keywords for the dimension
    """
    cache_key = f"{ticker.upper()}|{dimension}"
    if cache_key in _kw_cache:
        logger.debug("Groq keyword cache hit for %s/%s", ticker, dimension)
        return _kw_cache[cache_key]

    dimension_label = dimension.replace("_", " ").title()
    prompt = f"""For the company "{company_name}" (ticker: {ticker}), in the context of
AI readiness assessment for the dimension "{dimension_label}", suggest 5-10 additional
keywords or phrases similar to: {base_keywords}.

These keywords will be used to search for evidence in SEC filings, job postings, and
analyst notes. Focus on terminology specific to this company's industry and use of AI/ML.

Respond with ONLY a JSON array of strings, e.g. ["keyword1", "keyword2"]."""

    try:
        raw = _chat(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        extra = json.loads(raw)
        if isinstance(extra, list):
            combined = list(dict.fromkeys(base_keywords + [str(k).lower() for k in extra]))
            _kw_cache[cache_key] = combined
            return combined
    except Exception as exc:
        logger.warning("Groq keyword expansion failed for %s/%s: %s", ticker, dimension, exc)

    _kw_cache[cache_key] = base_keywords
    return base_keywords
