"""Company name mappings and portfolio constants.

Single source of truth for company ticker → name mappings,
search aliases, patent names, and portfolio lists.
"""
from typing import Optional, List, Dict

# =============================================================================
# CS3 PORTFOLIO — The 5 companies scored in CS3
# =============================================================================

CS3_PORTFOLIO: List[str] = ["NVDA", "JPM", "WMT", "GE", "DG"]


# =============================================================================
# COMPANY NAME MAPPINGS
# =============================================================================
# Maps ticker -> search name and aliases for job scraping and fuzzy matching
# - "search": Primary name to use when searching job sites
# - "aliases": All valid variations for fuzzy matching (includes search name)
# - "patent_search": List of assignee names to query on PatentsView API
#     PatentsView treats each exact spelling as a SEPARATE entity.
#     e.g. "Walmart Inc." returns 0 patents; "Walmart Apollo, LLC" returns 1000+
# =============================================================================

COMPANY_NAME_MAPPINGS: Dict[str, Dict] = {
    "CAT": {
        "official": "Caterpillar Inc.",
        "search": "Caterpillar",
        "job_search_names": ["Caterpillar"],
        "patent_search": ["Caterpillar Inc."],
        "domain": "caterpillar.com",
        "aliases": ["Caterpillar", "Caterpillar Inc", "Caterpillar Inc.", "CAT"],
    },
   "DE": {
    "official": "Deere & Company",
    "search": "John Deere",
    "job_search_names": ["John Deere", "Blue River Technology"],
    "patent_search": ["Deere & Company"],
    "domain": "deere.com",
    "aliases": ["John Deere", "Deere", "Deere & Company", "JD",
                "Blue River Technology"],
},
    "UNH": {
        "official": "UnitedHealth Group Incorporated",
        "search": "UnitedHealth",
        "job_search_names": ["UnitedHealth Group", "Optum"],
        "patent_search": [
            "UnitedHealth Group Incorporated",
            "Optum, Inc.",
            "Optum Services, Inc.",
        ],
        "domain": "unitedhealthgroup.com",
        "aliases": ["UnitedHealth", "UnitedHealth Group", "United Health",
                     "UnitedHealthcare", "UHG", "Optum"],
    },
    "HCA": {
        "official": "HCA Healthcare, Inc.",
        "search": "HCA Healthcare",
        "job_search_names": ["HCA Healthcare"],
        "patent_search": ["HCA Healthcare, Inc."],
        "domain": "hcahealthcare.com",
        "aliases": ["HCA Healthcare", "HCA", "HCA Inc",
                     "Hospital Corporation of America"],
    },
    "ADP": {
        "official": "Automatic Data Processing, Inc.",
        "search": "ADP",
        "job_search_names": ["ADP"],
        "patent_search": [
            "ADP, Inc.",
            "Automatic Data Processing, Inc.",
        ],
        "domain": "adp.com",
        "aliases": ["ADP", "Automatic Data Processing", "ADP Inc"],
    },
    "PAYX": {
        "official": "Paychex, Inc.",
        "search": "Paychex",
        "job_search_names": ["Paychex"],
        "patent_search": ["Paychex, Inc."],
        "domain": "paychex.com",
        "aliases": ["Paychex", "Paychex Inc", "Paychex Inc."],
    },
    "WMT": {
        "official": "Walmart Inc.",
        "search": "Walmart",
        "job_search_names": ["Walmart"],
        "patent_search": [
            "Walmart Apollo, LLC",
            "Wal-Mart Stores, Inc.",
        ],
        "domain": "walmart.com",
        "aliases": ["Walmart", "Walmart Inc", "Walmart Inc.",
                     "Wal-Mart", "Wal Mart"],
    },
    "TGT": {
        "official": "Target Corporation",
        "search": "Target",
        "job_search_names": ["Target"],
        "patent_search": ["Target Brands, Inc."],
        "domain": "target.com",
        "aliases": ["Target", "Target Corporation", "Target Corp"],
    },
    "JPM": {
        "official": "JPMorgan Chase & Co.",
        "search": "JPMorgan Chase",
        "job_search_names": ["JPMorgan Chase"],
        "patent_search": ["JPMorgan Chase Bank, N.A."],
        "domain": "jpmorganchase.com",
        "aliases": ["JPMorgan Chase", "JPMorgan", "JP Morgan",
                     "Chase", "J.P. Morgan", "JPMC"],
    },
    "GS": {
        "official": "The Goldman Sachs Group, Inc.",
        "search": "Goldman Sachs",
        "job_search_names": ["Goldman Sachs"],
        "patent_search": [
            "Goldman Sachs & Co. LLC",
            "Goldman Sachs & Co.",
        ],
        "domain": "goldmansachs.com",
        "aliases": [
            "Goldman Sachs", "Goldman", "GS", "Goldman Sachs Group",
            "Goldman Sachs & Co", "Goldman Sachs & Co. LLC",
            "Goldman Sachs Bank", "Goldman Sachs Bank USA",
            "Goldman Sachs Asset Management",
            "Goldman Sachs International",
            "Marcus by Goldman Sachs",
        ],
    },
    # =========================================================================
    # CS3-ONLY COMPANIES (NVDA, GE, DG — JPM and WMT already above)
    # =========================================================================
    "NVDA": {
        "official": "NVIDIA Corporation",
        "search": "NVIDIA",
        "job_search_names": ["NVIDIA"],
        "patent_search": ["NVIDIA Corporation"],
        "domain": "nvidia.com",
        "aliases": ["NVIDIA", "NVIDIA Corporation", "Nvidia"],
    },
    "GE": {
        "official": "General Electric Company",
        "search": "GE Aerospace",
        "job_search_names": [
            "GE Aerospace",
            "GE Vernova",
        ],
        "patent_search": ["General Electric Company"],
        "domain": "ge.com",
        "aliases": ["General Electric", "GE", "General Electric Company",
                     "GE Aerospace", "GE Vernova", "GE HealthCare"],
    },
    "NFLX": {
        "official": "Netflix, Inc.",
        "search": "Netflix",
        "job_search_names": ["Netflix"],
        "patent_search": ["Netflix, Inc."],
        "domain": "netflix.com",
        "aliases": ["Netflix", "Netflix Inc", "Netflix, Inc.", "NFLX"],
    },
    "AAPL": {
        "official": "Apple Inc.",
        "search": "Apple",
        "job_search_names": ["Apple"],
        "patent_search": ["Apple Inc."],
        "domain": "apple.com",
        "aliases": ["Apple", "Apple Inc", "Apple Inc.", "AAPL"],
    },
    "MSFT": {
        "official": "Microsoft Corporation",
        "search": "Microsoft",
        "job_search_names": ["Microsoft"],
        "patent_search": ["Microsoft Corporation", "Microsoft Technology Licensing, LLC"],
        "domain": "microsoft.com",
        "aliases": ["Microsoft", "Microsoft Corporation", "MSFT"],
    },
    "GOOGL": {
        "official": "Alphabet Inc.",
        "search": "Google",
        "job_search_names": ["Google", "DeepMind"],
        "patent_search": ["Google LLC", "Alphabet Inc."],
        "domain": "google.com",
        "aliases": ["Google", "Alphabet", "Alphabet Inc", "GOOGL", "GOOG"],
    },
    "AMZN": {
        "official": "Amazon.com, Inc.",
        "search": "Amazon",
        "job_search_names": ["Amazon", "AWS"],
        "patent_search": ["Amazon Technologies, Inc.", "Amazon.com, Inc."],
        "domain": "amazon.com",
        "aliases": ["Amazon", "Amazon.com", "AWS", "Amazon Web Services", "AMZN"],
    },
    "META": {
        "official": "Meta Platforms, Inc.",
        "search": "Meta",
        "job_search_names": ["Meta"],
        "patent_search": ["Meta Platforms, Inc.", "Facebook, Inc."],
        "domain": "meta.com",
        "aliases": ["Meta", "Meta Platforms", "Facebook", "META"],
    },
    "TSLA": {
        "official": "Tesla, Inc.",
        "search": "Tesla",
        "job_search_names": ["Tesla"],
        "patent_search": ["Tesla, Inc."],
        "domain": "tesla.com",
        "aliases": ["Tesla", "Tesla Inc", "Tesla, Inc.", "TSLA"],
    },
    "IBM": {
        "official": "International Business Machines Corporation",
        "search": "IBM",
        "job_search_names": ["IBM"],
        "patent_search": ["International Business Machines Corporation"],
        "domain": "ibm.com",
        "aliases": ["IBM", "International Business Machines", "IBM Corporation"],
    },
    "ORCL": {
        "official": "Oracle Corporation",
        "search": "Oracle",
        "job_search_names": ["Oracle"],
        "patent_search": ["Oracle International Corporation", "Oracle America, Inc."],
        "domain": "oracle.com",
        "aliases": ["Oracle", "Oracle Corporation", "ORCL"],
    },
    "CRM": {
        "official": "Salesforce, Inc.",
        "search": "Salesforce",
        "job_search_names": ["Salesforce"],
        "patent_search": ["Salesforce, Inc.", "salesforce.com, inc."],
        "domain": "salesforce.com",
        "aliases": ["Salesforce", "Salesforce Inc", "CRM"],
    },
    "DG": {
        "official": "Dollar General Corporation",
        "search": "Dollar General",
        "job_search_names": ["Dollar General"],
        "patent_search": ["Dollar General Corporation"],
        "domain": "dollargeneral.com",
        "aliases": ["Dollar General", "Dollar General Corporation", "DG"],
    },
}


