"""CS2 Client — Evidence from S3 (jobs, patents, techstack, glassdoor, SEC chunks)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

import httpx

from app.clients.base import BaseAPIClient
from app.utils.id_utils import stable_evidence_id
from app.prompts.rag_prompts import CS2_KEYWORD_EXPANSION_USER, CS2_SIGNAL_SUMMARY_USER

logger = logging.getLogger(__name__)

from app.core.settings import settings as _settings

GROQ_API_KEY = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_API_URL = _settings.GROQ_API_URL
GROQ_BASE_URL = _settings.GROQ_API_URL.rsplit("/chat/completions", 1)[0]

SIGNAL_KEYWORDS: Dict[str, List[str]] = {
    "technology_hiring": [
        "machine learning", "data science", "AI engineer", "software engineer",
        "cloud", "python", "MLOps", "LLM", "deep learning", "NLP",
    ],
    "innovation_activity": [
        "patent", "R&D", "invention", "USPTO", "intellectual property",
        "innovation", "research", "technology development",
    ],
    "digital_presence": [
        "cloud infrastructure", "AWS", "Azure", "GCP", "tech stack",
        "digital transformation", "AI platform", "data platform", "SaaS",
    ],
    "leadership_signals": [
        "CEO", "CTO", "CDO", "board", "executive", "strategy",
        "AI governance", "digital strategy", "technology leadership",
    ],
    "glassdoor_culture": [
        "culture", "innovation", "data-driven", "AI awareness",
        "change readiness", "employee", "work environment",
    ],
    "board_governance": [
        "board committee", "tech committee", "AI expertise", "independent director",
        "risk oversight", "governance", "proxy statement", "DEF 14A",
    ],
}

SOURCE_TYPES = [
    "sec_10k_item_1",
    "sec_10k_item_1a",
    "sec_10k_item_7",
    "job_posting_linkedin",
    "job_posting_indeed",
    "patent_uspto",
    "glassdoor_review",
    "board_proxy_def14a",
    "digital_presence",
    "analyst_interview",
    "dd_data_room",
]

# Minimum word count for a chunk to be considered substantive
_MIN_CHUNK_WORDS = 40

# Patterns that identify table-of-contents / boilerplate chunks
# These are useless for RAG and should be filtered out
_TOC_PATTERNS = [
    # Classic ToC: "Item X. Title  PageNum" repeated 4+ times
    re.compile(r'(?:item\s+\d+[a-z]?\.\s+\S.{0,60}\d{1,3}\s*){4,}', re.IGNORECASE | re.DOTALL),
    # Index-page boilerplate
    re.compile(r'part\s+[ivx]+\s+item\s+\d', re.IGNORECASE),
]

# Regex to count "Item N." occurrences — ToC chunks have many
_ITEM_PATTERN = re.compile(r'\bitem\s+\d+[a-z]?\.', re.IGNORECASE)

# Proxy statement boilerplate chunks to skip
_PROXY_BOILERPLATE = re.compile(
    r'(?:copyright\s+\d{4}|all rights reserved|'
    r'\$100 invested on december|'
    r'reinvestment of dividends|'
    r'index.*\$100|'
    r'vote your shares at|'
    r'please call \d|'
    r'toll free|'
    r'technicians available)',
    re.IGNORECASE,
)


def _is_toc_chunk(text: str) -> bool:
    """
    Return True if the chunk looks like a table of contents or boilerplate.

    Criteria:
    1. High density of "Item N." references relative to total words
    2. Very low ratio of unique meaningful words (mostly page numbers + item refs)
    3. Matches known ToC regex patterns
    """
    words = text.split()
    if len(words) < _MIN_CHUNK_WORDS:
        return True

    # Count Item N. occurrences
    item_matches = len(_ITEM_PATTERN.findall(text))
    # If more than 1 Item reference per 30 words → likely ToC
    if item_matches > 0 and len(words) / item_matches < 30:
        return True

    # Check ToC pattern
    for pat in _TOC_PATTERNS:
        if pat.search(text):
            return True

    return False


def _is_proxy_boilerplate(text: str) -> bool:
    """Return True if the chunk is proxy statement boilerplate (stock graphs, voting instructions)."""
    return bool(_PROXY_BOILERPLATE.search(text))


def _section_to_source_type(section: str) -> str:
    s = (section or "").lower().replace(" ", "_")
    if "item_1a" in s or "1a" in s or "risk" in s:
        return "sec_10k_item_1a"
    if "item_7" in s or "item7" in s or "mda" in s or "management" in s:
        return "sec_10k_item_7"
    return "sec_10k_item_1"


def _section_to_signal_category(section: str) -> str:
    """
    Map SEC section name to the most appropriate signal category.

    More granular than previous version — Item 7 (MD&A) maps to
    leadership_signals so it gets tagged as leadership/use_case_portfolio
    dimensions rather than digital_presence.
    """
    s = (section or "").lower().replace(" ", "_")

    # Item 1A — Risk Factors → AI governance / compliance signals
    if "item_1a" in s or "1a" in s or "risk" in s:
        return "governance_signals"

    # Item 7 — MD&A → leadership signals (strategy, results, outlook)
    if "item_7" in s or "item7" in s or "mda" in s or "management" in s:
        return "leadership_signals"

    # Proxy/governance sections
    if "def14a" in s or "proxy" in s or "governance" in s or "board" in s:
        return "governance_signals"

    # Default Item 1 — Business description → digital presence / use cases
    return "digital_presence"


def _content_to_signal_category(text: str) -> str:
    """
    Infer signal category from chunk content when section metadata is missing
    or too generic. Uses keyword presence to determine the best mapping.
    """
    t = text.lower()

    # Governance signals
    if any(kw in t for kw in ["governance", "board", "committee", "compliance",
                               "risk factor", "regulatory", "oversight"]):
        return "governance_signals"

    # Leadership / strategy signals
    if any(kw in t for kw in ["strategy", "investment", "revenue", "growth",
                               "competition", "market", "outlook", "results"]):
        return "leadership_signals"

    # Technology hiring signals
    if any(kw in t for kw in ["machine learning", "mlops", "pytorch", "tensorflow",
                               "sagemaker", "mlflow", "deep learning", "llm"]):
        return "technology_hiring"

    # Innovation signals
    if any(kw in t for kw in ["patent", "research", "r&d", "innovation",
                               "intellectual property"]):
        return "innovation_activity"

    # Default
    return "digital_presence"


async def expand_keywords_with_groq(ticker: str, category: str) -> List[str]:
    """Thin wrapper — delegates to CS2Client._expand_keywords_with_groq for callers outside the class."""
    if not GROQ_API_KEY:
        return SIGNAL_KEYWORDS.get(category, [])
    _client = CS2Client()
    return await _client._expand_keywords_with_groq(ticker, category)


async def get_groq_signal_summary(ticker: str, category: str, raw_data: Dict[str, Any]) -> Optional[str]:
    """Thin wrapper — delegates to CS2Client._get_groq_signal_summary for callers outside the class."""
    if not GROQ_API_KEY:
        return None
    _client = CS2Client()
    return await _client._get_groq_signal_summary(ticker, category, raw_data)


@dataclass
class CS2Evidence:
    evidence_id: str
    company_id: str
    source_type: str
    signal_category: str
    content: str
    confidence: float = 0.0
    extracted_entities: Dict[str, Any] = field(default_factory=dict)
    fiscal_year: Optional[str] = None
    source_url: Optional[str] = None
    page_number: Optional[int] = None
    indexed_in_cs4: bool = False


class CS2Client(BaseAPIClient):
    """Fetches evidence directly from S3, mirroring vr_scoring_service._load_jobs_from_s3()."""

    def __init__(self, company_repo=None):
        super().__init__(
            base_url=GROQ_BASE_URL,
            service_name="groq",
            timeout=15.0,
            max_retries=3,
        )
        from app.services.s3_storage import get_s3_service
        self._s3 = get_s3_service()
        self._company_repo = company_repo

    async def _expand_keywords_with_groq(self, ticker: str, category: str) -> List[str]:
        """Use Groq LLM to expand keywords for a given signal category and company."""
        base_keywords = SIGNAL_KEYWORDS.get(category, [])
        if not GROQ_API_KEY:
            return base_keywords
        prompt = CS2_KEYWORD_EXPANSION_USER.format(
            ticker=ticker,
            category=category,
            base_keywords=", ".join(base_keywords),
        )
        try:
            result = await self.post(
                "/chat/completions",
                json_body={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.3,
                },
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            text = result["choices"][0]["message"]["content"].strip()
            expanded = [kw.strip() for kw in text.split(",") if kw.strip()]
            return list(set(base_keywords + expanded))
        except Exception as e:
            logger.warning("groq_keyword_expansion_failed ticker=%s category=%s error=%s", ticker, category, e)
            return base_keywords

    async def _get_groq_signal_summary(self, ticker: str, category: str, raw_data: Dict[str, Any]) -> Optional[str]:
        """Use Groq to generate a short natural language summary of a signal result."""
        if not GROQ_API_KEY:
            return None
        prompt = CS2_SIGNAL_SUMMARY_USER.format(
            category=category,
            ticker=ticker,
            data=json.dumps(raw_data, default=str)[:1500],
        )
        try:
            result = await self.post(
                "/chat/completions",
                json_body={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.4,
                },
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("groq_summary_failed ticker=%s category=%s error=%s", ticker, category, e)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_evidence(
        self,
        company_id: Optional[str] = None,
        ticker: Optional[str] = None,
        source_types: Optional[List[str]] = None,
        signal_categories: Optional[List[str]] = None,
        min_confidence: float = 0.0,
        indexed: Optional[bool] = None,
        since: Optional[datetime] = None,
    ) -> List[CS2Evidence]:
        """Fetch evidence from S3 for the given ticker/company."""
        resolved_ticker = ticker or self._resolve_ticker(company_id) or company_id or ""
        resolved_ticker = resolved_ticker.upper()

        fetchers = {
            "technology_hiring": self._fetch_jobs,
            "innovation_activity": self._fetch_patents,
            "digital_presence": self._fetch_techstack,
            "culture_signals": self._fetch_glassdoor,
            "sec_chunks": self._fetch_sec_chunks,
        }

        requested = set(signal_categories) if signal_categories else set(fetchers.keys())
        if signal_categories and "sec_chunks" not in signal_categories:
            requested.discard("sec_chunks")

        all_evidence: List[CS2Evidence] = []
        for cat, fn in fetchers.items():
            if cat not in requested:
                continue
            try:
                all_evidence.extend(fn(resolved_ticker))
            except Exception:
                pass

        result = [e for e in all_evidence if e.confidence >= min_confidence]
        if source_types:
            result = [e for e in result if e.source_type in source_types]
        return result

    def mark_indexed(self, evidence_ids: List[str]) -> int:
        """Mark evidence as indexed. Returns count (always succeeds — no endpoint needed)."""
        return len(evidence_ids)

    # ------------------------------------------------------------------
    # S3 fetchers
    # ------------------------------------------------------------------

    def _fetch_jobs(self, ticker: str) -> List[CS2Evidence]:
        prefix = f"signals/jobs/{ticker}/"
        keys = sorted(self._s3.list_files(prefix), reverse=True)
        postings = []
        for key in keys:
            raw = self._s3.get_file(key)
            if raw is None:
                continue
            data = json.loads(raw)
            postings = data.get("jobs", data.get("job_postings", []))
            if postings:
                break

        results = []
        for p in postings:
            title = p.get("title", "")
            desc = p.get("description", "")
            content = f"{title} — {desc}".strip(" —")
            if not content:
                continue
            source = p.get("source", "")
            source_type = (
                "job_posting_linkedin" if "linkedin" in source.lower()
                else "job_posting_indeed"
            )
            results.append(CS2Evidence(
                evidence_id=p.get("job_id") or stable_evidence_id(ticker, source_type, content),
                company_id=ticker,
                source_type=source_type,
                signal_category="technology_hiring",
                content=content,
                confidence=0.7,
                fiscal_year=None,
            ))
        return results

    def _fetch_patents(self, ticker: str) -> List[CS2Evidence]:
        prefix = f"signals/patents/{ticker}/"
        keys = sorted(self._s3.list_files(prefix), reverse=True)
        patents = []
        for key in keys:
            raw = self._s3.get_file(key)
            if raw is None:
                continue
            data = json.loads(raw)
            patents = data.get("patents", [])
            if patents:
                break

        results = []
        for p in patents:
            title = p.get("title", "")
            abstract = p.get("abstract", "")
            categories = ", ".join(p.get("ai_categories", []))
            cat_str = f" | AI Categories: {categories}" if categories else ""
            content = f"[Patent] {title} — {abstract}{cat_str}".strip()
            if not content or content == "[Patent]":
                continue
            patent_num = p.get("patent_number") or p.get("patent_id", "")
            evidence_id = f"patent_{ticker}_{patent_num}" if patent_num else stable_evidence_id(ticker, "patent_uspto", content)
            results.append(CS2Evidence(
                evidence_id=evidence_id,
                company_id=ticker,
                source_type="patent_uspto",
                signal_category="innovation_activity",
                content=content,
                confidence=0.8,
            ))
        return results

    def _fetch_techstack(self, ticker: str) -> List[CS2Evidence]:
        prefix = f"signals/digital/{ticker}/"
        keys = sorted(self._s3.list_files(prefix), reverse=True)
        data = {}
        for key in keys:
            raw = self._s3.get_file(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data:
                break

        ai_techs: List[str] = data.get("ai_technologies_detected", [])
        wap_techs: List[str] = data.get("wappalyzer_techs", [])
        all_techs = ai_techs + [t for t in wap_techs if t not in ai_techs]
        if not all_techs:
            return []

        content = "Detected technologies: " + ", ".join(all_techs[:50])
        return [CS2Evidence(
            evidence_id=stable_evidence_id(ticker, "digital_presence", content),
            company_id=ticker,
            source_type="digital_presence",
            signal_category="digital_presence",
            content=content,
            confidence=0.6,
        )]

    def _fetch_glassdoor(self, ticker: str) -> List[CS2Evidence]:
        prefix = f"glassdoor_signals/raw/{ticker}/"
        keys = sorted(self._s3.list_files(prefix), reverse=True)
        raw = None
        for key in keys:
            raw = self._s3.get_file(key)
            if raw is not None:
                break
        if raw is None:
            return []
        wrapper = json.loads(raw)
        reviews = wrapper if isinstance(wrapper, list) else wrapper.get("reviews", [])

        results = []
        for r in reviews:
            title = r.get("title", "")
            pros = r.get("pros", "")
            cons = r.get("cons", "")
            content = f"{title} — Pros: {pros} Cons: {cons}".strip()
            if not content or content == "— Pros:  Cons:":
                continue
            results.append(CS2Evidence(
                evidence_id=r.get("review_id") or stable_evidence_id(ticker, "glassdoor_review", content),
                company_id=ticker,
                source_type="glassdoor_review",
                signal_category="culture_signals",
                content=content,
                confidence=0.65,
            ))
        return results

    def _fetch_sec_chunks(self, ticker: str) -> List[CS2Evidence]:
        """
        Fetch SEC chunks using Snowflake chunk repo for reliable S3 key lookup.

        FIX (S3 ListObjects): Uses chunk_repo.get_all_s3_keys() which queries
        Snowflake — bypasses the IAM ListObjects denial on sec/chunks/ prefix.

        FIX (chunk quality): Filters out table-of-contents chunks and proxy
        boilerplate (stock performance graphs, voting instructions) that pollute
        retrieval results with useless content.

        DocumentChunk fields: document_id, chunk_index, content, section,
        start_char, end_char, word_count.
        Stored in S3 as a raw JSON list (NOT wrapped in {"chunks": [...]}).
        """
        try:
            from app.repositories.chunk_repository import ChunkRepository
            chunk_repo = ChunkRepository()
        except Exception as e:
            logger.warning("sec_chunk_repo_unavailable", ticker=ticker, error=str(e))
            return []

        results: List[CS2Evidence] = []
        skipped_toc = 0
        skipped_boilerplate = 0
        skipped_short = 0

        filing_configs = [
            ("10-K",    False),
            ("DEF 14A", True),
        ]

        for filing_type, is_proxy in filing_configs:
            try:
                s3_keys = chunk_repo.get_all_s3_keys(ticker, filing_type)
            except Exception as e:
                logger.warning(
                    "sec_chunk_s3_keys_failed ticker=%s filing_type=%s error=%s",
                    ticker, filing_type, e,
                )
                continue

            logger.info(
                "sec_chunks_s3_keys_found ticker=%s filing_type=%s count=%d",
                ticker, filing_type, len(s3_keys),
            )

            for s3_key in s3_keys:
                try:
                    raw = self._s3.get_file(s3_key)
                    if raw is None:
                        continue
                    data = json.loads(raw)
                    chunks = data if isinstance(data, list) else data.get("chunks", [])

                    for chunk in chunks:
                        text = chunk.get("content") or chunk.get("text", "")
                        if not text:
                            skipped_short += 1
                            continue

                        text = text.strip()

                        # Filter: too short to be useful
                        if len(text.split()) < _MIN_CHUNK_WORDS:
                            skipped_short += 1
                            continue

                        # Filter: table of contents
                        if _is_toc_chunk(text):
                            skipped_toc += 1
                            continue

                        # Filter: proxy boilerplate (stock graph, voting instructions)
                        if is_proxy and _is_proxy_boilerplate(text):
                            skipped_boilerplate += 1
                            continue

                        section = chunk.get("section") or ""

                        if is_proxy:
                            source_type = "board_proxy_def14a"
                            signal_cat  = "governance_signals"
                        else:
                            source_type = _section_to_source_type(section)
                            # Use section metadata if available, otherwise infer from content
                            if section:
                                signal_cat = _section_to_signal_category(section)
                            else:
                                signal_cat = _content_to_signal_category(text)

                        chunk_index = chunk.get("chunk_index", 0)
                        fname = s3_key.split("/")[-1].replace("_chunks.json", "")
                        evidence_id = f"{ticker}_{fname}_{chunk_index}"

                        results.append(CS2Evidence(
                            evidence_id=evidence_id,
                            company_id=ticker,
                            source_type=source_type,
                            signal_category=signal_cat,
                            content=text,
                            confidence=0.9,
                            page_number=chunk_index,
                            fiscal_year=None,
                        ))
                except Exception as e:
                    logger.warning(
                        "sec_chunk_read_failed ticker=%s s3_key=%s error=%s",
                        ticker, s3_key, e,
                    )
                    continue

        logger.info(
            "sec_chunks_fetched ticker=%s total=%d skipped_toc=%d "
            "skipped_boilerplate=%d skipped_short=%d",
            ticker, len(results), skipped_toc, skipped_boilerplate, skipped_short,
        )
        return results

    # ------------------------------------------------------------------
    # Groq-enhanced async methods
    # ------------------------------------------------------------------

    async def get_keywords_for_category(self, ticker: str, category: str) -> List[str]:
        """Groq-expanded keywords for a signal category; falls back to static list."""
        return await self._expand_keywords_with_groq(ticker, category)

    async def get_full_evidence_with_keywords(self, ticker: str, category: str) -> Dict[str, Any]:
        """Evidence from S3 for ticker/category + Groq-expanded keywords + IC summary."""
        evidence = self.get_evidence(
            ticker=ticker,
            signal_categories=[category] if category else None,
        )
        keywords = await self._expand_keywords_with_groq(ticker, category)
        summary = await self._get_groq_signal_summary(ticker, category, {
            "evidence_count": len(evidence),
            "category": category,
        })
        return {
            "ticker": ticker.upper(),
            "category": category,
            "keywords": keywords,
            "evidence": evidence,
            "groq_summary": summary,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_ticker(self, company_id: Optional[str]) -> Optional[str]:
        if not company_id:
            return None
        try:
            if self._company_repo is None:
                from app.repositories.company_repository import CompanyRepository
                self._company_repo = CompanyRepository()
            company = self._company_repo.get_by_id(company_id)
            if company:
                return company.get("ticker") or company.get("symbol")
        except Exception:
            pass
        return company_id