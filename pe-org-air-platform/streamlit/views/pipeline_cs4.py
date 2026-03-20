"""
PE Org-AI-R Platform — CS4 Pipeline View
streamlit/views/pipeline_cs4.py

FIXES v3:
  - If ticker already exists in Snowflake → auto-confirm, show individual run buttons
  - If ticker is new → show confirm card + Run full pipeline + individual buttons
  - Steps show correct Done/Waiting status from _get_completed_steps
  - Dimension scores shown in a ROW grid (not column)
  - on_step_complete maps "success" → "done" for display
  - No timeout restrictions
  - BUG #2 + #4 fixes preserved
"""
from __future__ import annotations

import sys
import os
import time
import requests
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.company_resolver import resolve_company
from utils.pipeline_client  import PipelineClient, PipelineStepResult

BASE_URL = "http://localhost:8000"

PIPELINE_STEPS = [
    ("Company Setup",     "🏢", "POST /api/v1/companies"),
    ("SEC Filings",       "📄", "POST /api/v1/documents/collect — EDGAR + S3"),
    ("Parse Documents",   "🔍", "POST /api/v1/documents/parse/{ticker}"),
    ("Chunk Documents",   "✂️",  "POST /api/v1/documents/chunk/{ticker}"),
    ("Signal Scoring",    "📡", "POST /api/v1/signals/collect — all 6 categories"),
    ("Glassdoor Culture", "💬", "Included in unified signal collection"),
    ("Board Governance",  "🏛️", "Included in unified signal collection"),
    ("Scoring",           "🧮", "POST /api/v1/scoring/{ticker}"),
    ("Index Evidence",    "🗂️", "POST /rag/index/{ticker}?force=true"),
]

STEP_RUNNING_MSGS = [
    "Writing company to Snowflake companies table...",
    "Fetching 10-K, DEF 14A, 8-K from SEC EDGAR...",
    "Extracting text from PDF filings via pdfplumber...",
    "Splitting into overlapping chunks with metadata...",
    "Running technology_hiring · digital_presence · innovation_activity signals...",
    "Scraping Glassdoor reviews and computing sentiment...",
    "Analyzing board composition and proxy governance signals...",
    "Computing 7 dimension scores and composite...",
    "Embedding chunks and uploading to Chroma pe_evidence collection...",
]

# Prerequisites relaxed — only company (step 0) is hard required for most steps.
# Steps can run in any order as long as the company exists.
STEP_PREREQUISITES = {
    0: [],      # Company Setup — no prereqs
    1: [0],     # SEC Filings — needs company
    2: [0],     # Parse — needs company (will check docs internally)
    3: [0],     # Chunk — needs company
    4: [0],     # Signal Scoring — needs company
    5: [0],     # Glassdoor — needs company
    6: [0],     # Board Governance — needs company
    7: [0],     # Scoring — needs company (will use whatever signals exist)
    8: [0],     # Index Evidence — needs company
}

TICKER_SECTOR_MAP = {
    "NVDA": "Technology", "NFLX": "Technology", "MSFT": "Technology",
    "GOOGL": "Technology", "GOOG": "Technology", "AAPL": "Technology",
    "META": "Technology", "AMZN": "Technology", "CRM": "Technology",
    "JPM": "Financial Services", "BAC": "Financial Services",
    "GS": "Financial Services", "UNH": "Healthcare", "JNJ": "Healthcare",
}


def _get_sector(company) -> str:
    sector = getattr(company, "sector", None) or (
        company.get("sector") if isinstance(company, dict) else None
    )
    if sector:
        return sector.title()
    ticker = getattr(company, "ticker", "") or (
        company.get("ticker", "") if isinstance(company, dict) else ""
    )
    return TICKER_SECTOR_MAP.get(ticker.upper(), "Unknown")


def _load_companies() -> list:
    try:
        r = requests.get(f"{BASE_URL}/api/v1/companies/all")
        if r.status_code == 200:
            return r.json().get("items", [])
    except Exception:
        pass
    return []


