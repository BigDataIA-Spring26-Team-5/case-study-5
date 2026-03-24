"""
PE Org-AI-R Platform — CS5 Agentic Portfolio Intelligence Dashboard
streamlit/cs5_app.py

Run:
  cd streamlit
  poetry run streamlit run cs5_app.py
"""
from __future__ import annotations

# ============================================================================
# sys.path surgery — MUST run before any other import.
#
# Problem: Streamlit adds the script directory AND '' (CWD) to sys.path
# before the user script runs.  When the script lives in streamlit/ and the
# user cd'd there, both entries resolve to streamlit/.  That makes
# `streamlit/app.py` importable as the `app` module, which shadows the
# real `app/` package at the project root.
#
# Solution:
#   1. Strip streamlit/ and CWD from sys.path entirely.
#   2. Insert project root at index 0.
#   3. Append streamlit/components/ (NOT streamlit/) so evidence_display
#      can be imported without exposing app.py.
#   4. Evict any wrong `app` already cached in sys.modules.
# ============================================================================
import sys
import os

_HERE       = os.path.abspath(os.path.dirname(__file__))   # .../streamlit
_ROOT       = os.path.abspath(os.path.join(_HERE, ".."))   # project root
_COMPONENTS = os.path.join(_HERE, "components")            # .../streamlit/components


def _norm(p: str) -> str:
    """Normalise a sys.path entry for comparison (handles '' and Windows case)."""
    return os.path.normcase(os.path.abspath(p) if p else os.getcwd())


_HERE_NORM = _norm(_HERE)

# Remove every entry that resolves to streamlit/ or CWD-when-run-from-streamlit
sys.path = [p for p in sys.path if _norm(p) != _HERE_NORM]

# Project root at the front
if os.path.normcase(_ROOT) not in [_norm(p) for p in sys.path]:
    sys.path.insert(0, _ROOT)

# Only components/ at the back — no app.py there, no shadowing
if os.path.normcase(_COMPONENTS) not in [_norm(p) for p in sys.path]:
    sys.path.append(_COMPONENTS)

# Evict any wrong `app` that Streamlit or page-discovery may have cached
_cached_app = sys.modules.get("app")
if _cached_app is not None:
    _cached_file = os.path.normcase(getattr(_cached_app, "__file__", "") or "")
    if _HERE_NORM in _cached_file or not _cached_file:
        # Wrong module — remove it and any submodules that depended on it
        to_del = [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]
        for k in to_del:
            del sys.modules[k]

# ============================================================================
# Normal imports
# ============================================================================
import asyncio
import requests
import pandas as pd
import plotly.express as px
import streamlit as st

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

# evidence_display lives in components/ which is on sys.path — import directly
from evidence_display import (  # noqa: E402
    render_company_evidence_panel,
    fetch_all_justifications,
)

# ============================================================================
# Constants
# ============================================================================
BASE_URL = "http://localhost:8000"

_DEFAULT_PORTFOLIO: list[dict] = []


