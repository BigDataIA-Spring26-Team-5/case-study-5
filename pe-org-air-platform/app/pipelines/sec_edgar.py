#app/pipelines/sec_edgar.py
import time
import structlog
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Generator
from dataclasses import dataclass
from app.core.settings import settings

logger = structlog.get_logger()

@dataclass
class SECFiling:
    accession_number: str
    filing_type: str
    filing_date: str
    primary_document: str
    primary_doc_url: str
    filing_url: str

class SECEdgarCollector:
    """SEC EDGAR filing collector with rate limiting"""
    
    BASE_URL = "https://www.sec.gov"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
    
    # Mapping of ticker to CIK (Central Index Key)
    TICKER_TO_CIK = {
        "CAT": "0000018230",
        "DE": "0000315189",
        "UNH": "0000731766",
        "HCA": "0000860730",
        "ADP": "0000008670",
        "PAYX": "0000723531",
        "WMT": "0000104169",
        "TGT": "0000027419",
        "JPM": "0000019617",
        "GS": "0000886982",
        "NVDA": "0001045810",
        "GE": "0000040545",
        "DG": "0000029534",
        # Additional companies
        "NFLX": "0001065280",
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "GOOGL": "0001652044",
        "AMZN": "0001018724",
        "META": "0001326801",
        "TSLA": "0001318605",
        "ORCL": "0001341439",
        "IBM": "0000051143",
        "INTC": "0000050863",
        "AMD": "0000002488",
        "CRM": "0001108524",
        "NOW": "0001373715",
        "SNOW": "0001640147",
        "UBER": "0001543151",
        "LYFT": "0001759509",
        "ABNB": "0001559720",
        "COIN": "0001679788",
        "PLTR": "0001321655",
        "SHOP": "0001594805",
    }
    
    # Only the primary filing types (no amendments or supplemental)
    FILING_TYPE_MAP = {
        "10-K": ["10-K"],       # Annual report
        "10-Q": ["10-Q"],       # Quarterly report
        "8-K": ["8-K"],         # Material events
        "DEF 14A": ["DEF 14A"], # Definitive proxy statement (annual)
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })
        self.rate_limit = settings.SEC_RATE_LIMIT
        self.last_request_time = 0
        logger.info(f"SEC Edgar Collector initialized (Rate limit: {self.rate_limit}/sec)")

    def _rate_limit_wait(self):
        """Enforce SEC rate limiting (10 requests per second)"""
        elapsed = time.time() - self.last_request_time
        min_interval = 1.0 / self.rate_limit
        if elapsed < min_interval:
            sleep_time = min_interval - elapsed
            logger.debug(f"  ⏳ Rate limiting: sleeping {sleep_time:.3f}s")
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def _make_request(self, url: str) -> Optional[requests.Response]:
        """Make rate-limited request to SEC"""
        self._rate_limit_wait()
        try:
            logger.debug(f"  🌐 Requesting: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"  ❌ Request failed: {url} - {e}")
            return None

    def get_cik(self, ticker: str) -> Optional[str]:
        """Get CIK for a ticker. Checks hardcoded map first, then SEC company_tickers.json."""
        cik = self.TICKER_TO_CIK.get(ticker.upper())
        if cik:
            return cik

        # Dynamic lookup via SEC company_tickers.json
        logger.info(f"  🔍 Looking up CIK for {ticker} via SEC EDGAR")
        try:
            url = f"{self.BASE_URL}/files/company_tickers.json"
            response = self._make_request(url)
            if response:
                for entry in response.json().values():
                    if entry.get("ticker", "").upper() == ticker.upper():
                        cik_padded = str(entry["cik_str"]).zfill(10)
                        self.TICKER_TO_CIK[ticker.upper()] = cik_padded  # cache it
                        logger.info(f"  ✅ Found CIK for {ticker}: {cik_padded}")
                        return cik_padded
                logger.warning(f"  ⚠️  {ticker} not found in SEC company_tickers.json")
        except Exception as e:
            logger.error(f"  ❌ CIK lookup failed for {ticker}: {e}")
        return None

    def get_company_filings(
        self,
        ticker: str,
        filing_types: List[str],
        years_back: int = 3
    ) -> Generator[SECFiling, None, None]:
        """
        Fetch filings for a company.
        Yields SECFiling objects for each matching filing.
        """
        cik = self.get_cik(ticker)
        if not cik:
            logger.error(f"❌ Could not find CIK for ticker: {ticker}")
            return

        cik_no_padding = cik.lstrip("0")
        logger.info(f"📋 Fetching filings for {ticker} (CIK: {cik})")
        
        # Get company submissions
        url = f"{self.SUBMISSIONS_URL}/CIK{cik}.json"
        response = self._make_request(url)
        if not response:
            return

        data = response.json()
        filings = data.get("filings", {}).get("recent", {})
        
        # Calculate date cutoff
        cutoff_date = datetime.now() - timedelta(days=years_back * 365)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")
        logger.info(f"  📅 Looking for filings after {cutoff_str}")
        
        # Build list of acceptable form types
        acceptable_forms = []
        for ft in filing_types:
            acceptable_forms.extend(self.FILING_TYPE_MAP.get(ft, [ft]))
        
        # Process filings
        form_list = filings.get("form", [])
        date_list = filings.get("filingDate", [])
        accession_list = filings.get("accessionNumber", [])
        primary_doc_list = filings.get("primaryDocument", [])
        
        found_count = 0
        for i, form in enumerate(form_list):
            filing_date = date_list[i]
            
            # Check date
            if filing_date < cutoff_str:
                continue
                
            # Check form type
            if form not in acceptable_forms:
                continue
            
            accession = accession_list[i].replace("-", "")
            primary_doc = primary_doc_list[i]
            
            # Build URLs
            filing_url = f"{self.ARCHIVES_URL}/{cik_no_padding}/{accession}"
            primary_doc_url = f"{filing_url}/{primary_doc}"
            
            found_count += 1
            logger.info(f"  📄 Found: {form} filed {filing_date}")
            
            yield SECFiling(
                accession_number=accession_list[i],
                filing_type=form,
                filing_date=filing_date,
                primary_document=primary_doc,
                primary_doc_url=primary_doc_url,
                filing_url=filing_url
            )
        
        logger.info(f"  ✅ Found {found_count} filings for {ticker}")

    def download_filing(self, filing: SECFiling) -> Optional[bytes]:
        """Download the primary document of a filing"""
        logger.info(f"  ⬇️  Downloading: {filing.filing_type} ({filing.filing_date})")
        response = self._make_request(filing.primary_doc_url)
        if response:
            logger.info(f"  ✅ Downloaded {len(response.content):,} bytes")
            return response.content
        return None

    def download_filing_index(self, filing: SECFiling) -> Optional[Dict]:
        """Download the filing index to get all documents"""
        index_url = f"{filing.filing_url}/index.json"
        response = self._make_request(index_url)
        if response:
            return response.json()
        return None


# Singleton
_collector: Optional[SECEdgarCollector] = None

def get_sec_collector() -> SECEdgarCollector:
    global _collector
    if _collector is None:
        _collector = SECEdgarCollector()
    return _collector