def _company_exists_in_snowflake(ticker: str) -> dict | None:
    """Check if company already exists in Snowflake. Returns company dict or None."""
    try:
        r = requests.get(f"{BASE_URL}/api/v1/companies/{ticker}")
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _get_completed_steps(ticker: str) -> set[int]:
    """Determine which pipeline steps are already complete."""
    completed = set()

    # Step 0 — company exists
    try:
        r = requests.get(f"{BASE_URL}/api/v1/companies/{ticker}")
        if r.status_code == 200:
            completed.add(0)
    except Exception:
        pass

    # Steps 1/2/3 — document pipeline status
    # Primary: /api/v1/documents/stats/{ticker}
    # Returns: {"total": 33, "chunks": 125, "form_10k": 3, ...}
    try:
        r = requests.get(f"{BASE_URL}/api/v1/documents/stats/{ticker}")
        if r.status_code == 200:
            ds = r.json()
            total_docs = (
                ds.get("total", 0)
                or ds.get("total_documents", 0)
                or ds.get("document_count", 0)
                or ds.get("raw_count", 0)
            )
            chunks = (
                ds.get("chunks", 0)
                or ds.get("chunk_count", 0)
                or ds.get("total_chunks", 0)
            )
            # If total docs > 0 → collected (step 1) AND parsed (step 2)
            if total_docs > 0:
                completed.add(1)  # collected
                completed.add(2)  # parsed (docs are counted after parsing)
            # If chunks > 0 → chunked (step 3)
            if chunks > 0:
                completed.add(3)
    except Exception:
        pass

    # Fallback: try /api/v1/documents/{ticker}/status (may 404)
    if 1 not in completed:
        try:
            r = requests.get(f"{BASE_URL}/api/v1/documents/{ticker}/status")
            if r.status_code == 200:
                ds = r.json()
                if ds.get("raw_count", 0) > 0 or ds.get("total", 0) > 0:
                    completed.add(1)
                if ds.get("parsed_count", 0) > 0 or ds.get("total", 0) > 0:
                    completed.add(2)
                if ds.get("chunk_count", 0) > 0 or ds.get("chunks", 0) > 0:
                    completed.add(3)
        except Exception:
            pass

    # Steps 4/5/6 + fallback for step 1 — ONE evidence fetch
    evidence_data: dict = {}
    try:
        r = requests.get(f"{BASE_URL}/api/v1/companies/{ticker}/evidence")
        if r.status_code == 200:
            evidence_data = r.json()
    except Exception:
        pass

    # Fallback for step 1 from evidence doc count
    if 1 not in completed:
        doc_count = (
            evidence_data.get("document_count", 0)
            or evidence_data.get("total_documents", 0)
        )
        if doc_count > 0:
            completed.add(1)

    sig = evidence_data.get("signal_summary", {}) or {}
    signals_list = evidence_data.get("signals", []) or []

    # Build a set of signal categories that exist in the signals array
    signal_categories = {s.get("category", "") for s in signals_list if s.get("normalized_score")}

    # Step 4 — technology signals (any of the 4 signal scores present)
    if sig and any(sig.get(k) for k in [
        "technology_hiring_score", "digital_presence_score",
        "innovation_activity_score", "leadership_signals_score",
    ]):
        completed.add(4)
    # Fallback: check signals array for tech-related categories
    if 4 not in completed:
        tech_cats = {"technology_hiring", "digital_presence", "innovation_activity", "leadership_signals"}
        if signal_categories & tech_cats:
            completed.add(4)

    # Step 5 — glassdoor/culture
    # Check signal_summary first
    culture_val = (
        sig.get("culture_score")
        or sig.get("culture_change_score")
        or sig.get("glassdoor_score")
    )
    if culture_val is not None and culture_val != 0:
        completed.add(5)
    # Fallback: check signals array for culture-related categories
    if 5 not in completed:
        culture_cats = {"culture", "culture_signals", "glassdoor", "glassdoor_reviews"}
        if signal_categories & culture_cats:
            completed.add(5)
    # NO further fallback — don't check S3 endpoint as it can return stale/cross-company data

    # Step 6 — board governance
    # Check signal_summary first
    board_val = (
        sig.get("board_governance_score")
        or sig.get("board_composition_score")
        or sig.get("governance_score")
    )
    if board_val is not None and board_val != 0:
        completed.add(6)
    # Fallback: check signals array for board governance category
    if 6 not in completed:
        board_cats = {"board_governance", "board_composition", "governance_signals"}
        if signal_categories & board_cats:
            completed.add(6)
    # NO further fallback — don't check S3 endpoint as it can return stale/cross-company data

    # Step 7 — dimension scores exist
    try:
        r = requests.get(f"{BASE_URL}/api/v1/scoring/{ticker}/dimensions")
        if r.status_code == 200:
            data = r.json()
            scores = data.get("scores", [])
            if scores and len(scores) > 0:
                completed.add(7)
    except Exception:
        pass

    # Step 8 — evidence indexed (chatbot ready)
    try:
        client = PipelineClient()
        status = client.get_company_status(ticker)
        if status.get("chatbot_ready"):
            completed.add(8)
    except Exception:
        pass

    return completed