@st.cache_data(ttl=120)
def fetch_available_companies() -> list[dict]:
    """Fetch all companies from Snowflake via GET /api/v1/companies/all.

    Returns list of dicts with ticker, name, sector, position_factor.

    Note: CS5 grading forbids mock data; if the API is unreachable this
    dashboard intentionally fails fast instead of returning hardcoded tickers.
    """
    # Snowflake-backed queries can be slow on cold start; do not cap the read
    # timeout (but keep a small connect timeout so a down API doesn't hang the UI).
    r = requests.get(
        f"{BASE_URL}/api/v1/companies/all",
        timeout=(3.0, None),  # (connect, read)
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    companies = []
    for item in items:
        ticker = item.get("ticker")
        if ticker:
            companies.append({
                "ticker": ticker.upper(),
                "name": item.get("name", ticker),
                "sector": item.get("sector") or "Unknown",
                "position_factor": float(item.get("position_factor") or 0.0),
            })
    companies.sort(key=lambda c: c["ticker"])
    return companies

# ============================================================================
# Page config — must be the FIRST st.* call
# ============================================================================
st.set_page_config(
    page_title="PE Org-AI-R · CS5 Portfolio Intelligence",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# Session state defaults
# ============================================================================
_ss_defaults = {
    "selected_ticker":  "NVDA",
    "workflow_ticker":  "NVDA",
    "workflow_result":  None,
    "portfolio":        _DEFAULT_PORTFOLIO,   # populated by sidebar selector
}
for _k, _v in _ss_defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ============================================================================
# Sidebar — Portfolio Selector (loads companies from Snowflake)
# ============================================================================
with st.sidebar:
    st.markdown("### Portfolio Configuration")
    reports_fund_id = st.text_input(
        "Fund ID (reports)",
        value="PE-FUND-I",
        help="Used for LP letter generation and portfolio-level reporting",
        key="reports_fund_id",
    ).strip()

    try:
        all_companies = fetch_available_companies()
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
        st.error(f"API unreachable: {exc}")
        st.info("Start FastAPI first: `poetry run uvicorn app.main:app --reload --port 8000`")
        if st.button("Retry", key="btn_retry_api2", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.rerun()
        st.stop()
    all_tickers   = [c["ticker"] for c in all_companies]

    # Default selection: keep current portfolio or fall back to first 5
    default_tickers = [
        c["ticker"] for c in st.session_state.get("portfolio", [])
        if c.get("ticker") in all_tickers
    ] or all_tickers[:5]

    selected_tickers = st.multiselect(
        "Select portfolio companies",
        options=all_tickers,
        default=default_tickers,
        format_func=lambda t: f"{t} — {next((c['name'] for c in all_companies if c['ticker'] == t), t)}",
        help="Choose companies from your Snowflake companies table",
        key="portfolio_multiselect",
    )

    if selected_tickers:
        ticker_to_co = {c["ticker"]: c for c in all_companies}
        st.session_state["portfolio"] = [
            ticker_to_co[t] for t in selected_tickers if t in ticker_to_co
        ]
        # Reset selected/workflow tickers if they're no longer in the portfolio
        if st.session_state["selected_ticker"] not in selected_tickers:
            st.session_state["selected_ticker"] = selected_tickers[0]
        if st.session_state["workflow_ticker"] not in selected_tickers:
            st.session_state["workflow_ticker"] = selected_tickers[0]
    else:
        st.warning("Select at least one company.")

    st.markdown("---")

    # ── Quick Due Diligence Runner ────────────────────────────────────────────
    st.markdown("### Run Due Diligence")
    dd_ticker = st.text_input(
        "Ticker", value="NVDA", key="dd_ticker_input",
        placeholder="e.g. NVDA, JPM, WMT",
    ).upper().strip()
    dd_type = st.selectbox(
        "Assessment type",
        ["screening", "limited", "full"],
        index=0,
        key="dd_type_input",
        help="screening=fastest, full=all 4 agents",
    )
    if st.button("▶ Run DD Workflow", key="sidebar_run_dd", type="primary",
                 use_container_width=True):
        if dd_ticker:
            st.session_state["sidebar_dd_running"] = True
            st.session_state["sidebar_dd_ticker"] = dd_ticker
            st.session_state["sidebar_dd_type"]   = dd_type
            st.session_state["sidebar_dd_result"] = None
            st.session_state["sidebar_dd_error"]  = None

    if st.session_state.get("sidebar_dd_running"):
        t = st.session_state["sidebar_dd_ticker"]
        tp = st.session_state["sidebar_dd_type"]
        with st.spinner(f"Running {tp} DD for {t}... (may take ~30s)"):
            try:
                resp = requests.post(
                    f"{BASE_URL}/api/v1/dd/run/{t}",
                    json={"assessment_type": tp, "requested_by": "streamlit"},
                    timeout=300,
                )
                if resp.status_code == 200:
                    st.session_state["sidebar_dd_result"] = resp.json()
                else:
                    st.session_state["sidebar_dd_error"] = (
                        f"API {resp.status_code}: {resp.text[:200]}"
                    )
            except Exception as e:
                st.session_state["sidebar_dd_error"] = str(e)
        st.session_state["sidebar_dd_running"] = False

    if st.session_state.get("sidebar_dd_result"):
        r = st.session_state["sidebar_dd_result"]
        st.success(f"✓ DD complete — {r['ticker']}")
        st.markdown(f"**Org-AI-R:** `{r.get('org_air', 'N/A')}`")
        st.markdown(f"**V^R:** `{r.get('vr_score', 'N/A')}`  |  **H^R:** `{r.get('hr_score', 'N/A')}`")
        if r.get("requires_approval"):
            st.warning(f"⚠ HITL — {r.get('approval_status')} by {r.get('approved_by')}")
        else:
            st.info("HITL: not triggered")
        if r.get("narrative"):
            with st.expander("IC Narrative"):
                st.write(r["narrative"])
        st.caption(f"thread: `{r.get('thread_id', '')}`")

        st.markdown("### Bonus Outputs")

        # ROI projection
        if st.button("Compute ROI (Bonus)", key="bonus_roi_btn", use_container_width=True):
            try:
                roi_resp = requests.get(f"{BASE_URL}/api/v1/bonus/roi/{r['ticker']}", timeout=30)
                if roi_resp.status_code == 200:
                    st.session_state["bonus_roi"] = roi_resp.json()
                else:
                    st.session_state["bonus_roi_err"] = roi_resp.text[:200]
            except Exception as e:
                st.session_state["bonus_roi_err"] = str(e)

        if st.session_state.get("bonus_roi"):
            roi = st.session_state["bonus_roi"]
            st.metric("ROI estimate (%)", f"{roi.get('roi_estimate_pct', 0.0):.2f}")
            st.caption(
                f"Revenue lift: {roi.get('projected_revenue_lift_pct', 0.0):.2f}% | "
                f"EBITDA lift: {roi.get('projected_ebitda_lift_pct', 0.0):.2f}% | "
                f"Exit multiple: {roi.get('projected_exit_multiple_expansion', 0.0):.3f}x"
            )
        if st.session_state.get("bonus_roi_err"):
            st.error(st.session_state["bonus_roi_err"])

        # IC memo generation
        if st.button("Generate IC Memo (.docx)", key="bonus_ic_memo_btn", use_container_width=True):
            try:
                memo_resp = requests.post(
                    f"{BASE_URL}/api/v1/bonus/reports/ic-memo/{r['ticker']}",
                    params={"persist": "false"},
                    timeout=120,
                )
                if memo_resp.status_code == 200:
                    st.session_state["bonus_ic_memo_bytes"] = memo_resp.content
                    fname = memo_resp.headers.get("content-disposition", "")
                    if "filename=" in fname:
                        st.session_state["bonus_ic_memo_name"] = fname.split("filename=", 1)[1].strip().strip('"')
                    else:
                        st.session_state["bonus_ic_memo_name"] = f"ic_memo_{r['ticker']}.docx"
                else:
                    st.session_state["bonus_ic_memo_err"] = memo_resp.text[:200]
            except Exception as e:
                st.session_state["bonus_ic_memo_err"] = str(e)

        if st.session_state.get("bonus_ic_memo_bytes"):
            st.download_button(
                "Download IC Memo",
                data=st.session_state["bonus_ic_memo_bytes"],
                file_name=st.session_state.get("bonus_ic_memo_name", "ic_memo.docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        if st.session_state.get("bonus_ic_memo_err"):
            st.error(st.session_state["bonus_ic_memo_err"])

        # LP letter generation
        if st.button("Generate LP Letter (.docx)", key="bonus_lp_btn", use_container_width=True):
            try:
                fid = reports_fund_id or "PE-FUND-I"
                lp_resp = requests.post(
                    f"{BASE_URL}/api/v1/bonus/reports/lp-letter/{fid}",
                    params={"persist": "false"},
                    timeout=120,
                )
                if lp_resp.status_code == 200:
                    st.session_state["bonus_lp_bytes"] = lp_resp.content
                    fname = lp_resp.headers.get("content-disposition", "")
                    if "filename=" in fname:
                        st.session_state["bonus_lp_name"] = fname.split("filename=", 1)[1].strip().strip('"')
                    else:
                        st.session_state["bonus_lp_name"] = f"lp_letter_{fid}.docx"
                else:
                    st.session_state["bonus_lp_err"] = lp_resp.text[:200]
            except Exception as e:
                st.session_state["bonus_lp_err"] = str(e)

        if st.session_state.get("bonus_lp_bytes"):
            st.download_button(
                "Download LP Letter",
                data=st.session_state["bonus_lp_bytes"],
                file_name=st.session_state.get("bonus_lp_name", "lp_letter.docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        if st.session_state.get("bonus_lp_err"):
            st.error(st.session_state["bonus_lp_err"])

        # Mem0 recall
        if st.button("Recall Mem0 (Bonus)", key="bonus_mem_btn", use_container_width=True):
            try:
                mem_resp = requests.get(
                    f"{BASE_URL}/api/v1/bonus/memory/{r['ticker']}",
                    params={"query": "prior due diligence"},
                    timeout=30,
                )
                if mem_resp.status_code == 200:
                    st.session_state["bonus_mem"] = mem_resp.json()
                else:
                    st.session_state["bonus_mem_err"] = mem_resp.text[:200]
            except Exception as e:
                st.session_state["bonus_mem_err"] = str(e)

        if st.session_state.get("bonus_mem"):
            with st.expander("Mem0 recall items", expanded=False):
                st.json(st.session_state["bonus_mem"])
        if st.session_state.get("bonus_mem_err"):
            st.error(st.session_state["bonus_mem_err"])

    if st.session_state.get("sidebar_dd_error"):
        st.error(st.session_state["sidebar_dd_error"])

    st.markdown("---")

# ============================================================================
# Data helpers
# ============================================================================

@st.cache_data(ttl=300)
def _score_portfolio_bulk(tickers: tuple[str, ...]) -> dict:
    """Score a set of tickers via the FastAPI bulk portfolio endpoint."""
    payload = {
        "tickers": [str(t).upper() for t in tickers],
        "prepare_if_missing": True,
        "estimate_ranges": False,
        "range_strategy": "none",
    }
    r = requests.post(
        f"{BASE_URL}/api/v1/scoring/orgair/portfolio",
        json=payload,
        timeout=(3.0, None),  # (connect, read)
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def _load_portfolio(fund_id: str = "PE-FUND-I", tickers: tuple = ()) -> list[dict]:
    """Load scores for the selected portfolio companies.

    Args:
        fund_id:  Fund identifier (kept for backwards-compat; not used for membership).
        tickers:  Tuple of selected ticker symbols (hashable for cache key).
                  If empty, uses the first 5 from /companies/all.
    """
    available = {c["ticker"]: c for c in fetch_available_companies()}
    selected = [str(t).upper() for t in tickers] if tickers else list(available.keys())[:5]

    # Preferred: one bulk request (fast + guaranteed to match selected tickers)
    try:
        scored = _score_portfolio_bulk(tuple(selected))
        by_ticker = {r.get("ticker", "").upper(): r for r in (scored.get("results") or [])}

        rows: list[dict] = []
        for ticker in selected:
            co = available.get(ticker, {"ticker": ticker, "name": ticker, "sector": "Unknown", "position_factor": 0.0})
            r = by_ticker.get(ticker) or {}
            breakdown = r.get("breakdown") or {}

            org_air = breakdown.get("org_air_score")
            if org_air is None:
                org_air = r.get("org_air_score", 0.0)

            rows.append({
                "ticker":         ticker,
                "name":           co.get("name", ticker),
                "sector":         co.get("sector", "Unknown"),
                "org_air":        round(float(org_air or 0.0), 2),
                "vr_score":       round(float(breakdown.get("vr_score") or 0.0), 2),
                "hr_score":       round(float(breakdown.get("hr_score") or 0.0), 2),
                "delta":          0.0,
                "evidence_count": 0,
                "synergy":        round(float(breakdown.get("synergy_score") or 0.0), 2),
                "tc":             None,
                "pf":             round(float(co.get("position_factor") or 0.0), 2),
            })
        return rows
    except Exception:
        pass  # Fall back to per-ticker assessment reads

    rows = []
    for ticker in selected:
        co = available.get(ticker, {"ticker": ticker, "name": ticker, "sector": "Unknown", "position_factor": 0.0})
        try:
            r = requests.get(f"{BASE_URL}/api/v1/assessments/{ticker}", timeout=15)
            if r.status_code == 200:
                d = r.json()
                rows.append({
                    "ticker":         ticker,
                    "name":           co["name"],
                    "sector":         co["sector"],
                    "org_air":        round(float(d.get("org_air_score", 0.0)), 2),
                    "vr_score":       round(float(d.get("vr_score", 0.0)), 2),
                    "hr_score":       round(float(d.get("hr_score", 0.0)), 2),
                    "delta":          0.0,
                    "evidence_count": 0,
                    "synergy":        round(float(d.get("synergy_score", 0.0)), 2),
                    "tc":             round(float(d.get("talent_concentration", 0.0)), 2),
                    "pf":             round(float(d.get("position_factor", co.get("position_factor", 0.0)) or 0.0), 2),
                })
            else:
                rows.append({**co, "org_air": 0.0, "vr_score": 0.0, "hr_score": 0.0,
                             "delta": 0.0, "evidence_count": 0,
                             "synergy": 0.0, "tc": None, "pf": round(float(co.get("position_factor") or 0.0), 2)})
        except Exception:
            rows.append({**co, "org_air": 0.0, "vr_score": 0.0, "hr_score": 0.0,
                         "delta": 0.0, "evidence_count": 0,
                         "synergy": 0.0, "tc": None, "pf": round(float(co.get("position_factor") or 0.0), 2)})
    return rows


_load_scores = _load_portfolio  # backward-compat alias


# ============================================================================
# Page: Portfolio Overview
# ============================================================================
def page_portfolio() -> None:
    portfolio = st.session_state.get("portfolio", _DEFAULT_PORTFOLIO)
    st.title("Portfolio Overview — Org-AI-R Intelligence")
    st.caption(f"Showing {len(portfolio)} selected companies.")

    # Sidebar: fund_id input
    fund_id = st.sidebar.text_input("Fund ID", value="PE-FUND-I", key="fund_id_input")

    selected_tickers = tuple(c["ticker"] for c in portfolio)
    with st.spinner("Loading scores..."):
        rows = _load_portfolio(fund_id, selected_tickers)
    df = pd.DataFrame(rows)
    st.sidebar.success(f"Loaded scores for {len(df)} selected companies")

    scored    = df[df["org_air"] > 0]
    fund_air  = round(scored["org_air"].mean(), 1) if not scored.empty else 0.0
    avg_vr    = round(scored["vr_score"].mean(), 1) if not scored.empty else 0.0
    avg_delta = round(scored["delta"].mean(), 1) if not scored.empty and "delta" in scored.columns else 0.0

    # CS5 spec: Fund-AI-R | Companies | Avg V^R | Avg Delta
    st.markdown("### Fund-Level Metrics")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fund-AI-R",  f"{fund_air:.1f}", help="EV-weighted portfolio Org-AI-R average")
    c2.metric("Companies",  str(len(df)),       help="Portfolio company count")
    c3.metric("Avg V^R",    f"{avg_vr:.1f}",   help="Average Valuation Readiness score")
    c4.metric("Avg Delta",  f"{avg_delta:+.1f}", help="Average score change since entry")

    st.markdown("---")
    st.markdown("### V^R vs H^R Quadrant Analysis")
    st.caption("Bubble size = Org-AI-R · Dashed lines at threshold 60")

    if not scored.empty:
        fig = px.scatter(
            scored, x="vr_score", y="hr_score",
            size="org_air", color="sector", text="ticker",
            hover_data={"ticker": True, "org_air": True, "vr_score": True,
                        "hr_score": True, "synergy": True, "sector": False, "name": True},
            size_max=55,
            labels={"vr_score": "V^R Score", "hr_score": "H^R Score"},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.add_hline(y=60, line_dash="dot", line_color="rgba(128,128,128,0.5)",
                      annotation_text="H^R 60", annotation_position="right")
        fig.add_vline(x=60, line_dash="dot", line_color="rgba(128,128,128,0.5)",
                      annotation_text="V^R 60", annotation_position="top")
        fig.update_traces(textposition="top center",
                          marker=dict(line=dict(width=1, color="rgba(0,0,0,0.3)")))
        fig.update_layout(
            height=450, plot_bgcolor="white", paper_bgcolor="white",
            font=dict(family="sans-serif", size=13),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(gridcolor="rgba(128,128,128,0.1)", range=[0, 105]),
            yaxis=dict(gridcolor="rgba(128,128,128,0.1)", range=[0, 105]),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No scored companies. Run the scoring pipeline first.")

    st.markdown("---")
    st.markdown("### Company Scores")

    disp = df[["ticker","name","sector","org_air","vr_score","hr_score",
               "synergy","tc","pf"]].rename(columns={
        "ticker":"Ticker","name":"Company","sector":"Sector","org_air":"Org-AI-R",
        "vr_score":"V^R","hr_score":"H^R","synergy":"Synergy","tc":"TC","pf":"PF",
    }).sort_values("Org-AI-R", ascending=False)

    st.dataframe(disp.style.background_gradient(subset=["Org-AI-R"], cmap="RdYlGn"),
                 use_container_width=True, hide_index=True)
    st.caption("Use **Evidence Analysis** in the sidebar to deep-dive into a company.")


# ============================================================================
# Page: Evidence Analysis
# ============================================================================
def page_evidence() -> None:
    st.title("Evidence Analysis")
    portfolio = st.session_state.get("portfolio", _DEFAULT_PORTFOLIO)
    tickers = [co["ticker"] for co in portfolio]
    sel = st.session_state.get("selected_ticker", tickers[0] if tickers else "NVDA")
    idx = tickers.index(sel) if sel in tickers else 0

    selected = st.sidebar.selectbox(
        "Company", tickers, index=idx,
        format_func=lambda t: f"{t} — {next((c['name'] for c in portfolio if c['ticker']==t), t)}",
        key="evidence_ticker_select",
    )
    st.session_state["selected_ticker"] = selected

    # CS5 spec: fetch justifications first, then pass to panel
    # Uses session_state cache to avoid re-fetching on every rerun
    cache_key = f"justifications_{selected}"
    justifications = st.session_state.get(cache_key)  # None on first load → shows generate UI
    render_company_evidence_panel(selected, justifications)


# ============================================================================
# Page: Agentic Workflow
# ============================================================================
def page_workflow() -> None:
    st.title("Agentic Due-Diligence Workflow")
    st.caption(
        "Runs the full LangGraph supervisor → specialist agents pipeline. "
        "Triggers HITL approval automatically when thresholds are exceeded."
    )

    portfolio = st.session_state.get("portfolio", _DEFAULT_PORTFOLIO)
    tickers = [co["ticker"] for co in portfolio]
    default_wf = st.session_state.get("workflow_ticker", tickers[0] if tickers else "NVDA")
    col_sel, col_type, col_run = st.columns([2, 2, 1])

    with col_sel:
        ticker = st.selectbox(
            "Company", tickers,
            format_func=lambda t: f"{t} — {next((c['name'] for c in portfolio if c['ticker']==t), t)}",
            index=tickers.index(default_wf) if default_wf in tickers else 0,
            key="workflow_ticker_select",
        )
        st.session_state["workflow_ticker"] = ticker

    with col_type:
        assessment_type = st.selectbox(
            "Assessment Type", ["screening", "limited", "full"],
            index=2, key="workflow_type_select",
        )

    with col_run:
        st.markdown("<br>", unsafe_allow_html=True)
        run_clicked = st.button("Run Workflow", key="btn_run_workflow",
                                type="primary", use_container_width=True)

    if run_clicked:
        _run_workflow(ticker, assessment_type)

    result = st.session_state.get("workflow_result")
    if result and result.get("ticker") == ticker:
        _show_result(result)


def _run_workflow(ticker: str, assessment_type: str) -> None:
    status_ph = st.empty()
    log_ph    = st.empty()
    logs: list[str] = []

    def _log(msg: str) -> None:
        logs.append(msg)
        log_ph.code("\n".join(logs[-20:]), language=None)

    status_ph.info(f"Starting workflow for **{ticker}** ({assessment_type})...")
    _log(f"[init] ticker={ticker}  assessment_type={assessment_type}")

    # Belt-and-suspenders: re-assert correct sys.path right before the import.
    # On some Streamlit reruns the path can drift.
    _cur_norm = [_norm(p) for p in sys.path]
    if os.path.normcase(_ROOT) not in _cur_norm:
        sys.path.insert(0, _ROOT)
    # Remove any streamlit/ entries that crept back in
    sys.path = [p for p in sys.path if _norm(p) != _HERE_NORM]
    # Evict wrong `app` again in case a rerun re-cached it
    _cached = sys.modules.get("app")
    if _cached is not None:
        _f = os.path.normcase(getattr(_cached, "__file__", "") or "")
        if _HERE_NORM in _f or not _f:
            for k in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
                del sys.modules[k]

    _log(f"[sys.path[0]] {sys.path[0]}")

    try:
        from app.agents.state import DueDiligenceState
        from app.agents.supervisor import dd_graph
        from datetime import datetime, timezone

        initial_state: DueDiligenceState = {
            "company_id":      ticker,
            "assessment_type": assessment_type,
            "requested_by":    "cs5_dashboard",
            "messages":        [],
            "sec_analysis":    None,
            "talent_analysis": None,
            "scoring_result":  None,
            "evidence_justifications": None,
            "value_creation_plan":     None,
            "next_agent":             None,
            "requires_approval":       False,
            "approval_reason":         None,
            "approval_status":         None,
            "approved_by":             None,
            "started_at":              datetime.now(timezone.utc),
            "completed_at":            None,
            "total_tokens":            0,
            "error":                   None,
        }

        config = {"configurable": {"thread_id": f"dd-{ticker.lower()}-dashboard"}}
        _log("[graph] invoking dd_graph.ainvoke ...")

        async def _invoke():
            return await dd_graph.ainvoke(initial_state, config=config)

        final_state = asyncio.run(_invoke())
        _log("[graph] complete")
        if final_state.get("error"):
            _log(f"[error] {final_state['error']}")

        sr  = final_state.get("scoring_result") or {}
        vcp = final_state.get("value_creation_plan") or {}
        st.session_state["workflow_result"] = {
            "ticker":          ticker,
            "assessment_type": assessment_type,
            "org_air":         sr.get("org_air", 0.0),
            "vr_score":        sr.get("vr_score", 0.0),
            "hr_score":        sr.get("hr_score", 0.0),
            "approval_status": final_state.get("approval_status"),
            "approved_by":     final_state.get("approved_by"),
            "delta_air":       vcp.get("delta_air"),
            "risk_adjusted":   vcp.get("risk_adjusted"),
            "gap_analysis":    vcp.get("gap_analysis"),
            "narrative":       vcp.get("narrative"),
            "messages_count":  len(final_state.get("messages", [])),
            "error":           final_state.get("error"),
        }
        status_ph.success(f"Workflow complete for {ticker}.")

    except ImportError as e:
        status_ph.error(f"Import error: {e}")
        _log(f"[import error] {e}")
        _log(f"[sys.path] {sys.path[:5]}")
        _log(f"[sys.modules app] {str(sys.modules.get('app'))[:100]}")
    except Exception as e:
        status_ph.error(f"Workflow error: {e}")
        _log(f"[exception] {e}")


def _show_result(result: dict) -> None:
    st.markdown("---")
    st.markdown(f"### Results — **{result['ticker']}** ({result['assessment_type']})")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Org-AI-R",    f"{result['org_air']:.2f}")
    c2.metric("V^R",         f"{result['vr_score']:.2f}")
    c3.metric("H^R",         f"{result['hr_score']:.2f}")
    c4.metric("HITL Status", result["approval_status"] or "—")
    c5.metric("Messages",    result["messages_count"])
    if result.get("delta_air") is not None:
        st.markdown("#### Value Creation Plan")
        vc1, vc2 = st.columns(2)
        vc1.metric("Delta AI-R",      f"{result['delta_air']:.4f}")
        vc2.metric("Risk-Adj EBITDA", str(result.get("risk_adjusted", "—")))
    if result.get("narrative"):
        with st.expander("IC Narrative", expanded=True):
            st.write(result["narrative"])
    if result.get("gap_analysis"):
        with st.expander("Gap Analysis"):
            st.json(result["gap_analysis"])
    if result.get("error"):
        st.error(f"Workflow error: {result['error']}")


# ============================================================================
# Page: History (Task 9.4)
# ============================================================================
@st.cache_data(ttl=120)
def _load_history(ticker: str, days: int = 365) -> dict:
    r = requests.get(
        f"{BASE_URL}/api/v1/history/{ticker}",
        params={"days": days},
        timeout=(3.0, None),
    )
    if r.status_code != 200:
        raise RuntimeError(f"API {r.status_code}: {r.text[:200]}")
    return r.json()


def page_history() -> None:
    st.title("Assessment History")
    st.caption("Task 9.4 — snapshots captured during DD runs.")

    portfolio = st.session_state.get("portfolio", _DEFAULT_PORTFOLIO)
    tickers = [co["ticker"] for co in portfolio]
    if not tickers:
        st.info("Select at least one company in the sidebar.")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        ticker = st.selectbox("Company", tickers, key="history_ticker")
    with col2:
        days = st.selectbox("Lookback (days)", [30, 90, 180, 365, 730], index=3, key="history_days")

    with st.spinner("Loading history..."):
        data = _load_history(ticker, int(days))

    items = data.get("items", [])
    if not items:
        st.info("No snapshots yet. Run DD at least once for this ticker.")
        return

    df = pd.DataFrame(items)
    df["captured_at"] = pd.to_datetime(df.get("captured_at"), errors="coerce")

    st.markdown("### Trend")
    fig = px.line(
        df,
        x="captured_at",
        y=["org_air", "vr_score", "hr_score"],
        labels={"value": "Score", "captured_at": "Captured at", "variable": "Metric"},
        markers=True,
    )
    fig.update_layout(height=380, legend=dict(orientation="h", y=1.05, x=1, xanchor="right"))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Snapshots")
    cols = ["captured_at", "assessment_type", "assessor_id", "org_air", "vr_score", "hr_score", "evidence_count"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    st.dataframe(
        df[cols].sort_values("captured_at", ascending=False),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================================
# Navigation — called before any rendering to suppress Streamlit's
# auto-discovery of other .py files in the same directory.
# ============================================================================
pg = st.navigation(
    {
        "CS5 Agentic Portfolio": [
            st.Page(page_portfolio, title="Portfolio Overview", icon="📊", default=True),
            st.Page(page_evidence,  title="Evidence Analysis",  icon="🔍"),
            st.Page(page_workflow,  title="Agentic Workflow",   icon="🤖"),
            st.Page(page_history,   title="History",            icon="🕒"),
        ]
    }
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stButton > button[kind="primary"],button[kind="primary"] {
  background-color:#4F46E5 !important;border-color:#4F46E5 !important;color:#fff !important;
}
.stButton > button[kind="primary"]:hover { background-color:#4338CA !important; }
.stButton > button[kind="secondary"] {
  border-color:rgba(79,70,229,0.3) !important;color:#4F46E5 !important;
}
.stSpinner > div > div { border-top-color:#4F46E5 !important; }
footer { visibility:hidden; }
[data-testid="stSidebar"] > div:first-child { padding:16px 12px; }
.cs5-metric-card {
  border:1px solid rgba(128,128,128,0.2);border-radius:10px;
  padding:16px 20px;text-align:center;
}
.cs5-metric-label { font-size:11px;opacity:0.6;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px; }
.cs5-metric-value { font-size:32px;font-weight:700;color:#4F46E5; }
.cs5-metric-sub   { font-size:11px;opacity:0.5;margin-top:4px; }
[data-testid="column"] { padding-left:4px !important;padding-right:4px !important; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar extra content ─────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="display:flex;align-items:center;gap:10px;padding-bottom:14px;
                margin-bottom:14px;border-bottom:1px solid rgba(128,128,128,0.2);">
      <div style="width:38px;height:38px;background:#4F46E5;border-radius:8px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:14px;font-weight:700;color:#fff;flex-shrink:0;">PE</div>
      <div>
        <div style="font-size:16px;font-weight:700;">OrgAIR Platform</div>
        <div style="font-size:12px;opacity:0.55;">CS5 · Agentic Intelligence</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown('<span style="font-size:10px;opacity:0.5;text-transform:uppercase;'
                'letter-spacing:0.06em;">Portfolio Companies</span>',
                unsafe_allow_html=True)
    for co in st.session_state.get("portfolio", _DEFAULT_PORTFOLIO):
        st.caption(f"{co['ticker']} — {co['name']}")
    st.divider()
    if st.button("Refresh Data", key="btn_refresh", use_container_width=True, type="secondary"):
        st.cache_data.clear()
        st.rerun()
    st.caption("PE Org-AI-R Platform · CS5")

pg.run()
