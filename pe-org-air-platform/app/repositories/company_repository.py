from __future__ import annotations

from typing import List, Dict, Optional
from uuid import UUID, uuid4

from app.repositories.base import BaseRepository


class CompanyRepository(BaseRepository):
    """
    Repository for accessing companies from Snowflake.
    """


    _COLS = (
        "id, name, ticker, industry_id, position_factor, "
        "sector, sub_sector, market_cap_percentile, revenue_millions, "
        "employee_count, fiscal_year_end, is_deleted, created_at, updated_at"
    )

    def get_all(self) -> List[Dict]:
        """
        Return all active (non-deleted) companies.
        """
        sql = f"""
        SELECT {self._COLS}
        FROM companies
        WHERE is_deleted = FALSE
        ORDER BY name
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def get_by_id(self, company_id: UUID) -> Dict | None:
        """
        Fetch a single company by ID.
        """
        sql = f"""
        SELECT {self._COLS}
        FROM companies
        WHERE id = %s AND is_deleted = FALSE
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(company_id),))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def get_by_ticker(self, ticker: str) -> Dict | None:
        """
        Fetch a single company by ticker.
        """
        sql = f"""
        SELECT {self._COLS}
        FROM companies
        WHERE UPPER(ticker) = UPPER(%s) AND is_deleted = FALSE
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def get_by_industry(self, industry_id: UUID) -> List[Dict]:
        """
        Return all active companies for a specific industry.
        """
        sql = f"""
        SELECT {self._COLS}
        FROM companies
        WHERE industry_id = %s AND is_deleted = FALSE
        ORDER BY name
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(industry_id),))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def exists(self, company_id: UUID) -> bool:
        """
        Check if a company exists (regardless of deleted status).
        """
        sql = "SELECT 1 FROM companies WHERE id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(company_id),))
                return cur.fetchone() is not None
            finally:
                cur.close()

    def is_deleted(self, company_id: UUID) -> bool:
        """
        Check if a company is soft-deleted.
        """
        sql = "SELECT is_deleted FROM companies WHERE id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(company_id),))
                row = cur.fetchone()
                return row is not None and row[0] is True
            finally:
                cur.close()

    def check_duplicate(
        self,
        name: str,
        industry_id: UUID,
        exclude_id: Optional[UUID] = None
    ) -> bool:
        """
        Check if a company with the same name exists in the same industry.
        """
        if exclude_id:
            sql = """
            SELECT 1 FROM companies 
            WHERE name = %s AND industry_id = %s AND id != %s AND is_deleted = FALSE
            """
            params = (name, str(industry_id), str(exclude_id))
        else:
            sql = """
            SELECT 1 FROM companies 
            WHERE name = %s AND industry_id = %s AND is_deleted = FALSE
            """
            params = (name, str(industry_id))

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchone() is not None
            finally:
                cur.close()

    def create(
        self,
        name: str,
        industry_id: UUID,
        ticker: Optional[str] = None,
        position_factor: float = 0.0,
        sector: Optional[str] = None,
        sub_sector: Optional[str] = None,
        market_cap_percentile: Optional[float] = None,
        revenue_millions: Optional[float] = None,
        employee_count: Optional[int] = None,
        fiscal_year_end: Optional[str] = None,
    ) -> Dict:
        """
        Create a new company and return its data.
        """
        company_id = str(uuid4())

        sql = """
        INSERT INTO companies (
            id, name, ticker, industry_id, position_factor,
            sector, sub_sector, market_cap_percentile,
            revenue_millions, employee_count, fiscal_year_end
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (
                    company_id, name, ticker, str(industry_id), position_factor,
                    sector, sub_sector, market_cap_percentile,
                    revenue_millions, employee_count, fiscal_year_end,
                ))
                conn.commit()
            finally:
                cur.close()

        return self.get_by_id(UUID(company_id))

    def update_enriched_fields(
        self,
        company_id: UUID,
        sector: Optional[str] = None,
        sub_sector: Optional[str] = None,
        market_cap_percentile: Optional[float] = None,
        revenue_millions: Optional[float] = None,
        employee_count: Optional[int] = None,
        fiscal_year_end: Optional[str] = None,
    ) -> None:
        """Update only the Groq-enriched fields for a company."""
        updates, params = [], []
        if sector is not None:
            updates.append("sector = %s"); params.append(sector)
        if sub_sector is not None:
            updates.append("sub_sector = %s"); params.append(sub_sector)
        if market_cap_percentile is not None:
            updates.append("market_cap_percentile = %s"); params.append(market_cap_percentile)
        if revenue_millions is not None:
            updates.append("revenue_millions = %s"); params.append(revenue_millions)
        if employee_count is not None:
            updates.append("employee_count = %s"); params.append(employee_count)
        if fiscal_year_end is not None:
            updates.append("fiscal_year_end = %s"); params.append(fiscal_year_end)
        if not updates:
            return
        updates.append("updated_at = CURRENT_TIMESTAMP()")
        params.append(str(company_id))
        sql = f"UPDATE companies SET {', '.join(updates)} WHERE id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, tuple(params))
                conn.commit()
            finally:
                cur.close()

    def get_by_portfolio(self, portfolio_id: str) -> List[Dict]:
        """Return all active companies belonging to a portfolio."""
        sql = f"""
        SELECT {self._COLS}
        FROM companies
        WHERE id IN (
            SELECT company_id FROM cs4_portfolio_companies WHERE portfolio_id = %s
        ) AND is_deleted = FALSE
        ORDER BY name
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (portfolio_id,))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def create_portfolio(self, name: str, fund_vintage: Optional[int] = None) -> str:
        """Create a new portfolio in cs4_portfolios and return its UUID."""
        portfolio_id = str(uuid4())
        sql = "INSERT INTO cs4_portfolios (id, name, fund_vintage) VALUES (%s, %s, %s)"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (portfolio_id, name, fund_vintage))
                conn.commit()
            finally:
                cur.close()
        return portfolio_id

    def add_company_to_portfolio(self, portfolio_id: str, company_id: str) -> None:
        """Link a company to a portfolio in cs4_portfolio_companies."""
        sql = "INSERT INTO cs4_portfolio_companies (portfolio_id, company_id) VALUES (%s, %s)"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (portfolio_id, company_id))
                conn.commit()
            finally:
                cur.close()

    def get_portfolio(self, portfolio_id: str) -> Optional[Dict]:
        """Fetch portfolio metadata by ID."""
        sql = "SELECT id, name, fund_vintage, created_at FROM cs4_portfolios WHERE id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (portfolio_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def set_portfolio_companies(self, portfolio_id: str, company_ids: List[str]) -> None:
        """Replace portfolio membership with the provided company UUIDs."""
        unique_ids = []
        seen = set()
        for cid in company_ids or []:
            s = str(cid)
            if not s or s in seen:
                continue
            seen.add(s)
            unique_ids.append(s)

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "DELETE FROM cs4_portfolio_companies WHERE portfolio_id = %s",
                    (portfolio_id,),
                )
                for cid in unique_ids:
                    cur.execute(
                        "INSERT INTO cs4_portfolio_companies (portfolio_id, company_id) VALUES (%s, %s)",
                        (portfolio_id, cid),
                    )
                conn.commit()
            finally:
                cur.close()

    def list_portfolios(self, limit: int = 200) -> List[Dict]:
        """List portfolios from cs4_portfolios (most recent first)."""
        sql = """
        SELECT id, name, fund_vintage, created_at
        FROM cs4_portfolios
        ORDER BY created_at DESC
        LIMIT %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (int(limit),))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def find_portfolio_id_by_name(self, name: str) -> Optional[str]:
        """Resolve portfolio UUID by exact name match (case-insensitive)."""
        sql = "SELECT id FROM cs4_portfolios WHERE LOWER(name) = LOWER(%s) LIMIT 1"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (name,))
                row = cur.fetchone()
                if not row:
                    return None
                return str(row[0])
            finally:
                cur.close()


    def update(
        self,
        company_id: UUID,
        name: Optional[str] = None,
        ticker: Optional[str] = None,
        industry_id: Optional[UUID] = None,
        position_factor: Optional[float] = None,
        sector: Optional[str] = None,
        sub_sector: Optional[str] = None,
        market_cap_percentile: Optional[float] = None,
        revenue_millions: Optional[float] = None,
        employee_count: Optional[int] = None,
        fiscal_year_end: Optional[str] = None,
    ) -> Dict:
        """
        Update a company's fields and return updated data.
        """
        updates = []
        params = []

        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if ticker is not None:
            updates.append("ticker = %s")
            params.append(ticker)
        if industry_id is not None:
            updates.append("industry_id = %s")
            params.append(str(industry_id))
        if position_factor is not None:
            updates.append("position_factor = %s")
            params.append(position_factor)
        if sector is not None:
            updates.append("sector = %s")
            params.append(sector)
        if sub_sector is not None:
            updates.append("sub_sector = %s")
            params.append(sub_sector)
        if market_cap_percentile is not None:
            updates.append("market_cap_percentile = %s")
            params.append(market_cap_percentile)
        if revenue_millions is not None:
            updates.append("revenue_millions = %s")
            params.append(revenue_millions)
        if employee_count is not None:
            updates.append("employee_count = %s")
            params.append(employee_count)
        if fiscal_year_end is not None:
            updates.append("fiscal_year_end = %s")
            params.append(fiscal_year_end)

        if not updates:
            return self.get_by_id(company_id)

        updates.append("updated_at = CURRENT_TIMESTAMP()")
        params.append(str(company_id))

        sql = f"UPDATE companies SET {', '.join(updates)} WHERE id = %s"

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, tuple(params))
                conn.commit()
            finally:
                cur.close()

        return self.get_by_id(company_id)

    def soft_delete(self, company_id: UUID) -> None:
        """
        Soft delete a company by setting is_deleted = TRUE.
        """
        sql = """
        UPDATE companies 
        SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP() 
        WHERE id = %s
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(company_id),))
                conn.commit()
            finally:
                cur.close()