def _step_html(i: int, name: str, icon: str, detail: str,
               status: str, message: str, elapsed: str) -> str:
    circle_cls = {"idle": "sn-idle", "running": "sn-running", "done": "sn-done", "error": "sn-err"}.get(status, "sn-idle")
    circle_lbl = {"idle": str(i + 1), "running": "…", "done": "✓", "error": "!"}.get(status, str(i + 1))
    row_cls    = {"running": "step-row-running", "done": "step-row-done", "error": "step-row-error"}.get(status, "")
    status_lbl = {"idle": "Waiting", "running": "Running...", "done": "Done", "error": "Error"}.get(status, "Waiting")
    status_cls = {"idle": "st-idle", "running": "st-running", "done": "st-done", "error": "st-err"}.get(status, "st-idle")
    msg_html   = f'<div class="step-msg">{message}</div>' if message else ""
    return (
        f'<div class="step-row {row_cls}">'
        f'<div class="step-num {circle_cls}">{circle_lbl}</div>'
        f'<span class="step-icon">{icon}</span>'
        f'<div style="flex:1;min-width:0">'
        f'<div class="step-name">{i + 1}. {name}</div>'
        f'<div class="step-detail">{detail}</div>'
        f'{msg_html}'
        f'</div>'
        f'<span class="step-status-lbl {status_cls}">{status_lbl}</span>'
        f'<span class="step-time">{elapsed}</span>'
        f'</div>'
    )


def _run_single_step(resolved, client: PipelineClient, idx: int, name: str, ticker: str, already_done: bool = False):
    """Run a single pipeline step."""
    ph = st.empty()
    ph.info(f"🔄 Running **Step {idx + 1}: {name}**...")
    start = time.time()
    try:
        if idx == 0:
            result = client._step_create_company(resolved)
        elif idx == 1:
            result = client._step_collect_sec(ticker, resolved.cik)
        elif idx == 2:
            ds = client._get_doc_status(ticker)
            result = client._step_parse(ticker, doc_status=ds, force=already_done)
        elif idx == 3:
            ds = client._get_doc_status(ticker)
            result = client._step_chunk(ticker, doc_status=ds, force=already_done)
        elif idx == 4:
            website = getattr(resolved, "website", None)
            result = client._step_signal_scoring(ticker, company_name=resolved.name, website=website)
        elif idx == 5:
            result = client._step_glassdoor(ticker)
        elif idx == 6:
            result = client._step_board_governance(ticker)
        elif idx == 7:
            result = client._step_score(ticker)
        elif idx == 8:
            result = client._step_index(ticker, force=True)
        else:
            result = None

        elapsed = time.time() - start
        if result and result.status == "success":
            ph.success(f"✅ **Step {idx + 1}: {name}** ({elapsed:.1f}s) — {result.message}")
        elif result and result.status == "skipped":
            ph.info(f"⏭️ **Step {idx + 1}: {name}** ({elapsed:.1f}s) — {result.message}")
        elif result and result.status == "error":
            ph.error(f"❌ **Step {idx + 1}: {name}** — {result.error}")
        else:
            ph.warning(f"⚠️ **Step {idx + 1}: {name}** — no result returned")
    except Exception as e:
        ph.error(f"❌ **Step {idx + 1}: {name}** — {e}")


