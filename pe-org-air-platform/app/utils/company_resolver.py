"""
Company Resolver — app/utils/company_resolver.py

Resolves a ticker, company name, or CIK into full company metadata.

Sources (in order of reliability):
  1. yfinance      → name, sector, revenue, employees, market cap
  2. SEC EDGAR API → CIK number, fiscal year end, SIC code
  3. Groq LLM      → maps sector → industry_id, estimates position_factor

Usage:
    from app.utils.company_resolver import resolve_company
    result = resolve_company("GOOGL")
    result = resolve_company("Google")
    result = resolve_company("0001652044")
"""
from __future__ import annotations

import re
import logging
import requests
import structlog
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = structlog.get_logger(__name__)

# ── Your 6 industry IDs from Snowflake seed data ─────────────────
INDUSTRY_MAP: Dict[str, str] = {
    "manufacturing":      "550e8400-e29b-41d4-a716-446655440001",
    "healthcare":         "550e8400-e29b-41d4-a716-446655440002",
    "business_services":  "550e8400-e29b-41d4-a716-446655440003",
    "retail":             "550e8400-e29b-41d4-a716-446655440004",
    "financial":          "550e8400-e29b-41d4-a716-446655440005",
    "technology":         "550e8400-e29b-41d4-a716-446655440006",
}

# ── Sector → industry mapping (covers yfinance sector names) ─────
SECTOR_TO_INDUSTRY: Dict[str, str] = {
    "technology":                    "technology",
    "communication services":        "technology",
    "information technology":        "technology",
    "financial services":            "financial",
    "financials":                    "financial",
    "banking":                       "financial",
    "insurance":                     "financial",
    "healthcare":                    "healthcare",
    "health care":                   "healthcare",
    "pharmaceuticals":               "healthcare",
    "biotechnology":                 "healthcare",
    "industrials":                   "manufacturing",
    "manufacturing":                 "manufacturing",
    "materials":                     "manufacturing",
    "energy":                        "manufacturing",
    "utilities":                     "manufacturing",
    "consumer cyclical":             "retail",
    "consumer defensive":            "retail",
    "retail":                        "retail",
    "consumer staples":              "retail",
    "consumer discretionary":        "retail",
    "real estate":                   "business_services",
    "services":                      "business_services",
    "business services":             "business_services",
}

from app.core.settings import settings as _settings

GROQ_API_KEY = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_API_URL = _settings.GROQ_API_URL

SEC_EDGAR_COMPANY = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"


@dataclass
class ResolvedCompany:
    """Full company metadata ready for POST /api/v1/companies."""
    name: str
    ticker: str
    industry_id: str
    position_factor: float = 0.0

    sector: Optional[str] = None
    sub_sector: Optional[str] = None
    revenue_millions: Optional[float] = None
    employee_count: Optional[int] = None
    market_cap_percentile: Optional[float] = None
    fiscal_year_end: Optional[str] = None

    cik: Optional[str] = None
    market_cap: Optional[float] = None
    description: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None

    resolved_from: str = ""
    confidence: float = 1.0
    warnings: list = field(default_factory=list)


# ── FIX 1: Robust yfinance fetch for 1.2.0 ───────────────────────
def _lookup_yfinance(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Fetch company info from yfinance 1.2.0.

    yfinance 1.2.0 uses curl_cffi to bypass Yahoo bot-detection.
    A bare Ticker() call with no session config can return an empty
    dict silently on some environments. We use get_info() explicitly
    (the method form), and fall back to constructing a dict from
    fast_info + income_stmt if needed.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper())

        # Prefer get_info() (method) over .info (property) in 1.2.0 —
        # the method form forces a fresh fetch and raises on true failure
        # rather than returning a stale empty cache.
        try:
            info = t.get_info()
        except Exception as e:
            logger.warning("yfinance_get_info_failed", ticker=ticker, error=str(e))
            info = {}

        # If info came back empty or without longName, try fast_info as fallback
        if not info or not info.get("longName"):
            logger.warning(
                "yfinance_info_empty_falling_back_to_fast_info",
                ticker=ticker,
            )
            try:
                fi = t.get_fast_info()
                # fast_info in 1.x is an object with attributes, not a dict
                info = {
                    "longName":           getattr(fi, "company_name", None) or ticker,
                    "sector":             "",
                    "industry":           "",
                    "marketCap":          getattr(fi, "market_cap", None),
                    "totalRevenue":       None,
                    "fullTimeEmployees":  None,
                    "website":            "",
                    "country":            "",
                    "longBusinessSummary": "",
                }
            except Exception as e:
                logger.warning("yfinance_fast_info_failed", ticker=ticker, error=str(e))
                return None

        if not info.get("longName"):
            logger.warning("yfinance_no_longName", ticker=ticker)
            return None

        return info

    except Exception as e:
        logger.warning("yfinance_failed", ticker=ticker, error=str(e))
        return None


