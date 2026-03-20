"""
Board I/O — SEC EDGAR and S3 proxy statement loading
app/pipelines/board_io.py

Extracted from board_analyzer.py. Handles all I/O for loading DEF 14A
proxy statements: S3 cache lookup, EDGAR API fallback, HTML stripping.
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx
import structlog

from app.config.company_mappings import CompanyRegistry
from app.repositories.document_repository import DocumentRepository
from app.services.s3_storage import S3StorageService, get_s3_service

logger = structlog.get_logger()


@dataclass
class ProxyData:
    text_content: str
    tables: List[dict]
    ticker: str


SEC_HEADERS = {
    "User-Agent": "OrgAIR-Scoring-Engine cs3-lab@quantuniversity.com",
    "Accept-Encoding": "gzip, deflate",
}
EDGAR_DELAY = 0.5


def strip_html(html: str) -> str:
    t = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.DOTALL)
    t = re.sub(r"<style[^>]*>.*?</style>", " ", t, flags=re.I | re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&amp;|&quot;|&apos;|&lt;|&gt;", " ", t)
    t = re.sub(r"&#\d+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _load_s3_json(s3: S3StorageService, key: str) -> Optional[dict]:
    data = s3.get_file(key)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8", errors="ignore"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _find_s3_parsed_keys(ticker: str, s3: S3StorageService) -> List[str]:
    for prefix in [f"sec/parsed/{ticker}/DEF14A/", f"sec/parsed/{ticker}/DEF 14A/"]:
        keys = s3.list_files(prefix)
        if keys:
            return keys
    return []


def _load_proxy_from_s3(ticker: str, s3: S3StorageService, doc_repo: DocumentRepository) -> Optional[ProxyData]:
    ticker = ticker.upper()
    parsed_keys = _find_s3_parsed_keys(ticker, s3)
    text_content = ""
    tables: List[dict] = []
    if parsed_keys:
        for key in sorted([k for k in parsed_keys if "_full" in k], reverse=True):
            logger.info(f"[{ticker}] Loading text from S3: {key}")
            data = _load_s3_json(s3, key)
            if data and data.get("text_content"):
                text_content = data["text_content"]
                if data.get("tables"):
                    tables = data["tables"]
                break
        for key in sorted([k for k in parsed_keys if "_tables" in k], reverse=True):
            logger.info(f"[{ticker}] Loading tables from S3: {key}")
            data = _load_s3_json(s3, key)
            if data:
                if isinstance(data, list):
                    tables = data
                elif isinstance(data, dict) and data.get("tables"):
                    tables = data["tables"]
                break
    if not text_content:
        docs = doc_repo.get_by_ticker(ticker)
        proxy_docs = [d for d in docs if d.get("filing_type") in ("DEF 14A", "DEF14A") and d.get("s3_key")]
        if proxy_docs:
            raw_key = proxy_docs[0]["s3_key"]
            logger.info(f"[{ticker}] Loading raw proxy HTML from S3: {raw_key}")
            raw = s3.get_file(raw_key)
            if raw:
                text_content = strip_html(raw.decode("utf-8", errors="ignore"))
    if not text_content:
        return None
    return ProxyData(text_content=text_content, tables=tables, ticker=ticker)


def _fetch_from_edgar(ticker: str, timeout: float = 30.0) -> ProxyData:
    info = CompanyRegistry.get(ticker)
    cik = info["cik"].lstrip("0")
    logger.info(f"[{ticker}] EDGAR fallback")
    url = f"https://data.sec.gov/submissions/CIK{info['cik']}.json"
    time.sleep(EDGAR_DELAY)
    resp = httpx.get(url, headers=SEC_HEADERS, timeout=timeout)
    resp.raise_for_status()
    filings = resp.json().get("filings", {}).get("recent", {})
    for i, form in enumerate(filings.get("form", [])):
        if form == "DEF 14A":
            acc = filings["accessionNumber"][i].replace("-", "")
            doc = filings["primaryDocument"][i]
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
            time.sleep(EDGAR_DELAY)
            r = httpx.get(doc_url, headers=SEC_HEADERS, timeout=timeout, follow_redirects=True)
            r.raise_for_status()
            return ProxyData(text_content=strip_html(r.text), tables=[], ticker=ticker)
    raise RuntimeError(f"[{ticker}] No DEF 14A found in EDGAR")


def load_proxy_data(ticker: str, s3: Optional[S3StorageService] = None, doc_repo: Optional[DocumentRepository] = None, use_s3: bool = True) -> ProxyData:
    ticker = ticker.upper()
    if use_s3:
        try:
            s3 = s3 or get_s3_service()
            doc_repo = doc_repo or DocumentRepository()
            proxy = _load_proxy_from_s3(ticker, s3, doc_repo)
            if proxy and len(proxy.text_content) > 500:
                logger.info(f"[{ticker}] Loaded: {len(proxy.text_content):,} chars, {len(proxy.tables)} tables")
                return proxy
        except Exception as e:
            logger.warning(f"[{ticker}] S3 load failed: {e}")
    return _fetch_from_edgar(ticker)
