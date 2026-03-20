"""
Pipeline Client — streamlit/utils/pipeline_client.py

Step order (9 steps):
  1. Company Setup          POST /api/v1/companies
  2. SEC Filings            POST /api/v1/documents/collect
  3. Parse Documents        POST /api/v1/documents/parse/{ticker}       NON-FATAL
  4. Chunk Documents        POST /api/v1/documents/chunk/{ticker}       NON-FATAL
  5. Signal Scoring         POST /api/v1/signals/collect + poll          ALL 6 CATEGORIES
  6. Glassdoor Culture      (included in Step 5)
  7. Board Governance       (included in Step 5)
  8. Scoring                POST /api/v1/scoring/{ticker}               FATAL
  9. Index Evidence         POST /rag/index/{ticker}?force=true         NON-FATAL

Step 5 uses POST /api/v1/signals/collect which runs all 6 signal categories
(hiring, digital, patents, leadership, board, culture) as a background task,
then polls /signals/tasks/{task_id} until complete.

WEBSITE FIX: resolved.website is now threaded through to the digital_presence
signal endpoint so BuiltWith/Wappalyzer use the real yfinance-resolved domain
instead of a hardcoded or derived fallback.

BUG FIXES (v2):
  - BUG #1 FIXED: _step_parse skip condition used raw_count==0 which is ALWAYS
    true after collection (docs move to parsed/chunked status). Now skips only
    when parsed+chunked >= total.
  - BUG #2 FIXED: _step_chunk Re-run button silently skipped because chunked>0
    and parsed==0 and raw==0 was always true for already-chunked companies.
    Added force=True param; _run_single_step passes force=True when re-running.
  - BUG #3 FIXED: _get_doc_status used data.get("document_summary", data) which
    fell back to the entire response dict when document_summary was absent,
    causing field lookups to silently return 0.
  - BUG #4 FIXED: _get_completed_steps hit /evidence endpoint 3 separate times
    (steps 4, 5, 6) causing 9 redundant Snowflake connections per page render.
    Now fetched once and reused.
"""
from __future__ import annotations

import os
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timezone, datetime
from typing import Optional, Dict, Any, Callable, List

logger = logging.getLogger(__name__)

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
REQUEST_TIMEOUT_SHORT = 30
REQUEST_TIMEOUT_LONG  = 600   # signals/score/all takes 5+ min (jobs + patents + leadership)


@dataclass
class SignalFlag:
    """
    LLM sanity-check result for a single signal score.
    Populated after Step 5 completes — surfaced in Streamlit as a ⚠️ warning.
    Never blocks the pipeline or auto-corrects scores.
    """
    category: str                         # e.g. "digital_presence"
    score: float                          # the score being questioned
    plausible: bool                       # LLM verdict
    reason: str                           # short explanation
    severity: str                         # "low" | "medium" | "high"
    raw_value: Optional[str] = None       # signal's raw_value for context


@dataclass
class PipelineStepResult:
    step: int
    name: str
    status: str                           # "success", "error", "skipped"
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_seconds: float = 0.0
    signal_flags: List = field(default_factory=list)
    # signal_flags: List[SignalFlag] — populated on Step 5 only


@dataclass
class PipelineResult:
    ticker: str
    overall_status: str                   # "success", "partial", "failed"
    steps: List[PipelineStepResult] = field(default_factory=list)
    org_air_score: Optional[float] = None
    indexed_count: int = 0
    error: Optional[str] = None
    signal_flags: List = field(default_factory=list)
    # signal_flags: List[SignalFlag] — rolled up from Step 5, read directly by Streamlit