def get_company_search_name(ticker: str) -> Optional[str]:
    """Get the search name for a company ticker."""
    ticker = ticker.upper()
    mapping = COMPANY_NAME_MAPPINGS.get(ticker)
    return mapping["search"] if mapping else None


def get_job_search_names(ticker: str) -> List[str]:
    """Get ALL job search names for a company."""
    ticker = ticker.upper()
    mapping = COMPANY_NAME_MAPPINGS.get(ticker)
    if not mapping:
        return []
    names = mapping.get("job_search_names", [])
    if not names:
        search = mapping.get("search")
        return [search] if search else []
    return names


def get_company_aliases(ticker: str) -> List[str]:
    """Get all valid name aliases for a company ticker."""
    ticker = ticker.upper()
    mapping = COMPANY_NAME_MAPPINGS.get(ticker)
    return mapping["aliases"] if mapping else []


def get_search_name_by_official(official_name: str) -> Optional[str]:
    """Get search name from official company name."""
    official_lower = official_name.lower().strip()
    for ticker, mapping in COMPANY_NAME_MAPPINGS.items():
        if mapping["official"].lower() == official_lower:
            return mapping["search"]
    return None


def get_aliases_by_official(official_name: str) -> List[str]:
    """Get all valid name aliases from official company name."""
    official_lower = official_name.lower().strip()
    for ticker, mapping in COMPANY_NAME_MAPPINGS.items():
        if mapping["official"].lower() == official_lower:
            return mapping["aliases"]
    return []