def _render_step_rows(resolved, ticker: str, completed: set[int]):
    """Render the 9 pipeline step rows with individual run buttons."""
    client = PipelineClient()

    step_states_key = f"step_states_{ticker}"
    if step_states_key not in st.session_state:
        st.session_state[step_states_key] = {
            i: {"status": "idle", "msg": "", "elapsed": ""}
            for i in range(len(PIPELINE_STEPS))
        }
    step_states = st.session_state[step_states_key]

    # Mark completed steps as "done"
    for i in completed:
        if step_states[i]["status"] in ("idle", ""):
            step_states[i]["status"] = "done"

    for i, (step_name, icon, detail) in enumerate(PIPELINE_STEPS):
        prereqs  = STEP_PREREQUISITES[i]
        missing  = [p for p in prereqs if p not in completed]
        disabled = bool(missing)
        state    = step_states[i]
        btn_lbl  = "Re-run" if state["status"] == "done" else "▶ Run"
        already_done = (state["status"] == "done")

        row_col, btn_col = st.columns([10, 1])
        with row_col:
            st.markdown(
                _step_html(i, step_name, icon, detail,
                           state["status"], state["msg"], state["elapsed"]),
                unsafe_allow_html=True,
            )
        with btn_col:
            st.markdown("<div style='padding-top:12px'>", unsafe_allow_html=True)
            if st.button(btn_lbl, key=f"run_step_{i}_{ticker}",
                         disabled=disabled, use_container_width=True):
                _run_single_step(resolved, client, i, step_name, ticker, already_done=already_done)
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    # Progress bar
    done_count = sum(1 for s in step_states.values() if s["status"] == "done")
    pct        = done_count / len(PIPELINE_STEPS)
    prog_lbl   = f"{done_count} of {len(PIPELINE_STEPS)} steps complete" if done_count else "Ready to run"
    st.markdown(
        f'<div class="prog-label">{prog_lbl}</div>'
        f'<div class="prog-bar"><div class="prog-fill" style="width:{pct*100:.0f}%"></div></div>',
        unsafe_allow_html=True,
    )