# ── FIX 2: Fiscal year end using income_stmt (1.x API) ───────────
def _get_fiscal_year_end(t) -> Optional[str]:
    """
    Infer fiscal year end month from the most recent annual income statement.

    yfinance 0.x used .financials — renamed to .income_stmt in 1.x.
    """
    try:
        stmt = t.income_stmt          # annual income statement, 1.x name
        if stmt is not None and not stmt.empty:
            last_col = stmt.columns[0]  # most recent fiscal year-end date
            return last_col.strftime("%B")  # e.g. "December", "January"
    except Exception as e:
        logger.warning("yfinance_fiscal_year_end_failed", ticker=t.ticker, error=str(e))
    return None


def _lookup_sec_cik(ticker: str) -> Optional[str]:
    """Look up CIK number from SEC EDGAR company tickers file."""
    try:
        resp = requests.get(
            SEC_EDGAR_TICKERS,
            headers={"User-Agent": "PE-OrgAIR-Platform research@quantuniversity.com"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        ticker_upper = ticker.upper()
        for _, company in data.items():
            if company.get("ticker", "").upper() == ticker_upper:
                return str(company["cik_str"]).zfill(10)
    except Exception as e:
        logger.warning("sec_cik_lookup_failed", ticker=ticker, error=str(e))
    return None


def _ticker_from_name_groq(company_name: str) -> Optional[str]:
    """Use Groq to resolve company name → ticker symbol."""
    if not GROQ_API_KEY:
        return None
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"What is the NYSE/NASDAQ ticker symbol for '{company_name}'? "
                        f"Reply with ONLY the ticker symbol, nothing else. "
                        f"Example: GOOGL or MSFT or AAPL"
                    ),
                }],
                "max_tokens": 10,
                "temperature": 0.1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        ticker = resp.json()["choices"][0]["message"]["content"].strip().upper()
        if re.match(r'^[A-Z]{1,5}$', ticker):
            return ticker
    except Exception as e:
        logger.warning("groq_ticker_resolution_failed", name=company_name, error=str(e))
    return None


def _map_sector_to_industry(sector: str) -> tuple[str, str]:
    sector_lower = sector.lower().strip()
    if sector_lower in SECTOR_TO_INDUSTRY:
        key = SECTOR_TO_INDUSTRY[sector_lower]
        return key, INDUSTRY_MAP[key]
    for s, key in SECTOR_TO_INDUSTRY.items():
        if s in sector_lower or sector_lower in s:
            return key, INDUSTRY_MAP[key]
    logger.warning("sector_not_mapped", sector=sector, default="business_services")
    return "business_services", INDUSTRY_MAP["business_services"]


def _calculate_position_factor(market_cap: Optional[float], sector: str) -> float:
    if not market_cap:
        return 0.0
    if market_cap >= 500_000_000_000:
        return 0.9
    elif market_cap >= 100_000_000_000:
        return 0.6
    elif market_cap >= 10_000_000_000:
        return 0.3
    elif market_cap >= 1_000_000_000:
        return 0.0
    return -0.3


def _calculate_market_cap_percentile(market_cap: Optional[float]) -> Optional[float]:
    if not market_cap:
        return None
    if market_cap >= 500_000_000_000:
        return 0.99
    elif market_cap >= 100_000_000_000:
        return 0.85
    elif market_cap >= 10_000_000_000:
        return 0.60
    elif market_cap >= 1_000_000_000:
        return 0.35
    return 0.10


