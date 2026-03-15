# """CS1 Client — Company metadata from the PE Org-AI-R platform."""
# from __future__ import annotations

# import httpx
# from dataclasses import dataclass, field
# from typing import List, Optional


# @dataclass
# class Company:
#     company_id: str
#     ticker: str
#     name: str
#     sector: str
#     sub_sector: str = ""
#     market_cap_percentile: float = 0.0
#     revenue_millions: float = 0.0
#     employee_count: int = 0
#     fiscal_year_end: str = ""


# class CS1Client:
#     """Fetches company metadata from CS1 API endpoints."""

#     def __init__(self, base_url: str = "http://localhost:8000"):
#         self.base_url = base_url.rstrip("/")
#         self._client = httpx.Client(timeout=30.0)

#     def get_company(self, ticker: str) -> Optional[Company]:
#         """Fetch a single company by ticker."""
#         resp = self._client.get(f"{self.base_url}/companies/{ticker}")
#         if resp.status_code == 404:
#             return None
#         resp.raise_for_status()
#         data = resp.json()
#         return self._parse_company(data)

#     def list_companies(
#         self,
#         sector: Optional[str] = None,
#         min_revenue: Optional[float] = None,
#     ) -> List[Company]:
#         """List all companies, optionally filtered."""
#         params: dict = {}
#         if sector:
#             params["sector"] = sector
#         if min_revenue is not None:
#             params["min_revenue"] = min_revenue
#         resp = self._client.get(f"{self.base_url}/companies", params=params)
#         resp.raise_for_status()
#         data = resp.json()
#         companies = data if isinstance(data, list) else data.get("companies", [])
#         return [self._parse_company(c) for c in companies]

#  @staticmethod
#     def _parse_company(data: dict) -> Company:
#         return Company(
#             company_id=str(data.get("id", data.get("company_id", ""))),
#             ticker=data.get("ticker", ""),
#             name=data.get("name", ""),
#             sector=data.get("sector", ""),
#             sub_sector=data.get("sub_sector", data.get("subsector", "")),
#             market_cap_percentile=float(data.get("market_cap_percentile", 0.0)),
#             revenue_millions=float(data.get("revenue_millions", 0.0)),
#             employee_count=int(data.get("employee_count", 0)),
#             fiscal_year_end=data.get("fiscal_year_end", ""),
#         )

#     def close(self):
#         self._client.close()

#     def __enter__(self):
#         return self

#     def __exit__(self, *_):
#         self.close()

"""CS1 Client — Company metadata from the PE Org-AI-R platform."""
from __future__ import annotations

import httpx
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


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


class CS1Client:
    """Fetches company metadata from CS1 API endpoints."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self._client = httpx.AsyncClient(timeout=30.0)

    async def get_company(self, ticker: str) -> Optional[Company]:
        """Fetch a single company by ticker."""
        resp = await self._client.get(f"{self.base_url}/companies/{ticker}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return self._parse_company(data)

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
        resp = await self._client.get(f"{self.base_url}/companies", params=params)
        resp.raise_for_status()
        data = resp.json()
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

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()