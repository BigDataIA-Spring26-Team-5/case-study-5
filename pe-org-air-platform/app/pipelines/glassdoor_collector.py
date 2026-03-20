# app/pipelines/glassdoor_collector.py
"""
Multi-Source Culture Collector (CS3)

Changes in this version (v4 — Option 3):
- Expanded keyword lists to match real employee review language
- 70% keyword / 30% rating blend for ALL four culture components
- Rating baseline scaled 0-100 from 1-5 star range
- Better differentiation: NVDA (4.61 rating) vs DG (2.64 rating)
"""

import json
import logging
import structlog
import os
import re
import sys
import time
from bs4 import BeautifulSoup
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

from app.core.settings import settings as _settings

# ---------------------------------------------------------------------
# PATH + ENV
# ---------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_APP_DIR = _THIS_FILE.parent.parent
_PROJECT_ROOT = _APP_DIR.parent
for _p in [str(_PROJECT_ROOT), str(_APP_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

load_dotenv(_PROJECT_ROOT / ".env")

logger = structlog.get_logger()

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)


# =====================================================================
# DATA MODELS
# =====================================================================

@dataclass
class CultureReview:
    review_id: str
    rating: float
    title: str
    pros: str
    cons: str
    advice_to_management: Optional[str] = None
    is_current_employee: bool = True
    job_title: str = ""
    review_date: Optional[datetime] = None
    source: str = "unknown"

    def __post_init__(self):
        if self.review_date is None:
            self.review_date = datetime.now(timezone.utc)


@dataclass
class CultureSignal:
    company_id: str
    ticker: str
    innovation_score: Decimal = Decimal("50.00")
    data_driven_score: Decimal = Decimal("0.00")
    change_readiness_score: Decimal = Decimal("50.00")
    ai_awareness_score: Decimal = Decimal("0.00")
    overall_score: Decimal = Decimal("25.00")
    review_count: int = 0
    avg_rating: Decimal = Decimal("0.00")
    current_employee_ratio: Decimal = Decimal("0.000")
    confidence: Decimal = Decimal("0.000")
    source_breakdown: Dict[str, int] = field(default_factory=dict)
    positive_keywords_found: List[str] = field(default_factory=list)
    negative_keywords_found: List[str] = field(default_factory=list)
    # "keyword_blend" = scored from real reviews | "groq_estimate" = no reviews, LLM-estimated | "no_data" = defaults
    scoring_method: str = "no_data"

    def to_json(self, indent=2) -> str:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
        return json.dumps(d, indent=indent, default=str)


# =====================================================================
# COMPANY REGISTRY
# =====================================================================

COMPANY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "NVDA": {
        "name": "NVIDIA", "sector": "Technology",
        "glassdoor_id": "NVIDIA",
        "indeed_slugs": ["NVIDIA"],
        "careerbliss_slug": "nvidia",
    },
    "JPM": {
        "name": "JPMorgan Chase", "sector": "Financial Services",
        "glassdoor_id": "JPMorgan-Chase",
        "indeed_slugs": ["JPMorgan-Chase", "jpmorgan-chase"],
        "careerbliss_slug": "jpmorgan-chase",
    },
    "WMT": {
        "name": "Walmart", "sector": "Consumer Retail",
        "glassdoor_id": "Walmart",
        "indeed_slugs": ["Walmart"],
        "careerbliss_slug": "walmart",
    },
    "GE": {
        "name": "GE Aerospace", "sector": "Industrials Manufacturing",
        "glassdoor_id": "GE-Aerospace",
        "indeed_slugs": ["GE-Aerospace", "General-Electric"],
        "careerbliss_slug": "ge-aerospace",
    },
    "DG": {
        "name": "Dollar General", "sector": "Consumer Retail",
        "glassdoor_id": "Dollar-General",
        "indeed_slugs": ["Dollar-General"],
        "careerbliss_slug": "dollar-general",
    },
    "NFLX": {
        "name": "Netflix", "sector": "Technology",
        "glassdoor_id": "Netflix",
        "indeed_slugs": ["Netflix"],
        "careerbliss_slug": "netflix",
    },
}

ALLOWED_TICKERS = set(COMPANY_REGISTRY.keys())
VALID_SOURCES = {"glassdoor", "indeed", "careerbliss"}


def _auto_register(ticker: str) -> None:
    """
    Dynamically add an unknown ticker to COMPANY_REGISTRY using
    COMPANY_NAME_MAPPINGS (config.py) as the source of truth, with
    simple heuristics as fallback.
    """
    from app.config import COMPANY_NAME_MAPPINGS
    mapping = COMPANY_NAME_MAPPINGS.get(ticker, {})
    name = mapping.get("search") or mapping.get("official") or ticker.capitalize()
    # Derive slugs from name: "Netflix" → "Netflix", "JPMorgan Chase" → "JPMorgan-Chase"
    slug = name.replace(" ", "-")
    careerbliss = name.lower().replace(" ", "-")
    COMPANY_REGISTRY[ticker] = {
        "name": name,
        "sector": "Unknown",
        "glassdoor_id": slug,
        "indeed_slugs": [slug],
        "careerbliss_slug": careerbliss,
    }
    ALLOWED_TICKERS.add(ticker)
    logger.info(f"[glassdoor_collector] Auto-registered ticker '{ticker}': name='{name}'")


def validate_ticker(ticker: str) -> str:
    t = ticker.upper()
    if t not in ALLOWED_TICKERS:
        _auto_register(t)
    return t


def all_tickers() -> List[str]:
    return sorted(ALLOWED_TICKERS)


# =====================================================================
# HELPERS
# =====================================================================