class PipelineClient:

    def __init__(self, base_url: str = API_BASE):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Health / status ───────────────────────────────────────────────────────

    def is_backend_alive(self) -> bool:
        try:
            resp = self._session.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_company_status(self, ticker: str) -> Dict[str, Any]:
        status = {
            "chatbot_ready": False,
            "org_air_score": None,
            "indexed_documents": 0,
            "has_scores": False,
            "has_documents": False,
        }
        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/rag/debug",
                params={"ticker": ticker, "limit": 5},
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code == 200:
                data = resp.json()
                ticker_count = data.get("by_ticker", {}).get(ticker, 0)
                total = data.get("total", 0)
                if ticker_count > 0:
                    status["indexed_documents"] = ticker_count
                    status["chatbot_ready"] = True
                elif total > 0:
                    try:
                        check = self._session.get(
                            f"{self.base_url}/api/v1/rag/chatbot/{ticker}",
                            params={"question": "test"},
                            timeout=10,
                        )
                        if check.status_code == 200:
                            result = check.json()
                            if result.get("sources_used", 0) > 0:
                                status["chatbot_ready"] = True
                                status["indexed_documents"] = result["sources_used"]
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/scoring/{ticker}/dimensions",
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code == 200:
                dim_scores = resp.json().get("scores", [])
                if dim_scores:
                    status["has_scores"] = True
                    avg = sum(d["score"] for d in dim_scores) / len(dim_scores)
                    status["org_air_score"] = round(avg, 1)
        except Exception:
            pass

        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/companies/{ticker}/evidence",
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code == 200:
                doc_summary = resp.json().get("document_summary", {})
                if doc_summary.get("total_documents", 0) > 0:
                    status["has_documents"] = True
        except Exception:
            pass

        return status

    # ── Company ID lookup ─────────────────────────────────────────────────────

    def _get_company_id(self, ticker: str) -> Optional[str]:
        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/companies/{ticker}",
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("id") or data.get("company_id") or data.get("uuid")
        except Exception:
            pass
        return None

    # ── Document status pre-check ─────────────────────────────────────────────

    def _get_doc_status(self, ticker: str) -> Dict[str, Any]:
        """
        Query how many documents are raw / parsed / chunked for this ticker.
        Falls back to all-zeros on any error so the caller always gets a dict.

        BUG #3 FIX: Previously used data.get("document_summary", data) which
        fell back to the entire response dict when the key was absent. This caused
        all field lookups (total_documents, by_status, etc.) to silently return 0,
        making skip conditions unreliable. Now extracts document_summary safely
        and falls back to top-level fields explicitly.
        """
        empty = {
            "total_documents": 0, "parsed_count": 0,
            "chunked_count": 0,   "raw_count": 0,
            "by_status": {},
        }
        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/companies/{ticker}/evidence",
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code != 200:
                return empty

            data = resp.json()

            # BUG #3 FIX: Don't fall back to the entire response dict.
            # Extract document_summary safely; if absent, try top-level fields.
            summary = data.get("document_summary") or {}

            total = int(
                summary.get("total_documents")
                or data.get("total_documents")
                or data.get("document_count")
                or 0
            )

            by_status = summary.get("by_status") or data.get("by_status") or {}

            chunked = int(by_status.get("chunked", 0))
            parsed  = int(by_status.get("parsed", 0))
            raw     = int(
                by_status.get("raw", 0)
                or by_status.get("collected", 0)
                or by_status.get("pending", 0)
            )

            return {
                "total_documents": total,
                "parsed_count":    parsed,
                "chunked_count":   chunked,
                "raw_count":       raw,
                "by_status":       by_status,
            }
        except Exception:
            return empty

    # ── Step 1 — Company Setup ────────────────────────────────────────────────

    def _step_create_company(self, resolved) -> PipelineStepResult:
        start = time.time()
        payload = {
            "name": resolved.name,
            "ticker": resolved.ticker,
            "industry_id": resolved.industry_id,
            "position_factor": resolved.position_factor,
        }
        if resolved.sector:             payload["sector"] = resolved.sector
        if resolved.revenue_millions:   payload["revenue_millions"] = resolved.revenue_millions
        if resolved.employee_count:     payload["employee_count"] = resolved.employee_count

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/companies",
                json=payload, timeout=REQUEST_TIMEOUT_SHORT,
            )
            duration = time.time() - start
            if resp.status_code == 409:
                company_id = self._get_company_id(resolved.ticker)
                return PipelineStepResult(
                    step=1, name="Company Setup", status="skipped",
                    message=f"{resolved.name} ({resolved.ticker}) already exists in platform",
                    data={"ticker": resolved.ticker, "company_id": company_id},
                    duration_seconds=duration,
                )
            resp.raise_for_status()
            return PipelineStepResult(
                step=1, name="Company Setup", status="success",
                message=f"Created {resolved.name} ({resolved.ticker})",
                data=resp.json(), duration_seconds=duration,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=1, name="Company Setup", status="error",
                message="Failed to create company in platform",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=1, name="Company Setup", status="error",
                message="Failed to create company in platform",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Step 2 — SEC Filings ──────────────────────────────────────────────────

    def _step_collect_sec(
        self,
        ticker: str,
        cik: Optional[str],
        on_substep: Optional[Callable[[str], None]] = None,
    ) -> PipelineStepResult:
        start = time.time()
        import datetime as _dt
        filing_types = ["10-K", "8-K", "DEF 14A"]

        if on_substep:
            yr = _dt.datetime.now().year
            for ft in filing_types:
                for y in [yr, yr - 1]:
                    on_substep(f"Querying {ft} ({y})...")

        payload: Dict[str, Any] = {
            "ticker": ticker, "filing_types": filing_types, "lookback_days": 730,
        }
        if cik:
            payload["cik"] = cik

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/documents/collect",
                json=payload, timeout=REQUEST_TIMEOUT_LONG,
            )
            resp.raise_for_status()
            data = resp.json()
            doc_count = data.get("collected_count", data.get("documents_found", data.get("count", "?")))
            return PipelineStepResult(
                step=2, name="SEC Filings", status="success",
                message=f"Collected {doc_count} filings (10-K, 8-K, DEF 14A — past 2 years)",
                data=data, duration_seconds=time.time() - start,
            )
        except requests.Timeout:
            return PipelineStepResult(
                step=2, name="SEC Filings", status="error",
                message="SEC collection timed out — EDGAR may be slow. Try re-running.",
                error="Request timed out.", duration_seconds=time.time() - start,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=2, name="SEC Filings", status="error",
                message="SEC filing collection failed",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=2, name="SEC Filings", status="error",
                message="SEC filing collection failed",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Signal status helpers ─────────────────────────────────────────────────

    def _get_signal_scores_today(self, ticker: str) -> Dict[str, Any]:
        """
        Return per-signal status for today from /api/v1/companies/{ticker}/evidence.

        Skip rule per signal:
          scored_today=True AND score > 0  →  should_skip=True
          score == 0 OR None               →  should_skip=False (re-run it)
          not scored today                 →  should_skip=False (re-run it)
        """
        SIGNAL_SCORE_KEYS = {
            "technology_hiring":   "technology_hiring_score",
            "digital_presence":    "digital_presence_score",
            "innovation_activity": "innovation_activity_score",
            "leadership_signals":  "leadership_signals_score",
        }
        default = {
            cat: {"score": None, "raw_value": None, "scored_today": False, "should_skip": False}
            for cat in SIGNAL_SCORE_KEYS
        }

        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/companies/{ticker}/evidence",
                timeout=REQUEST_TIMEOUT_SHORT,
            )
            if resp.status_code != 200:
                return default

            data    = resp.json()
            summary = data.get("signal_summary", {})
            signals = data.get("signals", [])
            if not summary:
                return default

            last_updated = summary.get("last_updated", "")
            today = datetime.now(timezone.utc).date()
            scored_today = False
            try:
                if last_updated:
                    updated_dt   = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                    scored_today = (updated_dt.date() == today)
            except (ValueError, AttributeError):
                pass

            raw_by_cat: Dict[str, str] = {}
            for sig in signals:
                cat = sig.get("category", "")
                if cat in SIGNAL_SCORE_KEYS and cat not in raw_by_cat:
                    raw_by_cat[cat] = sig.get("raw_value", "")

            result = {}
            for cat, summary_key in SIGNAL_SCORE_KEYS.items():
                score = summary.get(summary_key)
                is_zero_or_missing = (score is None or score == 0)
                result[cat] = {
                    "score":        score,
                    "raw_value":    raw_by_cat.get(cat),
                    "scored_today": scored_today,
                    "should_skip":  scored_today and not is_zero_or_missing,
                }

            return result

        except Exception:
            return default

    def _sanity_check_scores(
        self,
        ticker: str,
        company_name: str,
        signal_results: Dict[str, Any],
    ) -> List[SignalFlag]:
        """
        LLM sanity check — ask Groq whether each signal score is plausible
        for this specific company. Never raises — returns [] on any failure.
        """
        try:
            import httpx
            _use_httpx = True
        except ImportError:
            _use_httpx = False

        import json

        groq_api_key = os.getenv("GROQ_API_KEY", "")
        if not groq_api_key:
            logger.warning("GROQ_API_KEY not set — skipping score sanity check")
            return []

        flags: List[SignalFlag] = []

        CATEGORY_LABELS = {
            "technology_hiring":   "Technology Hiring (job postings for AI/ML roles)",
            "digital_presence":    "Digital Presence (tech stack sophistication)",
            "innovation_activity": "Innovation Activity (AI patent portfolio)",
            "leadership_signals":  "Leadership Signals (DEF 14A executive/board tech profile)",
        }

        for cat, info in signal_results.items():
            score     = info.get("score")
            raw_value = info.get("raw_value", "")

            if score is None or score == 0:
                continue

            label = CATEGORY_LABELS.get(cat, cat)
            prompt = f"""You are a PE analyst reviewing an AI readiness signal score.

Company: {company_name} ({ticker})
Signal: {label}
Score: {score}/100
Raw evidence: {raw_value or "not available"}

Does this score seem plausible for {company_name}?
Consider the company's industry, size, and general reputation.

Reply ONLY with valid JSON (no markdown, no explanation outside JSON):
{{"plausible": true/false, "reason": "one sentence explanation", "severity": "low/medium/high"}}

severity guide:
- low: mildly surprising but defensible
- medium: noticeably off, warrants review
- high: clearly wrong, likely a data or scoring error"""

            try:
                payload = {
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 120,
                }
                headers = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                }
                if _use_httpx:
                    import httpx
                    resp = httpx.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers, json=payload, timeout=15.0,
                    )
                    resp.raise_for_status()
                    raw_json = resp.json()
                else:
                    s = requests.Session()
                    resp = s.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers, json=payload, timeout=15.0,
                    )
                    resp.raise_for_status()
                    raw_json = resp.json()
                    s.close()

                content = raw_json["choices"][0]["message"]["content"].strip()
                content = content.replace("```json", "").replace("```", "").strip()
                parsed  = json.loads(content)

                plausible = bool(parsed.get("plausible", True))
                reason    = str(parsed.get("reason", ""))
                severity  = str(parsed.get("severity", "low"))

                if not plausible:
                    flags.append(SignalFlag(
                        category=cat,
                        score=score,
                        plausible=False,
                        reason=reason,
                        severity=severity,
                        raw_value=raw_value,
                    ))
                    logger.warning(
                        f"[{ticker}] Score sanity flag — {cat}: {score}/100 | "
                        f"severity={severity} | {reason}"
                    )

            except Exception as e:
                logger.debug(f"[{ticker}] Sanity check skipped for {cat}: {e}")
                continue

        return flags

    # ── Step 5 — Signal Scoring ───────────────────────────────────────────────

    def _poll_signal_task(self, task_id: str, timeout: int = REQUEST_TIMEOUT_LONG) -> Dict[str, Any]:
        """Poll /signals/tasks/{task_id} until completed or timeout."""
        deadline = time.time() + timeout
        poll_interval = 3
        while time.time() < deadline:
            try:
                resp = self._session.get(
                    f"{self.base_url}/api/v1/signals/tasks/{task_id}",
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "")
                    if status in ("completed", "completed_with_errors", "failed"):
                        return data
            except Exception as e:
                logger.warning(f"Poll failed for task {task_id}: {e}")
            time.sleep(poll_interval)
        return {"status": "timeout", "error": "Signal collection timed out"}

    def _step_signal_scoring(
        self,
        ticker: str,
        company_name: str = "",
        on_substep: Optional[Callable[[str], None]] = None,
        skip_if_scored_today: bool = True,
        website: Optional[str] = None,
    ) -> PipelineStepResult:
        """
        Trigger all signal collection via POST /api/v1/signals/collect and poll for completion.

        All 6 categories (hiring, digital, patents, leadership, board, culture)
        run as a single background task on the server.
        """
        start = time.time()

        ALL_CATEGORIES = [
            "technology_hiring", "digital_presence", "innovation_activity",
            "leadership_signals", "board_composition", "culture",
        ]

        # Submit collection request
        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/signals/collect",
                json={
                    "company_id": ticker,
                    "categories": ALL_CATEGORIES,
                    "force_refresh": not skip_if_scored_today,
                },
                timeout=30,
            )
            resp.raise_for_status()
            task_id = resp.json().get("task_id")
        except Exception as e:
            return PipelineStepResult(
                step=5, name="Signal Scoring", status="error",
                message=f"Failed to start signal collection: {e}",
                error=str(e), duration_seconds=time.time() - start,
            )

        if on_substep:
            on_substep(f"Signal collection queued (task: {task_id}), polling...")

        # Poll until done
        task_result = self._poll_signal_task(task_id)
        task_status = task_result.get("status", "unknown")
        signals = task_result.get("result", {}).get("signals", {})
        errors = task_result.get("result", {}).get("errors", [])

        # Build summary
        parts = []
        merged: Dict[str, Any] = {}
        for cat in ALL_CATEGORIES:
            sig = signals.get(cat, {})
            score = sig.get("score")
            status = sig.get("status", "missing")
            merged[cat] = {
                "score": score,
                "raw_value": sig.get("details", {}).get("raw_value") if sig.get("details") else None,
                "source": status,
            }
            label = cat.replace("_", " ")
            if status == "success" and score is not None:
                parts.append(f"{label}: {score:.1f}")
            elif status == "failed":
                parts.append(f"{label}: FAILED")
            else:
                parts.append(f"{label}: --")

        summary_msg = " | ".join(parts) if parts else "signals processed"

        flags = self._sanity_check_scores(ticker, company_name or ticker, merged)
        if flags:
            flag_summary = ", ".join(
                f"{f.category} ({f.score}/100, severity={f.severity})" for f in flags
            )
            logger.warning(f"[{ticker}] Score flags: {flag_summary}")
            summary_msg += f" | ⚠️ {len(flags)} score(s) flagged"

        failed = [cat for cat, info in merged.items() if info["source"] == "failed"]
        if failed:
            logger.warning(f"[{ticker}] Signals failed: {failed}")

        overall_status = "success" if task_status in ("completed",) else "error" if task_status == "failed" else "success"

        return PipelineStepResult(
            step=5, name="Signal Scoring", status=overall_status,
            message=summary_msg,
            data={"signal_results": merged, "failed": failed, "task_id": task_id},
            duration_seconds=time.time() - start,
            signal_flags=flags,
        )

    # ── Step 3 — Parse Documents (NON-FATAL) ──────────────────────────────────

    def _step_parse(self, ticker: str, doc_status: Optional[Dict] = None, force: bool = False) -> PipelineStepResult:
        """
        BUG #1 FIX: The original skip condition was `if total > 0 and raw == 0`.
        raw_count is ALWAYS 0 after collection because documents immediately
        transition from "raw" → "parsed" or "chunked" status in Snowflake.
        This meant parse was permanently skipped on every re-run.

        Fix: skip only when parsed+chunked >= total (all docs genuinely processed).
        Also added force=True to bypass skip when user explicitly clicks Re-run.
        """
        start = time.time()

        if doc_status and not force:
            total   = doc_status.get("total_documents", 0)
            parsed  = doc_status.get("parsed_count", 0)
            chunked = doc_status.get("chunked_count", 0)
            already_done = parsed + chunked
            # Skip only when ALL documents have already been parsed or chunked.
            # Do NOT use raw_count==0 — it is always 0 after collection.
            if total > 0 and already_done >= total:
                return PipelineStepResult(
                    step=3, name="Parse Documents", status="skipped",
                    message=f"All {already_done} of {total} documents already parsed or chunked — skipping",
                    data=doc_status, duration_seconds=time.time() - start,
                )

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/documents/parse/{ticker}",
                timeout=REQUEST_TIMEOUT_LONG,
            )
            resp.raise_for_status()
            data = resp.json()
            parsed = (
                data.get("parsed_count") or data.get("total_parsed")
                or data.get("documents_parsed") or data.get("count", "?")
            )
            skipped = data.get("skipped_count", data.get("skipped", 0))
            msg = f"Parsed {parsed} documents"
            if skipped:
                msg += f" ({skipped} already parsed, skipped)"
            return PipelineStepResult(
                step=3, name="Parse Documents", status="success",
                message=msg, data=data, duration_seconds=time.time() - start,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=3, name="Parse Documents", status="error",
                message="Parsing failed — leadership & SEC section scores may be missing",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=3, name="Parse Documents", status="error",
                message="Parsing failed — leadership & SEC section scores may be missing",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Step 4 — Chunk Documents (NON-FATAL) ──────────────────────────────────

    def _step_chunk(self, ticker: str, doc_status: Optional[Dict] = None, force: bool = False) -> PipelineStepResult:
        """
        BUG #2 FIX: The Re-run button in the UI silently skipped chunking because
        the skip condition `chunked > 0 and parsed == 0 and raw == 0` is always
        true for a company whose docs were previously chunked — regardless of
        whether the user explicitly wants to re-chunk.

        Fix: added force=True param. When force=True, skip logic is bypassed
        entirely. _run_single_step passes force=True when the step was already done.
        For full pipeline runs (force=False), skip is still applied correctly.
        """
        start = time.time()

        if doc_status and not force:
            total   = doc_status.get("total_documents", 0)
            chunked = doc_status.get("chunked_count", 0)
            parsed  = doc_status.get("parsed_count", 0)
            raw     = doc_status.get("raw_count", 0)
            # Skip only when all docs are chunked AND none remain in parsed/raw state.
            if total > 0 and chunked >= total and parsed == 0 and raw == 0:
                return PipelineStepResult(
                    step=4, name="Chunk Documents", status="skipped",
                    message=f"All {chunked} of {total} documents already chunked — skipping",
                    data=doc_status, duration_seconds=time.time() - start,
                )

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/documents/chunk/{ticker}",
                timeout=REQUEST_TIMEOUT_LONG,
            )
            resp.raise_for_status()
            data = resp.json()
            chunks = (
                data.get("chunk_count") or data.get("total_chunks")
                or data.get("chunks_created") or data.get("count", "?")
            )
            return PipelineStepResult(
                step=4, name="Chunk Documents", status="success",
                message=f"Created {chunks} chunks — ready for RAG indexing",
                data=data, duration_seconds=time.time() - start,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=4, name="Chunk Documents", status="error",
                message="Chunking failed — RAG chatbot will have limited SEC coverage",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=4, name="Chunk Documents", status="error",
                message="Chunking failed — RAG chatbot will have limited SEC coverage",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Step 6 — Glassdoor Culture (handled by unified /signals/collect) ─────

    def _step_glassdoor(self, ticker: str) -> PipelineStepResult:
        """No-op: culture signals are now collected in Step 5 via /signals/collect."""
        return PipelineStepResult(
            step=6, name="Glassdoor Culture", status="success",
            message="Included in unified signal collection (Step 5)",
            duration_seconds=0.0,
        )

    # ── Step 7 — Board Governance (handled by unified /signals/collect) ──────

    def _step_board_governance(self, ticker: str) -> PipelineStepResult:
        """No-op: board governance signals are now collected in Step 5 via /signals/collect."""
        return PipelineStepResult(
            step=7, name="Board Governance", status="success",
            message="Included in unified signal collection (Step 5)",
            duration_seconds=0.0,
        )

    # ── Step 8 — CS3 Scoring ──────────────────────────────────────────────────

    def _step_score(self, ticker: str) -> PipelineStepResult:
        start = time.time()
        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/scoring/{ticker}",
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            orgair = data.get("orgair_score", data.get("org_air_score", "?"))
            vr = data.get("vr_score", "?")
            hr = data.get("hr_score", "?")
            return PipelineStepResult(
                step=8, name="Scoring", status="success",
                message=f"Org-AI-R: {orgair} | VR: {vr} | HR: {hr}",
                data=data, duration_seconds=time.time() - start,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=8, name="Scoring", status="error",
                message="Scoring failed",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=8, name="Scoring", status="error",
                message="Scoring failed",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Step 9 — Index Evidence ───────────────────────────────────────────────

    def _step_index(self, ticker: str, force: bool = True) -> PipelineStepResult:
        start = time.time()
        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/rag/index/{ticker}",
                params={"force": str(force).lower()},
                timeout=REQUEST_TIMEOUT_LONG,
            )
            resp.raise_for_status()
            data = resp.json()
            indexed = data.get("indexed_count", "?")
            return PipelineStepResult(
                step=9, name="Index Evidence", status="success",
                message=f"{indexed} evidence vectors indexed — chatbot ready",
                data=data, duration_seconds=time.time() - start,
            )
        except requests.HTTPError as e:
            return PipelineStepResult(
                step=9, name="Index Evidence", status="error",
                message="Evidence indexing failed",
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                duration_seconds=time.time() - start,
            )
        except Exception as e:
            return PipelineStepResult(
                step=9, name="Index Evidence", status="error",
                message="Evidence indexing failed",
                error=str(e), duration_seconds=time.time() - start,
            )

    # ── Full pipeline orchestration ───────────────────────────────────────────

    def run_pipeline(
        self,
        resolved,
        on_step_start: Optional[Callable[[str], None]] = None,
        on_step_complete: Optional[Callable[[PipelineStepResult], None]] = None,
        on_substep: Optional[Callable[[str, str], None]] = None,
        force_reindex: bool = False,
        force_rescore: bool = False,
    ) -> PipelineResult:
        ticker = resolved.ticker
        website: Optional[str] = getattr(resolved, "website", None)
        company_name: str = getattr(resolved, "name", ticker)

        steps: List[PipelineStepResult] = []

        def substep(step_name: str, msg: str) -> None:
            if on_substep:
                on_substep(step_name, msg)

        def run_step(step_fn) -> PipelineStepResult:
            result = step_fn()
            steps.append(result)
            if on_step_complete:
                on_step_complete(result)
            return result

        # Step 1 — Company Setup (FATAL on error)
        if on_step_start: on_step_start("Company Setup")
        s1 = run_step(lambda: self._step_create_company(resolved))
        if s1.status == "error":
            return self._build_result(ticker, steps)

        company_id: Optional[str] = (
            s1.data.get("id") or s1.data.get("company_id") or s1.data.get("uuid")
        )
        if not company_id:
            company_id = self._get_company_id(ticker)

        # Step 2 — SEC Filings (FATAL on error)
        if on_step_start: on_step_start("SEC Filings")
        s2 = run_step(lambda: self._step_collect_sec(
            ticker, resolved.cik,
            on_substep=lambda msg: substep("SEC Filings", msg),
        ))
        if s2.status == "error":
            return self._build_result(ticker, steps)

        # Step 3 — Parse Documents (NON-FATAL)
        # Fetch fresh doc status AFTER collection for accurate counts.
        # force=False: skip if already parsed (correct for full pipeline runs).
        if on_step_start: on_step_start("Parse Documents")
        doc_status_pre = self._get_doc_status(ticker)
        s3 = run_step(lambda: self._step_parse(ticker, doc_status=doc_status_pre, force=False))
        if s3.status == "error":
            logger.warning("[%s] Parse failed — continuing.", ticker)

        # Step 4 — Chunk Documents (NON-FATAL)
        # Fetch fresh doc status AFTER parse for accurate parsed_count.
        # force=False: skip if already chunked (correct for full pipeline runs).
        if on_step_start: on_step_start("Chunk Documents")
        doc_status_post_parse = self._get_doc_status(ticker)
        s4 = run_step(lambda: self._step_chunk(ticker, doc_status=doc_status_post_parse, force=False))
        if s4.status == "error":
            logger.warning("[%s] Chunk failed — continuing.", ticker)

        # ── Steps 5 + 6 + 7 — CONCURRENT ─────────────────────────────────────
        if on_step_start:
            on_step_start("Signal Scoring")
            on_step_start("Glassdoor Culture")
            on_step_start("Board Governance")

        _concurrent_results: Dict[str, PipelineStepResult] = {}

        def _run_signal():
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            self_copy = PipelineClient(self.base_url)
            self_copy._session = session
            return "signal", self_copy._step_signal_scoring(
                ticker,
                company_name=company_name,
                on_substep=None,   # Cannot call Streamlit UI from background thread
                skip_if_scored_today=not force_rescore,
                website=website,
            )

        def _run_glassdoor():
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            self_copy = PipelineClient(self.base_url)
            self_copy._session = session
            return "glassdoor", self_copy._step_glassdoor(ticker)

        def _run_board():
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            self_copy = PipelineClient(self.base_url)
            self_copy._session = session
            return "board", self_copy._step_board_governance(ticker)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_run_signal):    "signal",
                pool.submit(_run_glassdoor): "glassdoor",
                pool.submit(_run_board):     "board",
            }
            for future in as_completed(futures):
                try:
                    key, result = future.result()
                    _concurrent_results[key] = result
                except Exception as exc:
                    import traceback
                    key = futures[future]
                    logger.error(
                        f"[{ticker}] Concurrent step '{key}' raised: {exc}\n"
                        f"{traceback.format_exc()}"
                    )
                    step_map = {
                        "signal":    (5, "Signal Scoring"),
                        "glassdoor": (6, "Glassdoor Culture"),
                        "board":     (7, "Board Governance"),
                    }
                    snum, sname = step_map.get(key, (0, key))
                    _concurrent_results[key] = PipelineStepResult(
                        step=snum, name=sname, status="error",
                        message=f"{sname} raised an unexpected exception",
                        error=f"{type(exc).__name__}: {exc}",
                    )

        s5 = _concurrent_results["signal"]
        s6 = _concurrent_results["glassdoor"]
        s7 = _concurrent_results["board"]

        steps.append(s5)
        if on_step_complete: on_step_complete(s5)

        steps.append(s6)
        if on_step_complete: on_step_complete(s6)

        steps.append(s7)
        if on_step_complete: on_step_complete(s7)

        # Step 5 is FATAL
        if s5.status == "error":
            logger.error(f"[{ticker}] Signal scoring failed — aborting pipeline.")
            return self._build_result(ticker, steps)

        if s6.status == "error":
            logger.warning(f"[{ticker}] Glassdoor failed — culture_change may be lower.")
        if s7.status == "error":
            logger.warning(f"[{ticker}] Board governance failed — board_composition may be missing.")

        # Step 8 — CS3 Scoring (FATAL)
        if on_step_start: on_step_start("Scoring")
        s8 = run_step(lambda: self._step_score(ticker))
        if s8.status == "error":
            return self._build_result(ticker, steps)

        # Step 9 — Index Evidence (NON-FATAL)
        if on_step_start: on_step_start("Index Evidence")
        run_step(lambda: self._step_index(ticker, force=True))

        return self._build_result(ticker, steps)

    def _build_result(self, ticker: str, steps: List[PipelineStepResult]) -> PipelineResult:
        error_steps   = [s for s in steps if s.status == "error"]
        success_steps = [s for s in steps if s.status == "success"]
        overall = "partial" if (error_steps and success_steps) else "failed" if error_steps else "success"

        org_air_score = None
        scoring_step = next(
            (s for s in steps if s.name == "Scoring" and s.status == "success"), None
        )
        if scoring_step:
            raw = scoring_step.data.get("orgair_score", scoring_step.data.get("org_air_score"))
            try:
                org_air_score = float(raw) if raw not in (None, "?") else None
            except (ValueError, TypeError):
                org_air_score = None

        index_step = next(
            (s for s in steps if s.name == "Index Evidence" and s.status == "success"), None
        )
        indexed_count = index_step.data.get("indexed_count", 0) if index_step else 0

        signal_flags = []
        signal_step = next(
            (s for s in steps if s.name == "Signal Scoring"), None
        )
        if signal_step and signal_step.signal_flags:
            signal_flags = signal_step.signal_flags

        return PipelineResult(
            ticker=ticker, overall_status=overall, steps=steps,
            org_air_score=org_air_score, indexed_count=indexed_count,
            signal_flags=signal_flags,
        )

    # ── RAG helpers ───────────────────────────────────────────────────────────

    def is_company_indexed(self, ticker: str) -> bool:
        return self.get_company_status(ticker)["chatbot_ready"]

    def ask_chatbot(self, ticker: str, question: str, dimension: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"question": question}
        if dimension:
            params["dimension"] = dimension
        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/rag/chatbot/{ticker}",
                params=params, timeout=REQUEST_TIMEOUT_SHORT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            return {"answer": f"HTTP {e.response.status_code}", "evidence": [], "sources_used": 0, "error": e.response.text[:200]}
        except Exception as e:
            return {"answer": str(e), "evidence": [], "sources_used": 0, "error": str(e)}

    def get_ic_prep(self, ticker: str) -> Dict[str, Any]:
        try:
            resp = self._session.get(f"{self.base_url}/api/v1/rag/ic-prep/{ticker}", timeout=REQUEST_TIMEOUT_LONG)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_justification(self, ticker: str, dimension: str) -> Dict[str, Any]:
        try:
            resp = self._session.get(f"{self.base_url}/api/v1/rag/justify/{ticker}/{dimension}", timeout=REQUEST_TIMEOUT_SHORT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_rag_status(self) -> Dict[str, Any]:
        try:
            resp = self._session.get(f"{self.base_url}/api/v1/rag/status", timeout=REQUEST_TIMEOUT_SHORT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}