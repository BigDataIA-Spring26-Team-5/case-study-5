"""
Job Signals Pipeline — Technology Hiring
app/pipelines/job_signals.py

ALIGNED WITH CASE STUDY 2 PDF SPEC (pages 14-16).

Key changes:
  - Added _is_tech_job() filter per PDF page 16
  - Scoring formula now 60/20/20 per PDF page 15:
      AI ratio (within tech jobs) * 60  (max 60)
      Skill diversity / 10 * 20         (max 20)
      Volume bonus min(ai_jobs/5, 1)*20 (max 20)
  - classify uses multi-word AI_KEYWORDS (no single-word false positives)
  - AI_SKILLS used for diversity scoring
"""
from __future__ import annotations

import asyncio
import json
import structlog
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.settings import settings
from app.config import (
    get_company_search_name,
    get_company_aliases,
    get_search_name_by_official,
    get_aliases_by_official,
    get_job_search_names,
)


from app.models.signal import JobPosting

from app.pipelines.keywords import (
    AI_KEYWORDS,
    AI_KEYWORDS_STRONG,
    AI_KEYWORDS_CONTEXTUAL,
    AI_SKILLS,
    AI_TECHSTACK_KEYWORDS,
    TECH_JOB_TITLE_KEYWORDS,
)
from app.pipelines.signal_pipeline_state import SignalPipelineState
from app.pipelines.utils import clean_nan, safe_filename

from rapidfuzz import fuzz

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Fuzzy matching (unchanged)
# ---------------------------------------------------------------------------

def is_company_match_fuzzy(
    job_company: str,
    target_company: str,
    threshold: float = 75.0,
    ticker: Optional[str] = None,
) -> bool:
    if not job_company or not target_company:
        return False
    job_clean = str(job_company).strip().lower()
    valid_names = [target_company]
    if ticker:
        valid_names.extend(get_company_aliases(ticker))
    else:
        aliases = get_aliases_by_official(target_company)
        if aliases:
            valid_names.extend(aliases)
    valid_names_clean = list({n.strip().lower() for n in valid_names if n})
    if job_clean in valid_names_clean:
        return True
    for vn in valid_names_clean:
        scores = [
            fuzz.token_sort_ratio(job_clean, vn),
            fuzz.partial_ratio(job_clean, vn),
            fuzz.ratio(job_clean, vn),
        ]
        if max(scores) >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Tech job filter (PDF page 16)
# ---------------------------------------------------------------------------