def _normalize_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()

    iso = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso:
        try:
            return datetime.strptime(iso.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
                "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    rel = re.match(r"(\d+)\s+(day|week|month|year)s?\s+ago", raw, re.I)
    if rel:
        num = int(rel.group(1))
        unit = rel.group(2).lower()
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        return datetime.now(timezone.utc) - timedelta(days=num * days)

    return None


# =====================================================================
# CULTURE COLLECTOR
# =====================================================================

class CultureCollector:
    MAX_REVIEWS_TOTAL: Optional[int] = None
    MAX_REVIEWS_PER_SOURCE: Optional[int] = None

    DEFAULT_MAX_GLASSDOOR_PAGES = 30
    DEFAULT_MAX_INDEED_PAGES = 25
    DEFAULT_MAX_CAREERBLISS_CLICKS = 15

    SOURCE_RELIABILITY = {
        "glassdoor":   Decimal("0.85"),
        "indeed":      Decimal("0.80"),
        "careerbliss": Decimal("0.75"),
        "unknown":     Decimal("0.70"),
    }

    RAPIDAPI_HOST = "real-time-glassdoor-data.p.rapidapi.com"
    RAPIDAPI_BASE = f"https://{RAPIDAPI_HOST}"

    # ──────────────────────────────────────────────────────────────
    # EXPANDED KEYWORD LISTS (Option 3)
    #
    # Original CS3 Table 2 keywords + real employee review language.
    # Employees don't write "data-driven" — they write "decisions
    # based on numbers". These expansions capture actual usage while
    # staying within the CS3 framework's intent.
    # ──────────────────────────────────────────────────────────────
        
    AI_AWARENESS_KEYWORDS_ADDITIONS = [
        # Hardware/infra terms NVDA employees actually use
        "gpus", "tensor cores", "tensor core",
        "inference", "training",                    # daily work vocabulary
        "accelerated computing", "accelerator",
        "high performance computing", "hpc",
        "supercomputer", "supercomputing",
        "parallel computing", "parallel processing",
        # Product/platform names that signal AI culture
        "dgx", "drive", "omniverse",
        "triton", "tensorrt", "nemo",
        "isaac sim", "metropolis",
        # Industry terms employees reference
        "foundation model", "transformer",
        "large language", "diffusion model",
        "generative", "gen ai",
        # Broader AI ecosystem vocabulary
        "neural net", "neural nets",
        "ai chip", "ai chips", "ai hardware",
        "model serving", "model deployment",
        "mlops", "ml ops",
        "ai infrastructure", "ai infra",
        "data center", "data centers",
        "cloud computing",
    ]

    DATA_DRIVEN_KEYWORDS_ADDITIONS = [
        # Engineering-culture equivalents of "data-driven"
        "performance", "benchmarks", "benchmark",
        "throughput", "latency",
        "optimization", "optimize", "optimized",
        "profiling", "profiler",
        "telemetry",
        "a/b test", "a/b testing",
        "experiment", "experiments",
        "results-driven", "results driven",
        "evidence", "rigorous",
        "specifications", "specs",
        # Engineering-culture equivalents
        "performance", "benchmarks", "benchmark",
        "throughput", "latency",
        "optimization", "optimize", "optimized",
        "profiling", "telemetry",
        "a/b test", "a/b testing",
        "experiment", "experiments",
        "results-driven", "results driven",
        "specifications", "specs",
    ]

    # Also add "gpus" to WHOLE_WORD_KEYWORDS to avoid substring matches
    # (e.g. matching inside a longer word), and add "hpc"
    WHOLE_WORD_KEYWORDS_ADDITIONS = ["gpus", "hpc", "dgx", "drive"]
    INNOVATION_POSITIVE = [
        # CS3 Table 2 original
        "innovative", "cutting-edge", "forward-thinking",
        "encourages new ideas", "experimental", "creative freedom",
        "startup mentality", "move fast", "disruptive",
        # Expanded — real employee language
        "innovation", "pioneering", "bleeding edge",
        "push boundaries", "think outside the box", "creative",
        "new technology", "new technologies", "latest technology",
        "state of the art", "state-of-the-art", "groundbreaking",
        "trailblazing", "leading edge", "next generation",
        "freedom to innovate", "encouraged to experiment",
        "culture of innovation", "innovative culture",
        "exciting technology", "exciting projects",
        "cool technology", "cool projects", "cool products",
        "world-class technology", "world class",
        "tech-forward", "technology-driven",
         # Research-org innovation signals
        "research", "research team", "research lab",
        "publish", "published", "paper", "papers",
        "open source", "open-source",
        "breakthrough", "breakthroughs",
        "new architecture", "new chip",
        "next-gen", "next gen", "next generation",
        # Real employee review language (verified from Glassdoor)
        "life's work", "life's best work",
        "best in class", "best-in-class",
        "defining the future", "changing the world",
        "highest performance", "world changing", "world-changing", "tech-forward", "technology-driven",
    ]

    INNOVATION_NEGATIVE = [
        # CS3 Table 2 original
        "bureaucratic", "slow to change", "resistant",
        "outdated", "stuck in old ways", "red tape",
        "politics", "siloed", "hierarchical",
        # Expanded — real employee language
        "stagnant", "old-fashioned", "behind the times",
        "not innovative", "lack of innovation", "no innovation",
        "legacy systems", "legacy technology", "legacy processes",
        "old technology", "outdated technology", "outdated systems",
        "too many processes", "too much process",
        "micromanagement", "micromanaged", "micro-managed",
        "top-down", "command and control",
    ]

    DATA_DRIVEN_KEYWORDS = [
        # CS3 Table 2 original
        "data-driven", "metrics", "evidence-based",
        "analytical", "kpis", "dashboards", "data culture",
        "measurement", "quantitative",
        # Expanded — real employee language
        "data informed", "analytics", "data-centric",
        "numbers-driven", "metrics-focused", "metrics-obsessed",
        "data focused", "data analysis", "reporting",
        "performance metrics", "measure everything",
        "tracking", "based on data", "decisions based on",
        "data transparency", "data-oriented",
        "business intelligence", "bi tools",
        "insights-driven", "evidence-driven",
        "quantify", "benchmarks", "scorecards",
    ]
    AI_AWARENESS_KEYWORDS = [
        # CS3 Table 2 original
        "ai", "artificial intelligence", "machine learning",
        "automation", "data science", "ml", "algorithms",
        "predictive", "neural network",
        # Expanded — real employee language
        "deep learning", "nlp", "llm", "generative ai",
        "chatbot", "computer vision",
        "gpu", "cuda", "model training",
        "chatgpt", "copilot", "ai-powered", "ai-driven",
        "ai tools", "ai platform", "ai products",
        "autonomous", "robotics", "self-driving",
        "intelligent automation", "rpa",
        "data engineering", "data pipeline", "data platform",
        "ml engineering", "ml platform", "ml infrastructure",
        "natural language", "recommendation engine",
        "ai transformation", "ai strategy",
        "ai initiatives", "ai investment",
        # Domain-specific (semiconductor / HPC / infra)
        "gpus", "tensor cores", "tensor core",
        "inference", "training",
        "accelerated computing", "accelerator",
        "high performance computing", "hpc",
        "supercomputer", "supercomputing",
        "parallel computing", "parallel processing",
        # Product / platform names
        "dgx", "omniverse", "triton", "tensorrt", "nemo",
        # Industry terms
        "foundation model", "transformer",
        "large language", "diffusion model",
        "generative", "gen ai",
        "neural net", "neural nets",
        "ai chip", "ai chips", "ai hardware",
        "model serving", "model deployment",
        "ai infrastructure", "ai infra",
        "data center", "data centers",
        "cloud computing",
    ]

    CHANGE_POSITIVE = [
        # CS3 Table 2 original
        "agile", "adaptive", "fast-paced", "embraces change",
        "continuous improvement", "growth mindset",
        # Expanded — real employee language
        "evolving", "dynamic", "transforming",
        "always changing", "constantly evolving", "rapidly growing",
        "open to new ideas", "receptive to feedback",
        "learning culture", "learn and grow",
        "willing to adapt", "flexible", "nimble",
        "move quickly", "iterate", "iterate quickly",
        "fail fast", "learn from failure",
        "empowered", "autonomy", "ownership",
        "progressive", "forward-looking",
        "transformation", "modernization", "modernizing",
        "progressive", "forward-looking",
        "transformation", "modernization", "modernizing",
        # Real employee review language (verified from Glassdoor)
        "collaborative", "collaboration",
        "flat culture", "flat organization",
        "intellectual honesty",
        "one team",
        "no politics", "less political", "least political",
        "empowering culture",
    ]

    CHANGE_NEGATIVE = [
        # CS3 Table 2 original
        "rigid", "traditional", "slow", "risk-averse",
        "change resistant", "old school",
        # Expanded — real employee language
        "inflexible", "set in their ways", "fear of change",
        "resistant to change", "doesn't adapt",
        "slow to adapt", "slow to change", "slow-moving",
        "old-fashioned", "stuck", "stagnant",
        "won't change", "refuses to change",
        "no room for growth", "no career growth",
        "same old", "nothing changes", "never changes",
        "afraid of change", "fear of failure",
        "complacent", "status quo",
    ]

    # Keywords requiring whole-word matching to avoid substring false positives
    WHOLE_WORD_KEYWORDS = [
        "ai", "ml", "nlp", "llm",
        "slow", "traditional", "rigid", "dynamic", "agile",
        "rpa", "bi tools", "gpu", "cuda",  "gpus", "hpc", "dgx",
    ]

    # Context exclusions — filter out non-culture uses of ambiguous keywords
    KEYWORD_CONTEXT_EXCLUSIONS = {
        "slow": [
            r"slow\s+climb",
            r"slow\s+(?:career|promotion|advancement|growth|process|hiring|recruiting|interview)",
            r"(?:career|promotion|advancement|growth|process|hiring|recruiting|interview)\s+(?:is|are|was|were|seems?|feels?)\s+slow",
            r"slow\s+(?:to\s+)?(?:promote|hire|respond|reply|get\s+back)",
        ],
        "traditional": [
            r"traditional\s+(?:benefits|hours|schedule|shift|role)",
        ],
        "politics": [
            r"(?:office|internal|team)\s+politics",
        ],
        "automation": [
            r"(?:test|testing)\s+automation",
            r"automation\s+(?:test|engineer|qa)",
        ],
    }

    INDEED_NOISE_INDICATORS = [
        "slide 1 of", "slide 2 of", "see more jobs",
        "selecting an option will update the page",
        "report review copy link", "show more report review",
        "page 1 of 3", "days ago slide", "an hour",
    ]
    INDEED_NOISE_THRESHOLD = 3
    MAX_REVIEW_TEXT_LENGTH = 2000

    # ──────────────────────────────────────────────────────────────
    # RATING BLEND PARAMETERS (Option 3)
    # ──────────────────────────────────────────────────────────────
    # KEYWORD_WEIGHT = Decimal("0.70")   # 70% keyword-based signal
    # RATING_WEIGHT = Decimal("0.30")    # 30% rating-based baseline

    # KEYWORD_WEIGHT = Decimal("0.55")   # 55% keyword-based signal
    # RATING_WEIGHT = Decimal("0.45")    # 45% rating-based baseline
    # KEYWORD_WEIGHT = Decimal("0.45")   # 45% keyword-based signal
    # RATING_WEIGHT  = Decimal("0.55")   # 55% rating-based baseline

    # KEYWORD_WEIGHT = Decimal("0.20")   # 40% keyword-based signal
    # RATING_WEIGHT  = Decimal("0.80")   # 60% rating-based baseline

    KEYWORD_WEIGHT = Decimal("0.19")   # 19% keyword-based signal
    RATING_WEIGHT  = Decimal("0.81")   # 81% rating-based baseline

    def __init__(self, cache_dir="data/culture_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._browser = None
        self._playwright = None

    def _run_timestamp(self) -> str:
        """Stable timestamp per execution (UTC ISO safe for S3)."""
        if not hasattr(self, "_run_ts"):
            self._run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        return self._run_ts

    # -----------------------------------------------------------------
    # Browser (Playwright) management
    # -----------------------------------------------------------------
    def _check_playwright(self) -> bool:
        """Return True if playwright + chromium are available, logging clearly if not."""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            logger.error(
                "Playwright is NOT installed in this environment. "
                "Indeed and CareerBliss scraping will be skipped. "
                "Run 'docker compose build --no-cache' to install it."
            )
            return False

    def _get_browser(self):
        if self._browser is None:
            if not self._check_playwright():
                raise RuntimeError("Playwright not installed — rebuild Docker image with 'docker compose build --no-cache'")
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                ],
            )
            logger.info("Playwright browser launched")
        return self._browser

    def _new_page(self, stealth=True):
        browser = self._get_browser()
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        page = ctx.new_page()
        if stealth:
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (p) =>
                    p.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : origQuery(p);
            """)
        page.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,webm}", lambda route: route.abort())
        return page

    def close_browser(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
            logger.info("Playwright browser closed")

    # -----------------------------------------------------------------
    # Keyword helpers
    # -----------------------------------------------------------------
    def _keyword_in_text(self, kw: str, text: str) -> bool:
        if kw in self.WHOLE_WORD_KEYWORDS:
            return bool(re.search(r"\b" + re.escape(kw) + r"\b", text))
        return kw in text

    def _keyword_in_context(self, kw: str, text: str) -> bool:
        if not self._keyword_in_text(kw, text):
            return False
        exclusions = self.KEYWORD_CONTEXT_EXCLUSIONS.get(kw)
        if not exclusions:
            return True
        for pattern in exclusions:
            if re.search(pattern, text, re.IGNORECASE):
                return False
        return True

    def _groq_ai_keywords(self, ticker: str, company_name: str) -> List[str]:
        """
        Call Groq to generate AI-awareness keywords specific to this company.

        Employees at different companies use different vocabulary to describe AI work.
        Netflix employees say "recommendation engine", "personalization", "content algorithm"
        rather than generic "machine learning". This method fetches those company-specific
        terms so reviews are scored accurately even when generic keywords don't match.

        Returns a deduplicated list of lowercase keyword strings (empty list on failure).
        """
        api_key = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
        if not api_key:
            logger.warning("[%s] GROQ_API_KEY not set — skipping AI keyword expansion", ticker)
            return []

        prompt = (
            f'For the company "{company_name}" (ticker: {ticker}), list 15-20 specific words or '
            f'short phrases that employees at this company would write in Glassdoor or Indeed reviews '
            f'to signal that the company uses AI, machine learning, data science, or intelligent '
            f'automation in its products or internal culture. '
            f'Focus on vocabulary specific to this company\'s industry and well-known products — '
            f'NOT generic AI terms like "artificial intelligence" or "machine learning" (those are '
            f'already covered). '
            f'For example, a streaming company might produce: '
            f'["recommendation engine", "personalization", "content algorithm", "a/b testing at scale", '
            f'"streaming analytics", "subscriber data", "taste preferences", "viewing history"]. '
            f'Return ONLY a JSON array of lowercase strings, no explanation.'
        )

        try:
            resp = httpx.post(
                _settings.GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an expert in company culture, employee reviews, and AI adoption. "
                                "Respond only with a valid JSON array of strings."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 400,
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            keywords = json.loads(raw)
            if isinstance(keywords, list):
                result = [str(k).lower().strip() for k in keywords if k]
                logger.info("[%s] Groq expanded AI keywords (%d terms): %s", ticker, len(result), result)
                return result
        except Exception as exc:
            logger.warning("[%s] Groq AI keyword expansion failed: %s", ticker, exc)
        return []

    def _groq_estimate_culture_scores(self, ticker: str, company_name: str) -> Optional[CultureSignal]:
        """
        When 0 reviews are collected (scraping blocked / API quota exhausted),
        ask Groq to estimate culture dimension scores from public knowledge.

        Returns a CultureSignal with Groq-estimated scores and review_count=0,
        or None if the API call fails so the caller can fall back to defaults.
        """
        api_key = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
        if not api_key:
            logger.warning("[%s] GROQ_API_KEY not set — cannot estimate culture scores", ticker)
            return None

        prompt = (
            f'Based on public knowledge about the company "{company_name}" (ticker: {ticker}), '
            f'estimate scores (0-100) for these culture dimensions as they would appear in '
            f'employee reviews on Glassdoor/Indeed:\n'
            f'- innovation_score: How innovative/cutting-edge is the culture? '
            f'(100=highly innovative startup-like, 0=bureaucratic/stagnant)\n'
            f'- data_driven_score: How data-driven/metrics-focused is the culture? '
            f'(100=everything measured, 0=no data culture)\n'
            f'- ai_awareness_score: How AI/ML-aware is the workforce and culture? '
            f'(100=AI-first company, 0=no AI usage)\n'
            f'- change_readiness_score: How change-ready/agile is the organization? '
            f'(100=highly agile, 0=rigid/resistant)\n'
            f'- avg_rating: Estimated Glassdoor avg star rating (1.0-5.0)\n\n'
            f'Base estimates on well-known public information: industry, products, '
            f'size, culture reputation, news coverage.\n'
            f'Return ONLY a JSON object like: '
            f'{{"innovation_score": 75, "data_driven_score": 80, "ai_awareness_score": 70, '
            f'"change_readiness_score": 65, "avg_rating": 3.9}}'
        )

        try:
            resp = httpx.post(
                _settings.GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a culture analyst with deep knowledge of public company "
                                "reputations. Respond only with a valid JSON object, no explanation."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 200,
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)

            inn  = Decimal(str(max(0.0, min(100.0, float(data.get("innovation_score", 50))))))
            dd   = Decimal(str(max(0.0, min(100.0, float(data.get("data_driven_score", 50))))))
            ai_s = Decimal(str(max(0.0, min(100.0, float(data.get("ai_awareness_score", 50))))))
            ch   = Decimal(str(max(0.0, min(100.0, float(data.get("change_readiness_score", 50))))))
            avg_r = Decimal(str(max(1.0, min(5.0, float(data.get("avg_rating", 3.5))))))

            overall = (
                Decimal("0.30") * inn
                + Decimal("0.25") * dd
                + Decimal("0.25") * ai_s
                + Decimal("0.20") * ch
            ).quantize(Decimal("0.01"))

            logger.info(
                "[%s] Groq culture estimate: inn=%.1f dd=%.1f ai=%.1f ch=%.1f overall=%.1f",
                ticker, float(inn), float(dd), float(ai_s), float(ch), float(overall),
            )

            return CultureSignal(
                company_id=ticker,
                ticker=ticker,
                innovation_score=inn.quantize(Decimal("0.01")),
                data_driven_score=dd.quantize(Decimal("0.01")),
                change_readiness_score=ch.quantize(Decimal("0.01")),
                ai_awareness_score=ai_s.quantize(Decimal("0.01")),
                overall_score=overall,
                review_count=0,
                avg_rating=avg_r.quantize(Decimal("0.01")),
                current_employee_ratio=Decimal("0.000"),
                confidence=Decimal("0.300"),   # low confidence — no real reviews
                source_breakdown={"groq_estimate": 1},
                positive_keywords_found=[],
                negative_keywords_found=[],
                scoring_method="groq_estimate",
            )

        except Exception as exc:
            logger.warning("[%s] Groq culture score estimation failed: %s", ticker, exc)
            return None

    def _is_indeed_page_dump(self, review: CultureReview) -> bool:
        text = f"{review.pros} {review.cons}".lower()
        noise_count = sum(1 for ind in self.INDEED_NOISE_INDICATORS if ind in text)
        if noise_count >= self.INDEED_NOISE_THRESHOLD:
            return True
        if len(text) > self.MAX_REVIEW_TEXT_LENGTH:
            date_pattern = re.findall(
                r"(?:january|february|march|april|may|june|july|august|"
                r"september|october|november|december)\s+\d{1,2},\s+\d{4}",
                text,
            )
            if len(date_pattern) >= 3:
                return True
        job_listing_signals = [
            r"\$\d+\s*-\s*\$\d+\s*an?\s*hour",
            r"\d+\s*days?\s*ago\s*slide",
            r"see more jobs",
        ]
        listing_count = sum(1 for p in job_listing_signals if re.search(p, text))
        if listing_count >= 2:
            return True
        return False

    def _deduplicate_reviews(self, reviews: List[CultureReview]) -> List[CultureReview]:
        seen = set()
        unique = []
        for r in reviews:
            content = f"{r.pros} {r.cons}".lower().strip()
            content = re.sub(r"\s+", " ", content)
            fingerprint = content[:150]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(r)
        removed = len(reviews) - len(unique)
        if removed > 0:
            logger.info(f"  Dedup removed {removed} duplicate reviews ({len(reviews)} -> {len(unique)})")
        return unique

    # -----------------------------------------------------------------
    # Glassdoor (RapidAPI)
    # -----------------------------------------------------------------
    def _get_api_key(self) -> str:
        key = os.getenv("RAPIDAPI_KEY", "")
        if not key:
            raise EnvironmentError("RAPIDAPI_KEY not set in .env")
        return key

    def _api_headers(self) -> Dict[str, str]:
        return {
            "x-rapidapi-key": self._get_api_key(),
            "x-rapidapi-host": self.RAPIDAPI_HOST,
        }

    def _resolve_glassdoor_id(self, ticker: str, timeout: float = 15.0) -> Optional[str]:
        """Use Groq LLM to resolve the correct Glassdoor company_id for the RapidAPI endpoint."""
        reg = COMPANY_REGISTRY.get(ticker, {})
        company_name = reg.get("name", ticker)

        api_key = _settings.GROQ_API_KEY.get_secret_value() if _settings.GROQ_API_KEY else ""
        if not api_key:
            return None

        prompt = (
            f'What is the exact Glassdoor company URL slug for "{company_name}" (ticker: {ticker})? '
            f'This is the string that appears in Glassdoor URLs like glassdoor.com/Reviews/SLUG-Reviews-EXXXXX.htm. '
            f'Examples: "NVIDIA" for NVIDIA, "JPMorgan-Chase" for JPMorgan Chase, "Walmart" for Walmart, '
            f'"Google" for Alphabet/Google, "Amazon" for Amazon, "Apple" for Apple. '
            f'Return ONLY the slug string, nothing else. No quotes, no explanation.'
        )

        try:
            resp = httpx.post(
                _settings.GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 50,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            slug = resp.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")

            # Sanity check: slug should be short, no spaces, no URLs
            if slug and len(slug) < 60 and " " not in slug and "/" not in slug:
                logger.info(f"[{ticker}] Glassdoor slug resolved via LLM: '{company_name}' → '{slug}'")
                return slug
            else:
                logger.warning(f"[{ticker}] LLM returned invalid Glassdoor slug: '{slug}'")
        except Exception as e:
            logger.warning(f"[{ticker}] Glassdoor slug LLM resolution failed: {e}")

        return None

    def fetch_glassdoor(self, ticker: str, max_pages: int, timeout: float = 30.0) -> List[CultureReview]:
        ticker = ticker.upper()
        reg = COMPANY_REGISTRY[ticker]

        # Try to resolve the correct Glassdoor company ID via search
        resolved_id = self._resolve_glassdoor_id(ticker, timeout=timeout)
        company_id = resolved_id or reg["glassdoor_id"]

        # Cache the resolved ID back so future calls don't search again
        if resolved_id and resolved_id != reg.get("glassdoor_id"):
            reg["glassdoor_id"] = resolved_id

        reviews: List[CultureReview] = []

        for page_num in range(1, max_pages + 1):
            params = {
                "company_id": company_id,
                "page": str(page_num),
                "sort": "POPULAR",
                "language": "en",
                "only_current_employees": "false",
                "extended_rating_data": "false",
                "domain": "www.glassdoor.com",
            }
            url = f"{self.RAPIDAPI_BASE}/company-reviews"
            logger.info(f"[{ticker}][glassdoor] Fetching page {page_num}...")

            try:
                resp = httpx.get(url, headers=self._api_headers(), params=params, timeout=timeout)
                resp.raise_for_status()
                raw_data = resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"[{ticker}][glassdoor] HTTP {e.response.status_code} on page {page_num} "
                    f"— body: {e.response.text[:500]}"
                )
                break
            except Exception as e:
                logger.error(f"[{ticker}][glassdoor] Request failed: {e}")
                break

            # Debug: log the top-level keys and structure on page 1 so we can
            # diagnose API response shape mismatches without guessing.
            if page_num == 1:
                top_keys = list(raw_data.keys()) if isinstance(raw_data, dict) else type(raw_data).__name__
                logger.info(f"[{ticker}][glassdoor] API response top-level keys: {top_keys}")
                # If no 'data' key, try common alternative structures
                if "data" not in raw_data:
                    logger.warning(
                        f"[{ticker}][glassdoor] Expected 'data' key not found. "
                        f"Raw response (first 500 chars): {str(raw_data)[:500]}"
                    )

            reviews_raw = raw_data.get("data", {}).get("reviews", [])
            # Fallback: some RapidAPI endpoints return reviews at the top level
            if not reviews_raw and isinstance(raw_data.get("reviews"), list):
                reviews_raw = raw_data["reviews"]
                logger.info(f"[{ticker}][glassdoor] Using top-level 'reviews' key (alternative response shape)")
            if not reviews_raw:
                logger.info(
                    f"[{ticker}][glassdoor] No reviews found at page {page_num}. "
                    f"API response snippet: {str(raw_data)[:300]}"
                )
                break

            for r in reviews_raw:
                parsed = self._parse_glassdoor_review(ticker, r)
                if parsed:
                    reviews.append(parsed)

            time.sleep(0.35)

        logger.info(f"[{ticker}][glassdoor] Total fetched: {len(reviews)}")
        return reviews

    def _parse_glassdoor_review(self, ticker: str, raw: Dict[str, Any]) -> Optional[CultureReview]:
        try:
            rid = f"glassdoor_{ticker}_{raw.get('review_id', 'unknown')}"
            rating = float(raw.get("rating", 3.0))
            title = raw.get("summary") or raw.get("title") or ""
            pros = raw.get("pros") or ""
            cons = raw.get("cons") or ""
            advice = raw.get("advice_to_management") or None
            job_title = raw.get("job_title") or ""
            is_current = bool(raw.get("is_current_employee", False))
            emp_status = raw.get("employment_status", "")
            if isinstance(emp_status, str) and emp_status.upper() == "REGULAR":
                is_current = True
            review_date = None
            raw_date = raw.get("review_datetime") or None
            if raw_date and isinstance(raw_date, str):
                review_date = _normalize_date(raw_date[:10])

            return CultureReview(
                review_id=rid,
                rating=min(5.0, max(1.0, rating)),
                title=title[:200],
                pros=pros[:2000],
                cons=cons[:2000],
                advice_to_management=advice,
                is_current_employee=is_current,
                job_title=job_title[:200],
                review_date=review_date,
                source="glassdoor",
            )
        except Exception as e:
            logger.warning(f"[{ticker}][glassdoor] Parse error: {e}")
            return None

    # -----------------------------------------------------------------
    # Indeed (Playwright + BeautifulSoup)
    # -----------------------------------------------------------------

    def scrape_indeed(self, ticker: str, max_pages: int = 25) -> list:
        ticker = ticker.upper()
        slugs = COMPANY_REGISTRY[ticker]["indeed_slugs"]
        reviews = []

        for slug in slugs:
            for page_num in range(max_pages):
                start = page_num * 20
                url = f"https://www.indeed.com/cmp/{slug}/reviews"
                if page_num > 0:
                    url = f"{url}?start={start}"

                logger.info(f"[{ticker}][indeed] Scraping page {page_num + 1}/{max_pages}: {url}")

                page = None
                try:
                    page = self._new_page(stealth=True)
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)

                    title = page.title().lower()
                    if any(w in title for w in ["blocked", "captcha", "access denied"]):
                        logger.warning(f"[{ticker}][indeed] Blocked: {page.title()}")
                        page.close()
                        break

                    try:
                        page.wait_for_selector(
                            '#cmp-container, [data-testid*="review"], .cmp-ReviewsList, '
                            '[data-tn-component="reviewsList"], div[itemprop="review"], '
                            'article[itemprop="review"], [data-testid="review-card"]',
                            timeout=15000,
                        )
                    except Exception:
                        logger.info(
                            f"[{ticker}][indeed] No known review container on page {page_num + 1} "
                            f"— attempting HTML parse anyway"
                        )

                    for _ in range(3):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(1)

                    html = page.content()
                    page.close()

                    soup = BeautifulSoup(html, "html.parser")

                    # Selector ladder: try progressively broader patterns
                    cards = soup.find_all("div", class_=re.compile(r"cmp-Review-container"))
                    if not cards:
                        cards = soup.find_all("div", attrs={"data-testid": re.compile(r"^review-\d+$")})
                    if not cards:
                        cards = soup.find_all(["div", "article"], attrs={"itemprop": "review"})
                    if not cards:
                        cards = soup.find_all("div", attrs={"data-tn-component": "review"})
                    if not cards:
                        cards = soup.find_all("div", attrs={"data-testid": "review-card"})
                    if not cards:
                        all_candidates = soup.find_all("div", class_=re.compile(r"cmp-Review(?!sList|s-|Rating)"))
                        cards = []
                        for c in all_candidates:
                            text = c.get_text(separator=" ", strip=True)
                            if "Job title Job titles" in text and "Sort Selecting an option" in text:
                                continue
                            if len(text) < 50:
                                continue
                            if c.find("div", class_=re.compile(r"cmp-Review-container")):
                                continue
                            cards.append(c)

                    if not cards:
                        # Log HTML snippet to help diagnose selector drift
                        snippet = html[:3000] if html else "(empty)"
                        logger.warning(
                            f"[{ticker}][indeed] No review cards found on page {page_num + 1} -> stopping. "
                            f"HTML snippet (first 3000 chars):\n{snippet}"
                        )
                        break

                    page_added = 0
                    for card in cards:
                        parsed = self._parse_indeed_card(card, ticker, len(reviews))
                        if parsed:
                            reviews.append(parsed)
                            page_added += 1

                    logger.info(f"[{ticker}][indeed] Page {page_num + 1}: +{page_added} (total {len(reviews)})")
                    time.sleep(1.5)

                except Exception as e:
                    logger.warning(f"[{ticker}][indeed] Error page {page_num + 1} for slug '{slug}': {e}")
                    try:
                        if page:
                            page.close()
                    except Exception:
                        pass
                    break

            if reviews:
                logger.info(f"[{ticker}][indeed] Extracted {len(reviews)} reviews total")
                break

        return reviews

    def _parse_indeed_card(self, card, ticker: str, index: int):
        text = card.get_text(separator=" ", strip=True)

        if "Selecting an option will update the page" in text:
            return None
        if "slide 1 of" in text.lower() or "see more jobs" in text.lower():
            job_listing_count = text.lower().count("slide") + text.lower().count("see more jobs")
            if job_listing_count >= 3:
                return None
        if len(text) < 60:
            return None

        # Rating
        rating = 3.0
        star_el = card.find(attrs={"aria-label": re.compile(r"(\d+\.?\d*)\s*out\s*of\s*5\s*star", re.I)})
        if star_el:
            m = re.search(r"(\d+\.?\d*)", star_el.get("aria-label", ""))
            if m:
                rating = float(m.group(1))
        else:
            rating_el = card.find(class_=re.compile(r"cmp-ReviewRating-text|ReviewRating"))
            if rating_el:
                m = re.search(r"(\d+\.?\d*)", rating_el.get_text())
                if m:
                    rating = float(m.group(1))
            else:
                star_el2 = card.find(attrs={"aria-label": re.compile(r"\d.*star", re.I)})
                if star_el2:
                    m = re.search(r"(\d+\.?\d*)", star_el2.get("aria-label", ""))
                    if m:
                        rating = float(m.group(1))

        # Title
        title_text = ""
        title_el = card.find(class_=re.compile(r"cmp-Review-title|Review-title"))
        if title_el:
            title_text = title_el.get_text(strip=True)
        else:
            title_el = card.find(["h2", "h3"])
            if title_el:
                title_text = title_el.get_text(strip=True)
        if not title_text:
            title_text = text[:100]

        # Pros and Cons
        pros_text = ""
        cons_text = ""

        for label_tag in card.find_all(["span", "div", "dt", "strong", "b"]):
            label_content = label_tag.get_text(strip=True).lower()
            if label_content in ("pros", "pro"):
                next_el = label_tag.find_next_sibling()
                if next_el:
                    pros_text = next_el.get_text(separator=" ", strip=True)
                elif label_tag.parent:
                    next_parent_sib = label_tag.parent.find_next_sibling()
                    if next_parent_sib:
                        pros_text = next_parent_sib.get_text(separator=" ", strip=True)
            elif label_content in ("cons", "con"):
                next_el = label_tag.find_next_sibling()
                if next_el:
                    cons_text = next_el.get_text(separator=" ", strip=True)
                elif label_tag.parent:
                    next_parent_sib = label_tag.parent.find_next_sibling()
                    if next_parent_sib:
                        cons_text = next_parent_sib.get_text(separator=" ", strip=True)

        if not pros_text and not cons_text:
            for dt in card.find_all("dt"):
                dt_text = dt.get_text(strip=True).lower()
                dd = dt.find_next_sibling("dd")
                if dd:
                    if "pro" in dt_text:
                        pros_text = dd.get_text(separator=" ", strip=True)
                    elif "con" in dt_text:
                        cons_text = dd.get_text(separator=" ", strip=True)

        if not pros_text and not cons_text:
            qa_blocks = card.find_all(["div", "p", "span"])
            for i, block in enumerate(qa_blocks):
                block_text = block.get_text(strip=True)
                if "best part of working" in block_text.lower():
                    answer = block_text
                    parts = re.split(r"What is the (?:best|most)", answer, flags=re.I)
                    if len(parts) > 1:
                        for part in parts[1:]:
                            cleaned = re.sub(r"^.*?\?", "", part).strip()
                            if cleaned and len(cleaned) > 10:
                                pros_text = cleaned
                                break
                elif "most stressful" in block_text.lower() or "hardest part" in block_text.lower():
                    answer = block_text
                    parts = re.split(r"What is the (?:most|hardest)", answer, flags=re.I)
                    if len(parts) > 1:
                        for part in parts[1:]:
                            cleaned = re.sub(r"^.*?\?", "", part).strip()
                            if cleaned and len(cleaned) > 10:
                                cons_text = cleaned
                                break

        if not pros_text and not cons_text:
            review_text_el = card.find(class_=re.compile(r"cmp-Review-text|Review-text"))
            if review_text_el:
                pros_text = review_text_el.get_text(separator=" ", strip=True)

        if not pros_text and not cons_text:
            clean_text = text
            if title_text and title_text in clean_text:
                clean_text = clean_text.replace(title_text, "", 1).strip()
            for noise in ["Report review Copy link", "Show more", "Report review"]:
                clean_text = clean_text.replace(noise, "").strip()
            if len(clean_text) > 30:
                pros_text = clean_text

        # Employee Status
        is_current = False
        author_el = card.find(class_=re.compile(r"cmp-Review-author|author|employee", re.I))
        if author_el:
            author_text = author_el.get_text(strip=True).lower()
            if "current" in author_text:
                is_current = True
        else:
            text_lower = text.lower()
            if "current employee" in text_lower:
                is_current = True
            elif "former employee" not in text_lower:
                if "i currently work" in text_lower or "i work here" in text_lower:
                    is_current = True

        # Job Title
        job_title = ""
        job_el = card.find(class_=re.compile(r"cmp-Review-author.*title|job.?title|position", re.I))
        if job_el:
            job_title = job_el.get_text(strip=True)

        # Review Date
        review_date = None
        date_el = card.find("time")
        if date_el:
            raw_d = date_el.get("datetime") or date_el.get("content") or date_el.get_text(strip=True)
            review_date = _normalize_date(raw_d)
        if not review_date:
            date_el2 = card.find(class_=re.compile(r"date|timestamp", re.I))
            if date_el2:
                raw_d = date_el2.get_text(strip=True)
                review_date = _normalize_date(raw_d)
        if not review_date:
            date_match = re.search(
                r"((?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{1,2},\s+\d{4})",
                text
            )
            if date_match:
                review_date = _normalize_date(date_match.group(1))

        # Clean noise
        if pros_text:
            for noise in ["Report review Copy link", "Show more", "... Show more", "Report review", "Copy link"]:
                pros_text = pros_text.replace(noise, "").strip()
            pros_text = re.sub(
                r"^(?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{1,2},\s+\d{4}\s*",
                "", pros_text
            ).strip()
        if cons_text:
            for noise in ["Report review Copy link", "Show more", "Report review", "Copy link"]:
                cons_text = cons_text.replace(noise, "").strip()

        if not pros_text and not cons_text:
            return None
        if len((pros_text or "") + (cons_text or "")) < 20:
            return None

        return CultureReview(
            review_id=f"indeed_{ticker}_{index}",
            rating=min(5.0, max(1.0, rating)),
            title=title_text[:200],
            pros=pros_text[:2000],
            cons=cons_text[:2000],
            is_current_employee=is_current,
            job_title=job_title[:200],
            review_date=review_date,
            source="indeed",
        )

    # -----------------------------------------------------------------
    # CareerBliss
    # -----------------------------------------------------------------
    def scrape_careerbliss(self, ticker: str, max_clicks: int = 15) -> List[CultureReview]:
        from bs4 import BeautifulSoup

        ticker = ticker.upper()
        slug = COMPANY_REGISTRY[ticker]["careerbliss_slug"]
        reviews: List[CultureReview] = []
        dq = chr(34)

        url = f"https://www.careerbliss.com/{slug}/reviews/"
        logger.info(f"[{ticker}][careerbliss] Scraping: {url}")

        page = None
        try:
            page = self._new_page(stealth=True)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            title_text = page.title().lower()
            if any(w in title_text for w in ["blocked", "denied", "captcha"]):
                logger.warning(f"[{ticker}][careerbliss] Blocked: {page.title()}")
                page.close()
                return reviews

            for _ in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)

            for i in range(max_clicks):
                try:
                    more = page.query_selector(
                        'a:has-text("More Reviews"), a:has-text("Show More"), '
                        'button:has-text("More"), a.next'
                    )
                    if more and more.is_visible():
                        more.click()
                        logger.info(f"[{ticker}][careerbliss] Clicked more ({i+1}/{max_clicks})")
                        time.sleep(2)
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(1)
                    else:
                        break
                except Exception:
                    break

            html = page.content()
            page.close()

            soup = BeautifulSoup(html, "html.parser")
            cards = (
                soup.find_all("div", class_=re.compile(r"review", re.I))
                or soup.find_all("li", class_=re.compile(r"review", re.I))
                or soup.find_all("article")
            )

            seen = set()
            for i, card in enumerate(cards):
                text = card.get_text(separator=" ", strip=True)
                if len(text) < 30:
                    continue

                key = text[:80].lower()
                if key in seen:
                    continue
                seen.add(key)

                if any(bp in text.lower() for bp in [
                    "careerbliss", "share salary", "update your browser",
                    "search by job title", "browse salaries",
                ]):
                    continue

                review_text = ""
                for s in card.stripped_strings:
                    if s.startswith(dq) and s.endswith(dq) and len(s) > 30:
                        review_text = s.strip(dq)
                        break
                if not review_text:
                    for el in card.find_all(["p", "span", "div"]):
                        t = el.get_text(separator=" ", strip=True)
                        if len(t) > len(review_text) and len(t) > 30:
                            review_text = t
                if not review_text or len(review_text) < 20:
                    review_text = text

                rating = 3.0
                rating_el = card.find(attrs={"aria-label": re.compile(r"\d.*star", re.I)})
                if rating_el:
                    m = re.search(r"(\d+\.?\d*)", rating_el.get("aria-label", ""))
                    if m:
                        rating = float(m.group(1))
                else:
                    rating_match = re.search(r"(\d+\.?\d*)\s*(?:/|out of)\s*5", text)
                    if rating_match:
                        rating = float(rating_match.group(1))

                job_title = ""
                job_el = card.find(class_=re.compile(r"job.?title|position|role", re.I))
                if job_el:
                    job_title = job_el.get_text(strip=True)

                review_date = None
                date_el = card.find("time") or card.find(class_=re.compile(r"date", re.I))
                if date_el:
                    raw_d = date_el.get("datetime") or date_el.get("content") or date_el.get_text(strip=True)
                    review_date = _normalize_date(raw_d)

                reviews.append(
                    CultureReview(
                        review_id=f"careerbliss_{ticker}_{i}",
                        rating=min(5.0, max(1.0, rating)),
                        title=review_text[:100],
                        pros=review_text[:2000],
                        cons="",
                        is_current_employee=True,
                        job_title=job_title[:100],
                        review_date=review_date,
                        source="careerbliss",
                    )
                )

            logger.info(f"[{ticker}][careerbliss] Extracted {len(reviews)} reviews")

        except Exception as e:
            logger.warning(f"[{ticker}][careerbliss] Error: {e}")
            try:
                if page:
                    page.close()
            except Exception:
                pass

        return reviews

    # -----------------------------------------------------------------
    # Caching
    # -----------------------------------------------------------------
    def _cache_path(self, ticker: str, source: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}_{source}.json"

    def _save_cache(self, ticker: str, source: str, reviews: List[CultureReview]) -> None:
        p = self._cache_path(ticker, source)
        try:
            data = []
            for r in reviews:
                data.append({
                    "review_id": r.review_id,
                    "rating": r.rating,
                    "title": r.title,
                    "pros": r.pros,
                    "cons": r.cons,
                    "advice_to_management": r.advice_to_management,
                    "is_current_employee": r.is_current_employee,
                    "job_title": r.job_title,
                    "review_date": r.review_date.isoformat() if r.review_date else None,
                    "source": r.source,
                })
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[{ticker}][{source}] Cached {len(reviews)} reviews -> {p}")
        except Exception as e:
            logger.warning(f"[{ticker}][{source}] Cache save failed: {e}")

    def _load_cache(self, ticker: str, source: str) -> Optional[List[CultureReview]]:
        p = self._cache_path(ticker, source)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            reviews: List[CultureReview] = []
            for d in data:
                rd = None
                if d.get("review_date"):
                    try:
                        rd = datetime.fromisoformat(d["review_date"])
                    except (ValueError, TypeError):
                        rd = None
                reviews.append(
                    CultureReview(
                        review_id=d["review_id"],
                        rating=d["rating"],
                        title=d["title"],
                        pros=d["pros"],
                        cons=d["cons"],
                        advice_to_management=d.get("advice_to_management"),
                        is_current_employee=d.get("is_current_employee", True),
                        job_title=d.get("job_title", ""),
                        review_date=rd,
                        source=d.get("source", source),
                    )
                )
            logger.info(f"[{ticker}][{source}] Loaded {len(reviews)} from cache")
            return reviews
        except Exception as e:
            logger.warning(f"[{ticker}][{source}] Cache load failed: {e}")
            return None

    # -----------------------------------------------------------------
    # Multi-source fetch
    # -----------------------------------------------------------------
    def fetch_all_reviews(
        self,
        ticker: str,
        sources: List[str],
        max_pages_glassdoor: Optional[int] = None,
        max_pages_indeed: Optional[int] = None,
        max_clicks_careerbliss: Optional[int] = None,
        use_cache: bool = True,
    ) -> List[CultureReview]:
        ticker = ticker.upper()

        max_pages_glassdoor = max_pages_glassdoor or self.DEFAULT_MAX_GLASSDOOR_PAGES
        max_pages_indeed = max_pages_indeed or self.DEFAULT_MAX_INDEED_PAGES
        max_clicks_careerbliss = max_clicks_careerbliss or self.DEFAULT_MAX_CAREERBLISS_CLICKS

        all_reviews: List[CultureReview] = []

        for source in sources:
            if use_cache:
                cached = self._load_cache(ticker, source)
                if cached is not None:
                    all_reviews.extend(cached)
                    continue

            revs: List[CultureReview] = []
            try:
                if source == "glassdoor":
                    revs = self.fetch_glassdoor(ticker, max_pages=max_pages_glassdoor)
                elif source == "indeed":
                    if not self._check_playwright():
                        logger.error(
                            f"[{ticker}][indeed] SKIPPED — Playwright not installed. "
                            f"Rebuild Docker: 'docker compose build --no-cache'"
                        )
                        continue
                    revs = self.scrape_indeed(ticker, max_pages=max_pages_indeed)
                elif source == "careerbliss":
                    if not self._check_playwright():
                        logger.error(
                            f"[{ticker}][careerbliss] SKIPPED — Playwright not installed. "
                            f"Rebuild Docker: 'docker compose build --no-cache'"
                        )
                        continue
                    revs = self.scrape_careerbliss(ticker, max_clicks=max_clicks_careerbliss)
                else:
                    logger.warning(f"[{ticker}] Unknown source: {source}")
                    continue
            except Exception as e:
                logger.error(f"[{ticker}][{source}] FAILED: {e}", exc_info=True)

            if revs:
                self._save_cache(ticker, source, revs)
                all_reviews.extend(revs)

        if self.MAX_REVIEWS_TOTAL is not None and len(all_reviews) > self.MAX_REVIEWS_TOTAL:
            all_reviews = all_reviews[: self.MAX_REVIEWS_TOTAL]

        logger.info(f"[{ticker}] Total reviews collected: {len(all_reviews)}")
        return all_reviews

    # -----------------------------------------------------------------
    # Scoring — OPTION 3: Expanded Keywords + 70/30 Rating Blend
    # -----------------------------------------------------------------
    def analyze_reviews(self, company_id: str, ticker: str, reviews: List[CultureReview]) -> CultureSignal:
        """
        Analyze reviews for culture indicators.

        Option 3 scoring approach:
        1. Compute keyword-based scores per CS3 formula
        2. Compute rating-based baseline (avg rating → 0-100 scale)
        3. Blend: 70% keyword + 30% rating for each component
        4. Overall = 0.30*innovation + 0.25*data_driven + 0.25*ai_awareness + 0.20*change

        Rationale: Employee reviews rarely contain explicit technical
        keywords even at top tech companies. Blending with aggregate
        satisfaction ratings provides a more robust culture proxy that
        differentiates companies while preserving keyword signal.
        """
        if not reviews:
            logger.warning(f"[{ticker}] No reviews to analyze — attempting Groq culture estimate")
            company_name = COMPANY_REGISTRY.get(ticker, {}).get("name", ticker)
            groq_signal = self._groq_estimate_culture_scores(ticker, company_name)
            if groq_signal:
                return groq_signal
            logger.warning(f"[{ticker}] Groq estimate also failed — returning hardcoded defaults")
            return CultureSignal(company_id=company_id, ticker=ticker)

        original_count = len(reviews)
        reviews = self._deduplicate_reviews(reviews)

        page_dump_ids = set()
        for r in reviews:
            if r.source == "indeed" and self._is_indeed_page_dump(r):
                page_dump_ids.add(r.review_id)

        if page_dump_ids:
            logger.info(f"[{ticker}] Detected {len(page_dump_ids)} Indeed page-dump reviews (excluded)")

        reviews = [r for r in reviews if r.review_id not in page_dump_ids]

        logger.info(f"[{ticker}] Reviews after cleaning: {len(reviews)} (original {original_count})")

        if not reviews:
            logger.warning(f"[{ticker}] No reviews remaining after cleaning — attempting Groq culture estimate")
            company_name = COMPANY_REGISTRY.get(ticker, {}).get("name", ticker)
            groq_signal = self._groq_estimate_culture_scores(ticker, company_name)
            if groq_signal:
                return groq_signal
            return CultureSignal(company_id=company_id, ticker=ticker)

        # ── Pre-Phase: Groq company-specific keyword expansion ────
        # Expand all 6 culture keyword groups once per (ticker, dimension)
        # so company-specific vocabulary (e.g. "content innovation" for Netflix,
        # "disruption" for NVIDIA) is captured before scoring begins.
        company_name = COMPANY_REGISTRY.get(ticker, {}).get("name", ticker)
        try:
            from app.services.groq_enrichment import get_dimension_keywords

            def _expand(base_list: list, dimension: str) -> list:
                return get_dimension_keywords(ticker, company_name, dimension, base_list)

            inn_pos_kws  = _expand(list(self.INNOVATION_POSITIVE),  "innovation_positive_culture")
            inn_neg_kws  = _expand(list(self.INNOVATION_NEGATIVE),  "innovation_negative_culture")
            dd_kws       = _expand(list(self.DATA_DRIVEN_KEYWORDS), "data_driven_culture")
            ai_kws       = _expand(list(self.AI_AWARENESS_KEYWORDS),"ai_awareness_culture")
            ch_pos_kws   = _expand(list(self.CHANGE_POSITIVE),      "change_positive_culture")
            ch_neg_kws   = _expand(list(self.CHANGE_NEGATIVE),      "change_negative_culture")
            logger.info(
                "[%s] Groq-expanded culture kws: inn_pos=%d inn_neg=%d dd=%d ai=%d ch_pos=%d ch_neg=%d",
                ticker,
                len(inn_pos_kws), len(inn_neg_kws), len(dd_kws),
                len(ai_kws), len(ch_pos_kws), len(ch_neg_kws),
            )
        except Exception as exc:
            logger.warning("[%s] Groq culture keyword expansion failed: %s — using base keywords", ticker, exc)
            inn_pos_kws  = list(self.INNOVATION_POSITIVE)
            inn_neg_kws  = list(self.INNOVATION_NEGATIVE)
            dd_kws       = list(self.DATA_DRIVEN_KEYWORDS)
            ai_kws       = list(self.AI_AWARENESS_KEYWORDS)
            ch_pos_kws   = list(self.CHANGE_POSITIVE)
            ch_neg_kws   = list(self.CHANGE_NEGATIVE)

        # ── Phase 1: Keyword counting ────────────────────────────
        inn_pos = inn_neg = Decimal("0")
        dd = ai_m = Decimal("0")
        ch_pos = ch_neg = Decimal("0")
        total_w = Decimal("0")
        rating_sum = 0.0
        current_count = 0
        pos_kw: List[str] = []
        neg_kw: List[str] = []
        src_counts: Dict[str, int] = {}
        now = datetime.now(timezone.utc)

        for idx, r in enumerate(reviews):
            text = f"{r.pros} {r.cons}".lower()
            if r.advice_to_management:
                text += f" {r.advice_to_management}".lower()
            job_title_lower = r.job_title.lower() if r.job_title else ""

            days_old = (now - r.review_date).days if r.review_date else -1
            rec_w = Decimal("1.0") if days_old < 730 else Decimal("0.5")
            emp_w = Decimal("1.2") if r.is_current_employee else Decimal("1.0")
            src_w = self.SOURCE_RELIABILITY.get(r.source, Decimal("0.70"))
            w = rec_w * emp_w * src_w
            total_w += w

            rating_sum += r.rating
            if r.is_current_employee:
                current_count += 1
            src_counts[r.source] = src_counts.get(r.source, 0) + 1

            # Innovation positive
            inn_pos_hit = False
            for kw in inn_pos_kws:
                if self._keyword_in_text(kw, text):
                    if kw not in pos_kw:
                        pos_kw.append(kw)
                    inn_pos_hit = True
            if inn_pos_hit:
                inn_pos += w

            # Innovation negative
            inn_neg_hit = False
            for kw in inn_neg_kws:
                if self._keyword_in_context(kw, text):
                    if kw not in neg_kw:
                        neg_kw.append(kw)
                    inn_neg_hit = True
            if inn_neg_hit:
                inn_neg += w

            # Data-driven
            dd_hit = False
            for kw in dd_kws:
                if self._keyword_in_text(kw, text):
                    dd_hit = True
            if dd_hit:
                dd += w

            # AI awareness
            ai_hit = False
            for kw in ai_kws:
                if self._keyword_in_context(kw, text):
                    ai_hit = True
                elif self._keyword_in_text(kw, job_title_lower):
                    ai_hit = True
            if ai_hit:
                ai_m += w

            # Change positive
            ch_pos_hit = False
            for kw in ch_pos_kws:
                if self._keyword_in_text(kw, text):
                    if kw not in pos_kw:
                        pos_kw.append(kw)
                    ch_pos_hit = True
            if ch_pos_hit:
                ch_pos += w

            # Change negative
            ch_neg_hit = False
            for kw in ch_neg_kws:
                if self._keyword_in_context(kw, text):
                    if kw not in neg_kw:
                        neg_kw.append(kw)
                    ch_neg_hit = True
            if ch_neg_hit:
                ch_neg += w

            if idx < 3:
                logger.debug(f"[{ticker}] sample review weight={w} source={r.source} current={r.is_current_employee}")

        # ── Phase 2: Keyword-based scores (CS3 formula) ──────────
        if total_w > 0:
            kw_inn = (inn_pos - inn_neg) / total_w * 50 + 50
            kw_dd  = dd / total_w * 100
            kw_ai  = ai_m / total_w * 100
            kw_ch  = (ch_pos - ch_neg) / total_w * 50 + 50
        else:
            kw_inn = Decimal("50")
            kw_dd  = Decimal("0")
            kw_ai  = Decimal("0")
            kw_ch  = Decimal("50")

        # ── Phase 3: Rating-based baseline ───────────────────────
        # Scale avg rating (1-5) to 0-100:
        #   1.0 → 0,  2.0 → 25,  3.0 → 50,  4.0 → 75,  5.0 → 100
        avg_rating_val = rating_sum / len(reviews) if reviews else 3.0
        rating_score = Decimal(str(
            max(0.0, min(100.0, (avg_rating_val - 1.0) / 4.0 * 100.0))
        ))

        # For AI awareness baseline, scale differently:
        # Only high-rated tech/finance companies likely have AI culture
        # Use a dampened version: rating 4.0+ → modest AI baseline
        # ai_rating_baseline = Decimal(str(
        #     max(0.0, min(60.0, (avg_rating_val - 2.5) / 2.5 * 40.0))
        # ))
        # ai_rating_baseline = Decimal(str(
        #     max(0.0, min(80.0, (avg_rating_val - 2.0) / 3.0 * 60.0))
        # ))

        # ai_rating_baseline = Decimal(str(
        #     max(0.0, min(80.0, (avg_rating_val - 2.0) / 3.0 * 80.0))
        # ))

        ai_rating_baseline = Decimal(str(
        max(0.0, min(100.0, (avg_rating_val - 1.5) / 3.0 * 100.0))
))

        # ── Groq fallback: expand AI keywords if none matched ────
        # When kw_ai == 0, no generic AI keywords hit any review.
        # This is common for companies like Netflix where employees write
        # "recommendation engine" or "personalization" rather than "machine learning".
        # We ask Groq for company-specific AI vocabulary and re-scan the reviews.
        if kw_ai == Decimal("0") and total_w > 0:
            groq_kws = self._groq_ai_keywords(ticker, company_name)
            if groq_kws:
                ai_m_groq = Decimal("0")
                for r in reviews:
                    r_text = f"{r.pros} {r.cons}".lower()
                    if r.advice_to_management:
                        r_text += f" {r.advice_to_management}".lower()
                    r_job = r.job_title.lower() if r.job_title else ""
                    days_old = (now - r.review_date).days if r.review_date else -1
                    rec_w = Decimal("1.0") if days_old < 730 else Decimal("0.5")
                    emp_w = Decimal("1.2") if r.is_current_employee else Decimal("1.0")
                    src_w = self.SOURCE_RELIABILITY.get(r.source, Decimal("0.70"))
                    r_w = rec_w * emp_w * src_w
                    hit = any(kw in r_text or kw in r_job for kw in groq_kws)
                    if hit:
                        ai_m_groq += r_w
                kw_ai = ai_m_groq / total_w * 100
                logger.info(
                    "[%s] Groq-expanded AI keyword score: kw_ai=%.2f (from %d extra terms)",
                    ticker, float(kw_ai), len(groq_kws),
                )

        # ── Phase 4: Blend 70% keyword + 30% rating ─────────────
        KW = self.KEYWORD_WEIGHT    # 0.70
        RT = self.RATING_WEIGHT     # 0.30

        inn_s = KW * kw_inn + RT * rating_score
        # dd_s  = KW * kw_dd  + RT * rating_score * Decimal("0.6")   # Dampened — rating is partial proxy for data culture
        # dd_s  = KW * kw_dd  + RT * rating_score * Decimal("0.9")   # Dampened — rating is partial proxy for data culture
        dd_s  = KW * kw_dd  + RT * rating_score * Decimal("1.0")   # was 0.9
        ai_s  = KW * kw_ai  + RT * ai_rating_baseline              # Use AI-specific baseline
        ch_s  = KW * kw_ch  + RT * rating_score

        # ── Clamp to [0, 100] ────────────────────────────────────
        clamp = lambda v: max(Decimal("0"), min(Decimal("100"), v))
        inn_s, dd_s, ai_s, ch_s = clamp(inn_s), clamp(dd_s), clamp(ai_s), clamp(ch_s)

        # ── Overall (CS3 weights) ────────────────────────────────
        overall = (
            Decimal("0.30") * inn_s
            + Decimal("0.25") * dd_s
            + Decimal("0.25") * ai_s
            + Decimal("0.20") * ch_s
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # ── Confidence ───────────────────────────────────────────
        conf = min(Decimal("0.5") + Decimal(str(len(reviews))) / 200, Decimal("0.90"))
        source_bonus = min(Decimal(str(len(src_counts))) * Decimal("0.03"), Decimal("0.10"))
        conf = min(conf + source_bonus, Decimal("0.95"))

        avg_rating = Decimal(str(round(rating_sum / len(reviews), 2)))
        current_ratio = Decimal(str(round(current_count / len(reviews), 3)))

        # ── Logging ──────────────────────────────────────────────
        logger.info(f"[{ticker}] Reviews analyzed={len(reviews)} sources={src_counts} total_w={total_w}")
        logger.info(f"[{ticker}] Avg rating: {avg_rating_val:.2f} → rating_score={float(rating_score):.1f}, ai_baseline={float(ai_rating_baseline):.1f}")
        logger.info(f"[{ticker}] Keyword scores: inn={float(kw_inn):.2f} dd={float(kw_dd):.2f} ai={float(kw_ai):.2f} ch={float(kw_ch):.2f}")
        logger.info(f"[{ticker}] Blended scores: inn={float(inn_s):.2f} dd={float(dd_s):.2f} ai={float(ai_s):.2f} ch={float(ch_s):.2f}")
        logger.info(f"[{ticker}] Overall: {overall}")

        return CultureSignal(
            company_id=company_id,
            ticker=ticker,
            innovation_score=inn_s.quantize(Decimal("0.01")),
            data_driven_score=dd_s.quantize(Decimal("0.01")),
            change_readiness_score=ch_s.quantize(Decimal("0.01")),
            ai_awareness_score=ai_s.quantize(Decimal("0.01")),
            overall_score=overall,
            review_count=len(reviews),
            avg_rating=avg_rating,
            current_employee_ratio=current_ratio,
            confidence=conf.quantize(Decimal("0.001")),
            source_breakdown=src_counts,
            positive_keywords_found=pos_kw,
            negative_keywords_found=neg_kw,
            scoring_method="keyword_blend",
        )

    # -----------------------------------------------------------------
    # S3 upload
    # -----------------------------------------------------------------
    def _get_s3_service(self):
        if not hasattr(self, "_s3_client"):
            try:
                from app.services.s3_storage import get_s3_service
                svc = get_s3_service()
                self._s3_client = svc.s3_client
                self._s3_bucket = svc.bucket_name
                logger.info(f"S3 initialized: bucket={self._s3_bucket}")
            except Exception as e:
                logger.error(f"S3 initialization failed: {e}")
                self._s3_client = None
                self._s3_bucket = None
        return self._s3_client

    def _decimal_to_float(self, obj):
        """Recursively convert Decimals to floats for JSON serialization."""
        if isinstance(obj, dict):
            return {k: self._decimal_to_float(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._decimal_to_float(v) for v in obj]
        elif isinstance(obj, Decimal):
            return float(obj)
        return obj

    def _upload_raw_to_s3(self, ticker: str, reviews: List[CultureReview]):
        client = self._get_s3_service()
        if not client:
            return None

        ticker = ticker.upper()
        ts = self._run_timestamp()

        raw_data = []
        for r in reviews:
            raw_data.append({
                "ticker": ticker,
                "source": r.source,
                "review_id": r.review_id,
                "rating": r.rating,
                "title": r.title,
                "pros": r.pros,
                "cons": r.cons,
                "advice_to_management": r.advice_to_management,
                "is_current_employee": r.is_current_employee,
                "job_title": r.job_title,
                "review_date": r.review_date.isoformat() if r.review_date else None,
                "collected_at": ts,
                "snapshot_id": f"{ticker}_{ts}"
            })

        s3_key = f"glassdoor_signals/raw/{ticker}/{ts}_raw.json"

        payload = json.dumps({
            "snapshot_id": f"{ticker}_{ts}",
            "ticker": ticker,
            "collected_at": ts,
            "review_count": len(raw_data),
            "reviews": raw_data
        }, indent=2, default=str)

        try:
            client.put_object(
                Bucket=self._s3_bucket,
                Key=s3_key,
                Body=payload.encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"[{ticker}] Uploaded {len(raw_data)} raw reviews to S3: {s3_key}")
            return s3_key
        except Exception as e:
            logger.error(f"[{ticker}] S3 raw upload failed: {e}")
            return None

    def _upload_output_to_s3(self, signal: CultureSignal):
        client = self._get_s3_service()
        if not client:
            return None

        ticker = signal.ticker.upper()
        output_data = self._decimal_to_float(asdict(signal))
        ts = self._run_timestamp()
        output_data["run_timestamp"] = ts

        s3_key = f"glassdoor_signals/output/{ticker}/{ts}_culture.json"
        payload = json.dumps(output_data, indent=2, default=str)

        try:
            client.put_object(
                Bucket=self._s3_bucket,
                Key=s3_key,
                Body=payload.encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"[{ticker}] Uploaded culture signal to S3: {s3_key}")
            return s3_key
        except Exception as e:
            logger.error(f"[{ticker}] S3 output upload failed: {e}")
            return None

    # -----------------------------------------------------------------
    # Entry points
    # -----------------------------------------------------------------
    def collect_and_analyze(
        self,
        ticker: str,
        sources: Optional[List[str]] = None,
        use_cache: bool = True,
        gd_pages: Optional[int] = None,
        indeed_pages: Optional[int] = None,
        cb_clicks: Optional[int] = None,
    ) -> CultureSignal:
        ticker = validate_ticker(ticker)
        if sources is None:
            sources = ["glassdoor", "indeed", "careerbliss"]
        sources = [s for s in sources if s in VALID_SOURCES]

        reg = COMPANY_REGISTRY[ticker]
        logger.info(f"{'=' * 55}")
        logger.info(f"CULTURE COLLECTION: {ticker} ({reg['name']})")
        logger.info(f"   Sector:  {reg['sector']}")
        logger.info(f"   Sources: {', '.join(sources)}")
        logger.info(f"   Depth:   gd_pages={gd_pages or self.DEFAULT_MAX_GLASSDOOR_PAGES}, "
                    f"indeed_pages={indeed_pages or self.DEFAULT_MAX_INDEED_PAGES}, "
                    f"cb_clicks={cb_clicks or self.DEFAULT_MAX_CAREERBLISS_CLICKS}")
        logger.info(f"{'=' * 55}")

        reviews = self.fetch_all_reviews(
            ticker,
            sources=sources,
            max_pages_glassdoor=gd_pages,
            max_pages_indeed=indeed_pages,
            max_clicks_careerbliss=cb_clicks,
            use_cache=use_cache,
        )

        signal = self.analyze_reviews(ticker, ticker, reviews)

        self._upload_raw_to_s3(ticker, reviews)
        self._upload_output_to_s3(signal)

        return signal

    def collect_multiple(
        self,
        tickers: List[str],
        sources: Optional[List[str]] = None,
        use_cache: bool = True,
        gd_pages: Optional[int] = None,
        indeed_pages: Optional[int] = None,
        cb_clicks: Optional[int] = None,
        delay: float = 2.0,
    ) -> Dict[str, CultureSignal]:
        results: Dict[str, CultureSignal] = {}
        try:
            for i, ticker in enumerate(tickers):
                try:
                    signal = self.collect_and_analyze(
                        ticker,
                        sources=sources,
                        use_cache=use_cache,
                        gd_pages=gd_pages,
                        indeed_pages=indeed_pages,
                        cb_clicks=cb_clicks,
                    )
                    results[ticker.upper()] = signal
                except Exception as e:
                    logger.error(f"[{ticker}] FAILED: {e}")
                if i < len(tickers) - 1:
                    logger.info(f"Waiting {delay}s before next ticker...")
                    time.sleep(delay)
        finally:
            self.close_browser()
        return results


# =====================================================================
# DISPLAY
# =====================================================================

def print_signal(signal: CultureSignal):
    reg = COMPANY_REGISTRY.get(signal.ticker, {})
    name = reg.get("name", signal.ticker)
    sector = reg.get("sector", "")
    logger.info("=" * 60)
    logger.info("  CULTURE ANALYSIS -- %s (%s)", signal.ticker, name)
    if sector:
        logger.info("  Sector: %s", sector)
    logger.info("=" * 60)
    logger.info("  Overall Score:          %s/100", signal.overall_score)
    logger.info("  Confidence:             %s", signal.confidence)
    logger.info("  Reviews Analyzed:       %s", signal.review_count)
    logger.info("  Source Breakdown:       %s", signal.source_breakdown)
    logger.info("  Avg Rating:             %s/5.0", signal.avg_rating)
    logger.info("  Current Employee Ratio: %s", signal.current_employee_ratio)
    logger.info("  Component Scores:       Weight   Score")
    logger.info("    Innovation:           0.30   %8s", signal.innovation_score)
    logger.info("    Data-Driven:          0.25   %8s", signal.data_driven_score)
    logger.info("    AI Awareness:         0.25   %8s", signal.ai_awareness_score)
    logger.info("    Change Readiness:     0.20   %8s", signal.change_readiness_score)
    if signal.positive_keywords_found:
        logger.info("  (+) Keywords: %s", ', '.join(signal.positive_keywords_found[:10]))
    if signal.negative_keywords_found:
        logger.info("  (-) Keywords: %s", ', '.join(signal.negative_keywords_found[:10]))


# =====================================================================
# MAIN / CLI
# =====================================================================

def _parse_int_flag(args: List[str], prefix: str) -> Optional[int]:
    for a in args:
        if a.startswith(prefix):
            try:
                return int(a.split("=", 1)[1])
            except Exception:
                raise ValueError(f"Bad flag {a}. Expected {prefix}<int>")
    return None


def main():
    args = sys.argv[1:]
    use_cache = "--no-cache" not in args

    sources: Optional[List[str]] = None
    for a in args:
        if a.startswith("--sources="):
            sources = [s.strip() for s in a.split("=", 1)[1].split(",") if s.strip()]

    gd_pages = _parse_int_flag(args, "--gd-pages=")
    indeed_pages = _parse_int_flag(args, "--indeed-pages=")
    cb_clicks = _parse_int_flag(args, "--cb-clicks=")

    tickers = []
    for a in args:
        if a.startswith("--"):
            continue
        if a.startswith("-"):
            continue
        tickers.append(a)

    if "--all" in args:
        tickers = all_tickers()
    elif not tickers:
        print("\n" + "=" * 58)
        print("  Multi-Source Culture Collector (CS3)")
        print("=" * 58)
        print()
        print("Usage:")
        print("  python app/pipelines/glassdoor_collector.py NVDA JPM")
        print("  python app/pipelines/glassdoor_collector.py --all")
        print()
        print("Options:")
        print("  --no-cache")
        print("  --sources=glassdoor,indeed,careerbliss")
        print("  --gd-pages=30         (RapidAPI usage guardrail)")
        print("  --indeed-pages=25     (scrape depth guardrail)")
        print("  --cb-clicks=15        (More Reviews clicks)")
        print()
        print("Allowed tickers:")
        for t in sorted(ALLOWED_TICKERS):
            reg = COMPANY_REGISTRY[t]
            print(f"  {t:<6} {reg['name']:<30} {reg['sector']}")
        print()
        return

    tickers = [validate_ticker(t) for t in tickers]

    collector = CultureCollector()
    results = collector.collect_multiple(
        tickers,
        sources=sources,
        use_cache=use_cache,
        gd_pages=gd_pages,
        indeed_pages=indeed_pages,
        cb_clicks=cb_clicks,
        delay=2.0,
    )

    print("\n\n" + "#" * 60)
    print(f"#  MULTI-SOURCE CULTURE ANALYSIS -- {len(results)} companies")
    print("#" * 60)

    for _, signal in results.items():
        print_signal(signal)

    if len(results) > 1:
        print(f"\n{'=' * 62}")
        print(f"  {'Ticker':<6} {'Overall':>8} {'Innov':>7} {'Data':>7} {'AI':>7} {'Change':>7} {'#Rev':>5} {'Conf':>6}")
        print(f"  {'-'*6} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*5} {'-'*6}")
        for t, s in sorted(results.items(), key=lambda x: x[1].overall_score, reverse=True):
            print(f"  {t:<6} {s.overall_score:>8} {s.innovation_score:>7} {s.data_driven_score:>7} {s.ai_awareness_score:>7} {s.change_readiness_score:>7} {s.review_count:>5} {s.confidence:>6}")
        print(f"{'=' * 62}")


if __name__ == "__main__":
    main()