def get_patent_search_names(ticker: str) -> List[str]:
    """Get ALL patent assignee names to search on PatentsView API."""
    ticker = ticker.upper()
    mapping = COMPANY_NAME_MAPPINGS.get(ticker)
    if not mapping:
        return []
    patent_names = mapping.get("patent_search", [])
    if isinstance(patent_names, str):
        return [patent_names]
    return patent_names


def get_patent_search_name(ticker: str) -> Optional[str]:
    """Get the PRIMARY patent search name for PatentsView API."""
    names = get_patent_search_names(ticker)
    return names[0] if names else None


# =============================================================================
# COMPANY REGISTRY — CIK lookups for SEC EDGAR board proxy analysis
# =============================================================================

class CompanyRegistry:
    """Maps CS3 portfolio tickers to SEC CIK numbers for DEF 14A retrieval."""
    COMPANIES: Dict[str, Dict] = {
        "NVDA": {"cik": "0001045810", "name": "NVIDIA Corporation",          "sector": "technology"},
        "JPM":  {"cik": "0000019617", "name": "JPMorgan Chase & Co.",        "sector": "financial_services"},
        "WMT":  {"cik": "0000104169", "name": "Walmart Inc.",                "sector": "retail"},
        "GE":   {"cik": "0000040545", "name": "GE Aerospace",                "sector": "manufacturing"},
        "DG":   {"cik": "0000029534", "name": "Dollar General Corporation",  "sector": "retail"},
    }

    @classmethod
    def get(cls, ticker: str) -> Dict:
        t = ticker.upper()
        if t in cls.COMPANIES:
            return cls.COMPANIES[t]
        raise ValueError(f"Unknown ticker '{ticker}'.")

    @classmethod
    def register(cls, ticker: str, cik: str, name: str, sector: str = "unknown"):
        cls.COMPANIES[ticker.upper()] = {"cik": cik, "name": name, "sector": sector}

    @classmethod
    def all_tickers(cls) -> List[str]:
        return list(cls.COMPANIES.keys())