def _show_dimension_scores_inline(ticker: str):
    """Show composite + 7 dimension scores in a horizontal ROW grid."""
    try:
        # Fetch dimension scores
        r = requests.get(f"{BASE_URL}/api/v1/scoring/{ticker}/dimensions")
        if r.status_code != 200:
            return
        dim_scores = r.json().get("scores", [])
        if not dim_scores:
            return

        # Calculate composite as weighted average
        weights = {
            "data_infrastructure": 0.25, "ai_governance": 0.20,
            "technology_stack": 0.15, "talent_skills": 0.15,
            "leadership_vision": 0.10, "use_case_portfolio": 0.10,
            "culture_change": 0.05,
        }
        total_w, total_s = 0.0, 0.0
        for dim in dim_scores:
            d = dim.get("dimension", "")
            s = dim.get("score", 0)
            w = weights.get(d, 0.1)
            total_s += s * w
            total_w += w
        composite = total_s / total_w if total_w > 0 else 0

        # Also try fetching actual composite from API
        try:
            r2 = requests.get(f"{BASE_URL}/api/v1/scoring/{ticker}")
            if r2.status_code == 200:
                d2 = r2.json()
                api_composite = d2.get("composite_score") or d2.get("org_air_score")
                if api_composite:
                    composite = float(api_composite)
        except Exception:
            pass

        # Build HTML — composite first, then 7 dimensions
        items_html = (
            f'<div class="dim-score-item" style="background:rgba(79,70,229,0.06);border-color:#4F46E5">'
            f'<div class="dim-score-label" style="color:#4F46E5;opacity:1">AI Readiness (Overall)</div>'
            f'<div class="dim-score-value">{composite:.1f}</div>'
            f'</div>'
        )
        for dim in dim_scores:
            label = dim.get("dimension", "").replace("_", " ").title()
            score = dim.get("score", 0)
            items_html += (
                f'<div class="dim-score-item">'
                f'<div class="dim-score-label">{label}</div>'
                f'<div class="dim-score-value">{score:.1f}</div>'
                f'</div>'
            )
        st.markdown(
            f'<div style="margin:12px 0">'
            f'<div style="font-size:14px;font-weight:600;margin-bottom:8px">📊 Dimension Scores</div>'
            f'<div class="dim-scores-grid">{items_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# ── Main render ───────────────────────────────────────────────────────────────

def render_pipeline_page():

    st.markdown(
        '<div class="step-name" style="font-size:15px;margin-bottom:3px">Pipeline builder</div>'
        '<div class="step-detail" style="font-size:12px;opacity:0.6">Collect, parse, chunk, score evidence and index evidence for a company</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Input row
    inp_col, btn_col = st.columns([4, 2])
    with inp_col:
        company_input = st.text_input(
            label="Company ticker, name, or CIK",
            placeholder="Enter a company ticker, name, or CIK...",
            key="pipe_input",
            label_visibility="collapsed",
        )
    with btn_col:
        fetch_clicked = st.button("🔍 Fetch Company Details", use_container_width=True, key="fetch_btn", type="primary")

    # ── Resolve
    resolved = st.session_state.get("resolved_company")

    if company_input and fetch_clicked:
        # First check: does this company already exist in Snowflake?
        existing = _company_exists_in_snowflake(company_input.strip().upper())

        if existing:
            # Company already exists — resolve it and auto-confirm
            with st.spinner("Company found in Snowflake, resolving details..."):
                try:
                    resolved = resolve_company(company_input)
                    st.session_state["resolved_company"]  = resolved
                    st.session_state["company_confirmed"] = True  # AUTO-CONFIRM
                    st.session_state["company_is_existing"] = True
                except Exception as e:
                    st.error(f"Could not resolve: {e}")
                    resolved = None
        else:
            # New company — normal flow
            with st.spinner("Resolving via Yahoo Finance + SEC EDGAR..."):
                try:
                    resolved = resolve_company(company_input)
                    st.session_state["resolved_company"]  = resolved
                    st.session_state["company_confirmed"] = False
                    st.session_state["company_is_existing"] = False
                except Exception as e:
                    st.error(f"Could not resolve: {e}")
                    resolved = None

    # ── Company card + pipeline controls
    if resolved:
        ticker    = resolved.ticker
        name      = resolved.name
        sector    = _get_sector(resolved)
        cik       = resolved.cik or "N/A"
        rev       = resolved.revenue_millions
        emp       = resolved.employee_count
        initials  = ticker[:2].upper()
        confirmed = st.session_state.get("company_confirmed", False)
        is_existing = st.session_state.get("company_is_existing", False)

        rev_str  = f"${rev:,.0f}M" if rev else "N/A"
        emp_str  = f"{emp:,}" if emp else "N/A"
        meta_str = f"{ticker} · {sector} · Revenue {rev_str} · Employees {emp_str} · CIK {cik}"
        card_cls = "co-confirm-card confirmed" if confirmed else "co-confirm-card"

        st.markdown(
            f'<div class="{card_cls}">'
            f'<div class="co-logo">{initials}</div>'
            f'<div style="flex:1">'
            f'<div style="font-size:18px;font-weight:700;margin-bottom:5px">{name}</div>'
            f'<div style="font-size:14px;opacity:0.7;line-height:1.8">{meta_str}</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── For NEW companies: show confirm buttons first
        if not confirmed:
            c1, c2, _ = st.columns([1, 1, 4])
            with c1:
                if st.button("Looks good ✓", key="confirm_yes", type="primary"):
                    st.session_state["company_confirmed"] = True
                    st.rerun()
            with c2:
                if st.button("Not right", key="confirm_no"):
                    st.session_state["resolved_company"]  = None
                    st.session_state["company_confirmed"] = False
                    st.rerun()

        # ── After confirmation
        if confirmed:
            completed = _get_completed_steps(ticker)

            if is_existing and len(completed) > 0:
                # ── EXISTING COMPANY: show status + individual run buttons only
                st.markdown(
                    f'<div style="font-size:12px;color:#4F46E5;margin:8px 0 4px">'
                    f'✅ <b>{name}</b> already exists in platform — use individual steps below to re-run or update.</div>',
                    unsafe_allow_html=True,
                )

                # Show dimension scores if they exist (step 7 done)
                if 7 in completed:
                    _show_dimension_scores_inline(ticker)

                st.markdown("<br>", unsafe_allow_html=True)
                _render_step_rows(resolved, ticker, completed)

            else:
                # ── NEW COMPANY: show Run full pipeline + individual steps
                if st.button(
                    "▶ Run Full Pipeline",
                    type="primary",
                    use_container_width=True,
                    key="run_full_btn",
                ):
                    _run_full_pipeline(resolved)

                st.markdown("<br>", unsafe_allow_html=True)
                _render_step_rows(resolved, ticker, completed)

    # ── Companies table
    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()
    _render_companies_table()


def _render_companies_table():
    st.markdown("**Companies in Platform**")
    companies = _load_companies()
    if not companies:
        st.info("No companies yet — enter a company above to get started.")
        return

    h0, h1, h2, h3 = st.columns([2, 1, 1, 1])
    h0.caption("Company"); h1.caption("Ticker"); h2.caption("Sector"); h3.caption("Action")

    for co in companies[:10]:
        ticker = co.get("ticker", "")
        name   = co.get("name", ticker)
        sector = _get_sector(co)
        r0, r1, r2, r3 = st.columns([2, 1, 1, 1])
        r0.write(name[:28])
        r1.write(ticker)
        r2.write(sector)
        with r3:
            if st.button("💬 Chat", key=f"co_chat_{ticker}", use_container_width=True):
                st.session_state["active_page"]     = "chatbot"
                st.session_state["chatbot_ticker"]  = ticker
                st.session_state["chatbot_company"] = name
                st.rerun()


def _run_full_pipeline(resolved):
    ticker = resolved.ticker
    client = PipelineClient()

    st.markdown("### Pipeline Progress")

    step_phs = []
    for i, (name, icon, detail) in enumerate(PIPELINE_STEPS):
        ph = st.empty()
        ph.markdown(_step_html(i, name, icon, detail, "idle", "", ""), unsafe_allow_html=True)
        step_phs.append(ph)

    prog_ph   = st.empty()
    status_ph = st.empty()
    prog_ph.markdown(
        '<div class="prog-label">Ready to run</div>'
        '<div class="prog-bar"><div class="prog-fill" style="width:0%"></div></div>',
        unsafe_allow_html=True,
    )

    def on_step_start(step_name: str):
        idx = next((i for i, (n, _, _) in enumerate(PIPELINE_STEPS) if n == step_name), None)
        if idx is not None:
            step_phs[idx].markdown(
                _step_html(idx, step_name, PIPELINE_STEPS[idx][1], PIPELINE_STEPS[idx][2],
                           "running", STEP_RUNNING_MSGS[idx], ""),
                unsafe_allow_html=True,
            )
            status_ph.caption(f"⏳ Running: {step_name}...")

    def on_step_complete(step: PipelineStepResult):
        idx = next((i for i, (n, _, _) in enumerate(PIPELINE_STEPS) if n == step.name), None)
        if idx is not None:
            dur = f"{step.duration_seconds:.1f}s"
            # Map "success" → "done" for display
            display_status = "done" if step.status == "success" else step.status
            step_phs[idx].markdown(
                _step_html(idx, step.name, PIPELINE_STEPS[idx][1], PIPELINE_STEPS[idx][2],
                           display_status, step.message, dur),
                unsafe_allow_html=True,
            )
            pct = (idx + 1) / len(PIPELINE_STEPS)
            prog_ph.markdown(
                f'<div class="prog-label">{idx+1} of {len(PIPELINE_STEPS)} steps complete</div>'
                f'<div class="prog-bar"><div class="prog-fill" style="width:{pct*100:.0f}%"></div></div>',
                unsafe_allow_html=True,
            )

    def on_substep(step_name: str, msg: str):
        status_ph.caption(f"↳ {msg}")

    _step_results: dict = {}
    _orig_complete = on_step_complete

    def on_step_complete_with_guard(step: PipelineStepResult):
        _step_results[step.name] = step
        _orig_complete(step)
        if step.name == "Chunk Documents" and step.status == "error":
            status_ph.warning(
                "⚠️ Chunk failed — no parsed docs found. "
                "Re-run **Parse Documents** (Step 3) then **Chunk Documents** (Step 4)."
            )

    pipeline_start = time.time()
    with st.spinner("Pipeline running — Signal Scoring can take 10+ minutes..."):
        result = client.run_pipeline(
            resolved,
            on_step_start=on_step_start,
            on_step_complete=on_step_complete_with_guard,
            on_substep=on_substep,
        )

    total_elapsed = time.time() - pipeline_start
    mins, secs = divmod(int(total_elapsed), 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    status_ph.empty()
    prog_ph.markdown(
        f'<div class="prog-label">{len(PIPELINE_STEPS)} of {len(PIPELINE_STEPS)} steps complete</div>'
        f'<div class="prog-bar"><div class="prog-fill" style="width:100%"></div></div>',
        unsafe_allow_html=True,
    )

    st.divider()
    if result.overall_status == "success":
        st.balloons()
        st.success(f"✅ Pipeline completed in **{time_str}**")
    elif result.overall_status == "partial":
        st.warning(f"⚠️ Pipeline completed with some issues in **{time_str}**")
    else:
        st.error(f"❌ Pipeline failed after **{time_str}**")

    # Show dimension scores
    _show_dimension_scores_inline(ticker)

    index_step = next(
        (s for s in (result.steps or []) if s.name == "Index Evidence" and s.status == "success"),
        None,
    )
    if index_step:
        indexed = index_step.data.get("indexed_count", 0)
        st.success(f"**{indexed} evidence vectors indexed** — Chatbot is now ready!")

    if st.button(f"💬 Start Chatbot for {ticker}", type="primary",
                 use_container_width=True, key="go_chat_post_pipe"):
        st.session_state["active_page"]     = "chatbot"
        st.session_state["chatbot_ticker"]  = ticker
        st.session_state["chatbot_company"] = resolved.name
        st.rerun()

    if result.steps:
        with st.expander("Step timing breakdown", expanded=False):
            for s in result.steps:
                icon = {"success": "✅", "error": "❌", "skipped": "⏭️"}.get(s.status, "⬜")
                st.markdown(f"{icon} **{s.name}** — {s.duration_seconds:.1f}s"
                            + (f"  \n`{s.error}`" if s.error else ""))