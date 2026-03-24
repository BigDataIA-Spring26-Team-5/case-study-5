"""CS1 Client — Company metadata from the PE Org-AI-R platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum

from app.clients.base import BaseAPIClient
from app.core.errors import NotFoundError


class Sector(str, Enum):
    TECHNOLOGY = "technology"
    FINANCIAL_SERVICES = "financial_services"
    HEALTHCARE = "healthcare"
    MANUFACTURING = "manufacturing"
    RETAIL = "retail"
    BUSINESS_SERVICES = "business_services"
    CONSUMER = "consumer"


@dataclass
class Company:
    company_id: str
    ticker: str
    name: str
    sector: Sector
    sub_sector: str = ""
    market_cap_percentile: float = 0.0
    revenue_millions: float = 0.0
    employee_count: int = 0
    fiscal_year_end: str = ""


@dataclass
class Portfolio:
    portfolio_id: str
    name: str
    company_ids: List[str] = field(default_factory=list)
    fund_vintage: int = 0


class CS1Client(BaseAPIClient):
    """Fetches company metadata from CS1 API endpoints."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        super().__init__(base_url + "/api/v1", "cs1", timeout=30.0)

    async def get_company(self, ticker: str) -> Optional[Company]:
        """Fetch a single company by ticker."""
        try:
            data = await self.get(f"/companies/{ticker}")
            return self._parse_company(data)
        except NotFoundError:
            return None

    async def list_companies(
        self,
        sector: Optional[Sector] = None,
        min_revenue: Optional[float] = None,
    ) -> List[Company]:
        """List all companies, optionally filtered."""
        params: dict = {}
        if sector:
            params["sector"] = sector.value
        if min_revenue is not None:
            params["min_revenue"] = min_revenue
        data = await self.get("/companies", params=params)
        companies = data if isinstance(data, list) else data.get("companies", [])
        return [self._parse_company(c) for c in companies]

    @staticmethod
    def _parse_company(data: dict) -> Company:
        raw_sector = data.get("sector", "")
        try:
            sector = Sector(raw_sector.lower().replace(" ", "_"))
        except ValueError:
            sector = Sector.BUSINESS_SERVICES  # safe fallback

        return Company(
            company_id=str(data.get("id", data.get("company_id", ""))),
            ticker=data.get("ticker", ""),
            name=data.get("name", ""),
            sector=sector,
            sub_sector=data.get("sub_sector", data.get("subsector", "")),
            market_cap_percentile=float(data.get("market_cap_percentile", 0.0)),
            revenue_millions=float(data.get("revenue_millions", 0.0)),
            employee_count=int(data.get("employee_count", 0)),
            fiscal_year_end=data.get("fiscal_year_end", ""),
        )

    # -------------------------------------------------------------------------
    # CS5 Portfolio support (CS1 portfolio management)
    # -------------------------------------------------------------------------

    async def get_portfolio(self, portfolio_id: str) -> Optional[Portfolio]:
        """Fetch a single portfolio by ID."""
        try:
            data = await self.get(f"/portfolios/{portfolio_id}")
        except NotFoundError:
            return None
        return Portfolio(
            portfolio_id=str(data.get("id", portfolio_id)),
            name=data.get("name", ""),
            company_ids=list(data.get("company_ids") or []),
            fund_vintage=int(data.get("fund_vintage") or 0),
        )

    async def resolve_portfolio_id(self, name: str) -> Optional[str]:
        """Resolve a portfolio UUID by name (exact, case-insensitive match)."""
        data = await self.get("/portfolios/resolve", params={"name": name})
        return data.get("portfolio_id")

    async def get_portfolio_companies(self, portfolio_id: str) -> List[Company]:
        """Return companies belonging to a portfolio."""
        data = await self.get(f"/portfolios/{portfolio_id}/companies")
        items = data.get("items", []) if isinstance(data, dict) else []
        return [self._parse_company(c) for c in items]