def _is_tech_job(posting: Dict[str, Any]) -> bool:
    """
    Check if a posting is a technology job by scanning the TITLE only.
    Per CS2 PDF page 16: filter tech jobs before computing AI ratio.
    """
    title = posting.get("title", "").lower()
    return any(kw in title for kw in TECH_JOB_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate_postings(postings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicate job postings using two dedup strategies:
      1. By URL — exact same job link from the same or different source
      2. By title + company + location — same job posted across sites

    When duplicates exist, prefer the one with a longer description.
    """
    seen_urls: Dict[str, Dict[str, Any]] = {}
    seen_keys: Dict[str, Dict[str, Any]] = {}

    def _keep_better(existing: Dict, new: Dict) -> Dict:
        """Keep whichever posting has the longer description."""
        if len(new.get("description") or "") > len(existing.get("description") or ""):
            return new
        return existing

    for p in postings:
        # --- Strategy 1: Dedup by URL ---
        url = (p.get("url") or "").strip()
        if url:
            # Normalize Indeed URLs: strip tracking params, keep job key
            url_key = url.split("?")[0].lower() if "indeed.com" not in url else url.lower()
            # For Indeed, extract the jk= param as the unique key
            if "jk=" in url:
                jk = url.split("jk=")[-1].split("&")[0]
                url_key = f"indeed|{jk}"

            if url_key in seen_urls:
                seen_urls[url_key] = _keep_better(seen_urls[url_key], p)
                continue
            seen_urls[url_key] = p

        # --- Strategy 2: Dedup by title + company + location ---
        title = p.get("title", "").strip().lower()
        company = p.get("company_name", "").strip().lower()
        location = (p.get("location") or "").strip().lower()
        key = f"{title}|{company}|{location}"

        if key in seen_keys:
            seen_keys[key] = _keep_better(seen_keys[key], p)
            continue
        seen_keys[key] = p

    # Merge: URL-deduped postings take priority, then add any title-based
    # that weren't already caught by URL
    final: Dict[str, Dict[str, Any]] = {}
    for p in list(seen_urls.values()) + list(seen_keys.values()):
        pid = p.get("id", id(p))
        if pid not in final:
            final[pid] = p

    return list(final.values())


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step1_init(state: SignalPipelineState) -> SignalPipelineState:
    state.mark_started()
    logger.info("-" * 40)
    logger.info("📁 [1/4] INITIALIZING JOB COLLECTION")
    return state

async def step2_fetch_job_postings(
    state: SignalPipelineState,
    *,
    sites: Optional[List[str]] = None,
    results_wanted: Optional[int] = None,
    hours_old: Optional[int] = None,
) -> SignalPipelineState:
    """Scrape job postings for all companies in state."""
    logger.info("-" * 40)
    logger.info("[2/4] FETCHING JOB POSTINGS")

    sites = sites or settings.JOBSPY_DEFAULT_SITES
    results_wanted = results_wanted or settings.JOBSPY_RESULTS_WANTED
    hours_old = hours_old or settings.JOBSPY_HOURS_OLD

    logger.info(f"   Sites: {', '.join(sites)}")
    logger.info(f"   Max results: {results_wanted}")
    logger.info(f"   Hours old: {hours_old}")

    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        msg = "python-jobspy not installed. Run: pip install python-jobspy"
        logger.error(f"   {msg}")
        state.add_error("job_fetch", msg)
        raise ImportError(msg) from e

    for company in state.companies:
        company_id = company.get("id", "")
        company_name = company.get("name", "")
        ticker = company.get("ticker", "").upper()
        if not company_name:
            continue

        # Get ALL search names for this company (multi-name support)
        search_names = get_job_search_names(ticker)
        if not search_names:
            # Fallback to single search name (backward compatible)
            search_name = (
                get_company_search_name(ticker)
                or get_search_name_by_official(company_name)
                or company_name
            )
            search_names = [search_name]

        company_postings: List[Dict[str, Any]] = []

        for search_name in search_names:
            await asyncio.sleep(max(state.request_delay, settings.JOBSPY_REQUEST_DELAY))

            try:
                logger.info(f"   Scraping: {company_name} (search: '{search_name}')...")

                jobs_df = scrape_jobs(
                    site_name=sites,
                    search_term=search_name,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed="USA",
                    linkedin_fetch_description=True,
                )

                filtered_count = 0
                total_raw = 0

                if jobs_df is not None and not jobs_df.empty:
                    total_raw = len(jobs_df)
                    for _, row in jobs_df.iterrows():
                        job_company = (
                            str(row.get("company", ""))
                            if clean_nan(row.get("company"))
                            else ""
                        )
                        source = str(row.get("site", "unknown"))

                        if not is_company_match_fuzzy(
                            job_company, search_name,
                            threshold=settings.JOBSPY_FUZZY_MATCH_THRESHOLD,
                            ticker=ticker,
                        ):
                            filtered_count += 1
                            continue

                        posting = JobPosting(
                            company_id=company_id,
                            company_name=job_company,
                            title=str(row.get("title", "")),
                            description=str(row.get("description", "")),
                            location=(
                                str(row.get("location", ""))
                                if clean_nan(row.get("location"))
                                else None
                            ),
                            posted_date=clean_nan(row.get("date_posted")),
                            source=source,
                            url=(
                                str(row.get("job_url", ""))
                                if clean_nan(row.get("job_url"))
                                else None
                            ),
                        )
                        company_postings.append(posting.model_dump())

                logger.info(
                    f"      Raw: {total_raw} | Matched: "
                    f"{len(company_postings)} | Filtered: {filtered_count}"
                )

            except Exception as e:
                state.add_error("job_fetch", str(e), company_id)
                logger.error(f"      Error: {e}")

        # Log combined results for multi-name searches
        if len(search_names) > 1:
            logger.info(
                f"   Combined {len(search_names)} searches -> "
                f"{len(company_postings)} total postings for {company_name}"
            )

        state.job_postings.extend(company_postings)
        state.summary["job_postings_collected"] += len(company_postings)

    # --- Deduplicate job postings ---
    before_dedup = len(state.job_postings)
    state.job_postings = _deduplicate_postings(state.job_postings)
    dupes_removed = before_dedup - len(state.job_postings)

    logger.info(f"   Total collected: {before_dedup} job postings")
    if dupes_removed > 0:
        logger.info(f"   Removed {dupes_removed} duplicates -> {len(state.job_postings)} unique postings")
    return state

# ---------------------------------------------------------------------------
# Classification (PDF page 14-15)
# ---------------------------------------------------------------------------

def _has_keyword(text: str, keyword: str) -> bool:
    """Match multi-word keywords in text. All AI_KEYWORDS are multi-word
    or unambiguous, so simple substring match is safe."""
    return keyword in text


def step3_classify_ai_jobs(state: SignalPipelineState) -> SignalPipelineState:
    """
    Classify each job posting as AI-related.

    Two-tier keyword matching to reduce false positives:
      - STRONG keywords: any single match in title+description → AI role
      - CONTEXTUAL keywords (e.g. "data scientist"): must appear in TITLE,
        or appear 2+ times in description, to count. A single mention
        in a boilerplate "about us" paragraph doesn't qualify.

    Groq expands both keyword sets with company-specific terms once per company
    before classification begins, so industry-specific vocabulary (e.g.
    "recommendation engine" for Netflix, "GPU compute" for NVIDIA) is captured.
    """
    from app.pipelines.keywords import (
        AI_KEYWORDS_STRONG, AI_KEYWORDS_CONTEXTUAL,
        AI_SKILLS, AI_TECHSTACK_KEYWORDS,
    )
    from app.services.groq_enrichment import get_dimension_keywords

    logger.info("-" * 40)
    logger.info("🤖 [3/4] CLASSIFYING AI-RELATED JOBS")

    # ------------------------------------------------------------------
    # Build per-company expanded keyword sets via Groq (called once each)
    # ------------------------------------------------------------------
    company_strong: Dict[str, frozenset] = {}
    company_contextual: Dict[str, frozenset] = {}
    company_skills: Dict[str, frozenset] = {}

    for company in state.companies:
        ticker = company.get("ticker", "").upper()
        name = company.get("name", ticker)
        cid = company.get("id", "")
        try:
            # Pass a representative sample as context; merge extras with full base set
            sample_strong = list(AI_KEYWORDS_STRONG)[:15]
            groq_strong = get_dimension_keywords(ticker, name, "ai_job_keywords", sample_strong)
            company_strong[cid] = AI_KEYWORDS_STRONG | frozenset(k for k in groq_strong if k not in sample_strong)

            groq_ctx = get_dimension_keywords(ticker, name, "ai_job_contextual", list(AI_KEYWORDS_CONTEXTUAL))
            company_contextual[cid] = AI_KEYWORDS_CONTEXTUAL | frozenset(k for k in groq_ctx if k not in AI_KEYWORDS_CONTEXTUAL)

            groq_skills = get_dimension_keywords(ticker, name, "ai_skills", list(AI_SKILLS))
            company_skills[cid] = AI_SKILLS | frozenset(k for k in groq_skills if k not in AI_SKILLS)

            logger.info(
                f"   [{ticker}] Groq-expanded: {len(company_strong[cid])} strong kws, "
                f"{len(company_contextual[cid])} contextual kws, {len(company_skills[cid])} skills"
            )
        except Exception as exc:
            logger.warning(f"   [{ticker}] Groq keyword expansion failed: {exc} — using base keywords")
            company_strong[cid] = AI_KEYWORDS_STRONG
            company_contextual[cid] = AI_KEYWORDS_CONTEXTUAL
            company_skills[cid] = AI_SKILLS

    # ------------------------------------------------------------------
    # Classify each posting using its company's expanded keyword set
    # ------------------------------------------------------------------
    for posting in state.job_postings:
        cid = posting.get("company_id", "")
        kws_strong = company_strong.get(cid, AI_KEYWORDS_STRONG)
        kws_contextual = company_contextual.get(cid, AI_KEYWORDS_CONTEXTUAL)
        kws_skills = company_skills.get(cid, AI_SKILLS)

        title = posting.get("title", "")
        desc = posting.get("description", "") or ""

        title_lower = title.lower()
        desc_lower = desc.lower()
        full_text = f"{title_lower} {desc_lower}"

        # --- Strong keywords: single match anywhere is enough ---
        strong_matches = [kw for kw in kws_strong if kw in full_text]

        # --- Contextual keywords: must be in title OR 2+ times in description ---
        contextual_matches = []
        for kw in kws_contextual:
            in_title = kw in title_lower
            desc_count = desc_lower.count(kw)
            if in_title or desc_count >= 2:
                contextual_matches.append(kw)

        ai_kw = strong_matches + contextual_matches

        # Find AI skills (for diversity scoring)
        skills = [sk for sk in kws_skills if sk in full_text]

        # Find techstack keywords (kept for metadata)
        ts_kw = [kw for kw in AI_TECHSTACK_KEYWORDS if kw in full_text]

        posting["ai_keywords_found"] = ai_kw
        posting["ai_skills_found"] = skills
        posting["techstack_keywords_found"] = ts_kw
        posting["is_ai_role"] = len(ai_kw) > 0
        posting["ai_score"] = min(100.0, len(ai_kw) * 15.0)

    ai_count = sum(1 for p in state.job_postings if p.get("is_ai_role"))
    total = len(state.job_postings)
    tech_count = sum(1 for p in state.job_postings if _is_tech_job(p))

    logger.info(f"   • Total: {total} | Tech jobs: {tech_count} | AI-related: {ai_count}")
    if tech_count > 0:
        logger.info(f"   • AI ratio (within tech): {ai_count / tech_count * 100:.1f}%")
    elif total > 0:
        logger.info(f"   • AI ratio (all jobs): {ai_count / total * 100:.1f}%")

    return state


# ---------------------------------------------------------------------------
# Scoring (PDF page 15)
# ---------------------------------------------------------------------------

def calculate_job_score(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate technology_hiring score (0-100) per CS2 PDF page 15.

    Formula:
      - AI ratio:       min(ai_ratio * 60, 60)       max 60 pts
        where ai_ratio = ai_jobs / total_TECH_jobs (not all jobs)
      - Skill diversity: min(len(skills) / 10, 1) * 20  max 20 pts
      - Volume bonus:    min(ai_jobs / 5, 1) * 20       max 20 pts

    Confidence based on total_tech_jobs (more data = higher confidence).
    """
    total_all = len(jobs)
    tech_jobs = [j for j in jobs if _is_tech_job(j)]
    total_tech = len(tech_jobs)

    # AI jobs must also be tech jobs to count
    ai_jobs_list = [j for j in tech_jobs if j.get("is_ai_role", False)]
    ai_jobs = len(ai_jobs_list)

    # Collect all unique AI skills across ALL postings (including non-tech)
    all_skills = set()
    for j in jobs:
        all_skills.update(j.get("ai_skills_found", []))

    # Also count skills from ai_keywords_found for backward compat
    all_kw = set()
    for j in jobs:
        all_kw.update(j.get("ai_keywords_found", []))

    # --- Scoring per PDF ---
    ai_ratio = ai_jobs / total_tech if total_tech > 0 else 0
    ratio_score = min(ai_ratio * 60, 60)
    diversity_score = min(len(all_skills) / 10, 1) * 20
    volume_score = min(ai_jobs / 5, 1) * 20
    final = round(ratio_score + diversity_score + volume_score, 1)

    # Confidence based on tech job sample size (PDF page 15 line 80)
    confidence = min(0.5 + total_tech / 100, 0.95)

    return {
        "score": final,
        "ai_jobs": ai_jobs,
        "total_jobs": total_all,
        "total_tech_jobs": total_tech,
        "ai_ratio": round(ai_ratio, 3),
        "ai_keywords": sorted(all_kw),
        "ai_skills": sorted(all_skills),
        "score_breakdown": {
            "ratio_score": round(ratio_score, 1),
            "volume_score": round(volume_score, 1),
            "diversity_score": round(diversity_score, 1),
        },
        "confidence": round(confidence, 3),
    }


def step4_score_job_market(state: SignalPipelineState) -> SignalPipelineState:
    """Score job market (technology_hiring) for each company."""
    logger.info("-" * 40)
    logger.info("📊 [4/4] SCORING JOB MARKET")

    name_lookup = {c.get("id"): c.get("name", c.get("id")) for c in state.companies}
    company_jobs: Dict[str, List] = defaultdict(list)
    for p in state.job_postings:
        company_jobs[p["company_id"]].append(p)

    for cid, jobs in company_jobs.items():
        if not jobs:
            state.job_market_scores[cid] = 0.0
            continue

        analysis = calculate_job_score(jobs)
        state.job_market_scores[cid] = round(analysis["score"], 2)
        state.job_market_analyses[cid] = analysis

        name = name_lookup.get(cid, cid)
        bd = analysis["score_breakdown"]
        logger.info(
            f"   • {name}: {analysis['score']:.1f}/100 "
            f"(ratio={bd['ratio_score']:.1f}, vol={bd['volume_score']:.1f}, "
            f"div={bd['diversity_score']:.1f}) "
            f"[{analysis['ai_jobs']} AI / {analysis['total_tech_jobs']} tech / {analysis['total_jobs']} total]"
        )

    logger.info(f"   ✅ Scored {len(state.job_market_scores)} companies")
    return state


# ---------------------------------------------------------------------------
# S3 + Snowflake storage (unchanged logic, updated metadata)
# ---------------------------------------------------------------------------

def step5_store_to_s3_and_snowflake(state: SignalPipelineState) -> SignalPipelineState:
    from app.services.s3_storage import get_s3_service
    from app.repositories.signal_repository import SignalRepository

    logger.info("-" * 40)
    logger.info("☁️ [5/5] STORING TO S3 & SNOWFLAKE")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    s3 = get_s3_service()
    signal_repo = SignalRepository()

    company_jobs: Dict[str, List] = defaultdict(list)
    for p in state.job_postings:
        company_jobs[p["company_id"]].append(p)

    for cid, jobs in company_jobs.items():
        if not jobs:
            continue

        company_name = jobs[0].get("company_name", cid)
        ticker = None
        for c in state.companies:
            if c.get("id") == cid:
                ticker = c.get("ticker", "").upper()
                break
        ticker = ticker or safe_filename(company_name).upper()

        analysis = state.job_market_analyses.get(cid, {})
        score = state.job_market_scores.get(cid, 0.0)

        s3_key = f"signals/jobs/{ticker}/{timestamp}.json"
        s3.upload_json(
            {
                "company_id": cid,
                "company_name": company_name,
                "ticker": ticker,
                "collection_date": timestamp,
                "total_jobs": len(jobs),
                "total_tech_jobs": analysis.get("total_tech_jobs", 0),
                "ai_jobs": analysis.get("ai_jobs", 0),
                "job_market_score": score,
                "score_breakdown": analysis.get("score_breakdown", {}),
                "jobs": jobs,
            },
            s3_key,
        )
        logger.info(f"   📤 S3: {s3_key}")

        ai_count = analysis.get("ai_jobs", 0)
        sources = list({j.get("source", "other") for j in jobs})
        primary_src = max(
            defaultdict(int, {j.get("source", "other"): 1 for j in jobs}),
            key=lambda k: sum(1 for j in jobs if j.get("source") == k),
            default="other",
        )

        signal_repo.create_signal(
            company_id=cid,
            category="job_market",
            source=primary_src,
            signal_date=datetime.now(timezone.utc),
            raw_value=f"Found {ai_count} AI roles out of {analysis.get('total_tech_jobs', 0)} tech jobs ({len(jobs)} total)",
            normalized_score=score,
            confidence=analysis.get("confidence", 0.5),
            metadata={
                "collection_date": timestamp,
                "s3_key": s3_key,
                "total_jobs": len(jobs),
                "total_tech_jobs": analysis.get("total_tech_jobs", 0),
                "ai_jobs": ai_count,
                "ai_ratio": analysis.get("ai_ratio", 0),
                "score_breakdown": analysis.get("score_breakdown", {}),
                "ai_skills": analysis.get("ai_skills", []),
                "sources": sources,
            },
        )
        logger.info(f"   💾 Snowflake: {company_name} (score: {score})")

    logger.info(f"   ✅ Stored {len(company_jobs)} companies to S3 + Snowflake")
    return state


# ---------------------------------------------------------------------------
# Main pipeline runner
# ---------------------------------------------------------------------------

async def run_job_signals(
    state: SignalPipelineState,
    *,
    skip_storage: bool = False,
) -> SignalPipelineState:
    state = step1_init(state)
    state = await step2_fetch_job_postings(state)
    state = step3_classify_ai_jobs(state)
    state = step4_score_job_market(state)
    if not skip_storage:
        state = step5_store_to_s3_and_snowflake(state)
    return state