def resolve_company(input_str: str) -> ResolvedCompany:
    """
    Resolve a ticker, company name, or CIK to full company metadata.
    """
    import yfinance as yf

    input_str = input_str.strip()
    warnings = []
    ticker = None
    cik = None

    # ── Detect input type ─────────────────────────────────────────
    if re.match(r'^\d{7,10}$', input_str):
        cik = input_str.zfill(10)
        try:
            resp = requests.get(
                SEC_EDGAR_COMPANY.format(cik=cik),
                headers={"User-Agent": "PE-OrgAIR-Platform research@quantuniversity.com"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                ticker = (data.get("tickers") or [None])[0]
                if ticker:
                    ticker = ticker.upper()
        except Exception:
            pass
        if not ticker:
            warnings.append(f"Could not resolve ticker from CIK {cik}")
            return ResolvedCompany(
                name=f"Company CIK {cik}",
                ticker=cik,
                industry_id=INDUSTRY_MAP["business_services"],
                cik=cik,
                resolved_from="sec_edgar",
                warnings=warnings,
            )

    elif re.match(r'^[A-Za-z]{1,5}$', input_str):
        ticker = input_str.upper()

    else:
        ticker = _ticker_from_name_groq(input_str)
        if not ticker:
            warnings.append(
                f"Could not resolve ticker for '{input_str}'. "
                "Try entering the ticker directly (e.g. GOOGL)"
            )
            return ResolvedCompany(
                name=input_str,
                ticker=input_str.upper()[:10],
                industry_id=INDUSTRY_MAP["business_services"],
                resolved_from="groq",
                confidence=0.3,
                warnings=warnings,
            )

    # ── Fetch from yfinance ───────────────────────────────────────
    info = _lookup_yfinance(ticker)

    if not info:
        warnings.append(
            f"yfinance returned no data for {ticker}. "
            "Company may not be publicly listed."
        )
        return ResolvedCompany(
            name=ticker,
            ticker=ticker,
            industry_id=INDUSTRY_MAP["business_services"],
            resolved_from="yfinance",       # FIX 3: was "yfinance_failed" which tripped
            confidence=0.3,                 # the resolved_from guard in the router
            warnings=warnings,
        )

    # ── Map sector → industry ─────────────────────────────────────
    yf_sector = info.get("sector", "") or ""
    industry_key, industry_id = _map_sector_to_industry(yf_sector)

    # ── Calculate financials ──────────────────────────────────────
    market_cap = info.get("marketCap")
    total_revenue = info.get("totalRevenue")
    revenue_millions = round(total_revenue / 1_000_000, 1) if total_revenue else None
    employee_count = info.get("fullTimeEmployees")
    position_factor = _calculate_position_factor(market_cap, yf_sector)
    market_cap_percentile = _calculate_market_cap_percentile(market_cap)

    if not cik:
        cik = _lookup_sec_cik(ticker)

    # ── FIX 2 applied: use income_stmt, not financials ────────────
    t = yf.Ticker(ticker)
    fiscal_year_end = _get_fiscal_year_end(t)

    logger.info(
        "company_resolved",
        ticker=ticker,
        name=info.get("longName"),
        sector=industry_key,
        revenue_millions=revenue_millions,
        employee_count=employee_count,
        market_cap_percentile=market_cap_percentile,
        fiscal_year_end=fiscal_year_end,
    )

    return ResolvedCompany(
        name=info.get("longName", ticker),
        ticker=ticker,
        industry_id=industry_id,
        position_factor=round(position_factor, 3),
        sector=industry_key,
        sub_sector=info.get("industry", ""),
        revenue_millions=revenue_millions,
        employee_count=employee_count,
        market_cap_percentile=market_cap_percentile,
        fiscal_year_end=fiscal_year_end,
        cik=cik,
        market_cap=market_cap,
        description=info.get("longBusinessSummary", "")[:500] if info.get("longBusinessSummary") else "",
        website=info.get("website", ""),
        country=info.get("country", ""),
        resolved_from="yfinance",
        confidence=0.95,
        warnings=warnings,
    )


def format_resolution_preview(company: ResolvedCompany) -> str:
    """Format resolved company for display in Streamlit."""
    lines = [
        f"**{company.name}** ({company.ticker})",
        f"Sector: {company.sector or 'Unknown'} | Sub-sector: {company.sub_sector or 'Unknown'}",
    ]
    if company.revenue_millions:
        lines.append(f"Revenue: ${company.revenue_millions:,.0f}M")
    if company.employee_count:
        lines.append(f"Employees: {company.employee_count:,}")
    if company.cik:
        lines.append(f"SEC CIK: {company.cik}")
    if company.warnings:
        for w in company.warnings:
            lines.append(f"⚠️ {w}")
    return "\n".join(lines)