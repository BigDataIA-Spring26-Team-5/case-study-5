"""
One-time script: Fill missing sector/sub_sector/revenue/employees for all companies.

Run:
  cd pe-org-air-platform
  python -m app.scripts.fill_sectors
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from app.repositories.base import get_snowflake_connection

# ── Company metadata to fill ─────────────────────────────────────────────────
COMPANY_DATA = {
    "NVDA": {"sector": "technology",           "sub_sector": "Semiconductors",         "revenue_millions": 130497, "employee_count": 42000,  "fiscal_year_end": "January",  "market_cap_percentile": 0.99},
    "CRM":  {"sector": "technology",           "sub_sector": "Cloud Computing",        "revenue_millions": 37884,  "employee_count": 79500,  "fiscal_year_end": "January",  "market_cap_percentile": 0.43},
    "GOOGL":{"sector": "technology",           "sub_sector": "Software - Application", "revenue_millions": 350018, "employee_count": 190000, "fiscal_year_end": "December", "market_cap_percentile": 0.15},
    "MSFT": {"sector": "technology",           "sub_sector": "Software - Infrastructure","revenue_millions":245122,"employee_count": 221000, "fiscal_year_end": "June",     "market_cap_percentile": 0.18},
    "NFLX": {"sector": "technology",           "sub_sector": "Streaming Media",        "revenue_millions": 39000,  "employee_count": 22000,  "fiscal_year_end": "December", "market_cap_percentile": 0.98},
    "AAPL": {"sector": "technology",           "sub_sector": "Consumer Electronics",   "revenue_millions": 394800, "employee_count": 154000, "fiscal_year_end": "September","market_cap_percentile": 0.14},
    "JPM":  {"sector": "financial_services",   "sub_sector": "Banking",                "revenue_millions": 177600, "employee_count": 309926, "fiscal_year_end": "December", "market_cap_percentile": 0.90},
    "GS":   {"sector": "financial_services",   "sub_sector": "Investment Banking",     "revenue_millions": 53510,  "employee_count": 46400,  "fiscal_year_end": "December", "market_cap_percentile": 0.85},
    "WMT":  {"sector": "retail",               "sub_sector": "Discount Stores",        "revenue_millions": 713163, "employee_count": 2100000,"fiscal_year_end": "January",  "market_cap_percentile": 0.99},
    "TGT":  {"sector": "retail",               "sub_sector": "Department Stores",      "revenue_millions": 105600, "employee_count": 440000, "fiscal_year_end": "January",  "market_cap_percentile": 0.75},
    "DG":   {"sector": "retail",               "sub_sector": "Discount Stores",        "revenue_millions": 40250,  "employee_count": 195000, "fiscal_year_end": "January",  "market_cap_percentile": 0.60},
    "ADP":  {"sector": "business_services",    "sub_sector": "Payroll & HR",           "revenue_millions": 19650,  "employee_count": 63000,  "fiscal_year_end": "June",     "market_cap_percentile": 0.88},
    "PAYX": {"sector": "business_services",    "sub_sector": "Payroll Services",       "revenue_millions": 5280,   "employee_count": 16800,  "fiscal_year_end": "May",      "market_cap_percentile": 0.70},
    "UNH":  {"sector": "healthcare_services",  "sub_sector": "Health Insurance",       "revenue_millions": 400300, "employee_count": 440000, "fiscal_year_end": "December", "market_cap_percentile": 0.95},
    "HCA":  {"sector": "healthcare_services",  "sub_sector": "Hospitals",              "revenue_millions": 68900,  "employee_count": 309000, "fiscal_year_end": "December", "market_cap_percentile": 0.80},
    "CAT":  {"sector": "manufacturing",        "sub_sector": "Heavy Equipment",        "revenue_millions": 65656,  "employee_count": 115000, "fiscal_year_end": "December", "market_cap_percentile": 0.92},
    "DE":   {"sector": "manufacturing",        "sub_sector": "Farm Equipment",         "revenue_millions": 51700,  "employee_count": 83000,  "fiscal_year_end": "October",  "market_cap_percentile": 0.87},
    "GE":   {"sector": "manufacturing",        "sub_sector": "Aerospace & Defense",    "revenue_millions": 67954,  "employee_count": 125000, "fiscal_year_end": "December", "market_cap_percentile": 0.91},
}


def main():
    conn = get_snowflake_connection()
    cur = conn.cursor()

    try:
        # Get all companies
        cur.execute("SELECT id, ticker, sector, sub_sector, revenue_millions, employee_count FROM companies WHERE is_deleted = FALSE")
        rows = cur.fetchall()
        print(f"Found {len(rows)} companies in Snowflake\n")

        updated = 0
        for row in rows:
            company_id, ticker, cur_sector, cur_sub, cur_rev, cur_emp = row
            ticker = (ticker or "").upper()

            if ticker not in COMPANY_DATA:
                print(f"  SKIP  {ticker} — not in metadata map")
                continue

            data = COMPANY_DATA[ticker]

            # Build SET clause only for fields that are NULL or need updating
            sets = []
            params = []

            if not cur_sector:
                sets.append("sector = %s")
                params.append(data["sector"])
            if not cur_sub:
                sets.append("sub_sector = %s")
                params.append(data["sub_sector"])
            if not cur_rev:
                sets.append("revenue_millions = %s")
                params.append(data["revenue_millions"])
            if not cur_emp:
                sets.append("employee_count = %s")
                params.append(data["employee_count"])

            # Always update these
            sets.append("fiscal_year_end = %s")
            params.append(data["fiscal_year_end"])
            sets.append("market_cap_percentile = %s")
            params.append(data["market_cap_percentile"])
            sets.append("updated_at = CURRENT_TIMESTAMP()")

            if not sets:
                print(f"  OK    {ticker} — already filled")
                continue

            sql = f"UPDATE companies SET {', '.join(sets)} WHERE id = %s"
            params.append(company_id)
            cur.execute(sql, params)
            updated += 1
            print(f"  DONE  {ticker} — sector={data['sector']}, sub={data['sub_sector']}, rev={data['revenue_millions']}, emp={data['employee_count']}")

        print(f"\nUpdated {updated} companies.")

        # Verify
        print("\n--- Verification ---")
        cur.execute("SELECT ticker, sector, sub_sector, revenue_millions, employee_count, fiscal_year_end FROM companies WHERE is_deleted = FALSE ORDER BY ticker")
        for row in cur.fetchall():
            print(f"  {row[0]:6s}  sector={row[1] or 'NULL':25s}  sub={row[2] or 'NULL':25s}  rev={row[3] or 'NULL'}  emp={row[4] or 'NULL'}  fy={row[5] or 'NULL'}")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
