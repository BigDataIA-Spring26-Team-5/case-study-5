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
from evidence_display import render_company_evidence_panel  # noqa: E402

# ============================================================================
# Constants
# ============================================================================
BASE_URL = "http://localhost:8000"

CS3_PORTFOLIO = [
    {"ticker": "NVDA", "name": "NVIDIA Corporation",          "sector": "Technology"},
    {"ticker": "JPM",  "name": "JPMorgan Chase & Co.",        "sector": "Financial Services"},
    {"ticker": "WMT",  "name": "Walmart Inc.",                "sector": "Retail"},
    {"ticker": "GE",   "name": "GE Aerospace",                "sector": "Manufacturing"},
    {"ticker": "DG",   "name": "Dollar General Corporation",  "sector": "Retail"},
]

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
for _k, _v in {"selected_ticker": "NVDA", "workflow_ticker": "NVDA",
               "workflow_result": None}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ============================================================================
# Data helpers
# ============================================================================

@st.cache_data(ttl=300)
def _load_scores() -> list[dict]:
    rows = []
    for co in CS3_PORTFOLIO:
        ticker = co["ticker"]
        try:
            r = requests.get(f"{BASE_URL}/api/v1/assessments/{ticker}", timeout=15)
            if r.status_code == 200:
                d = r.json()
                rows.append({
                    "ticker":   ticker,
                    "name":     co["name"],
                    "sector":   co["sector"],
                    "org_air":  round(float(d.get("org_air_score", 0.0)), 2),
                    "vr_score": round(float(d.get("vr_score", 0.0)), 2),
                    "hr_score": round(float(d.get("hr_score", 0.0)), 2),
                    "synergy":  round(float(d.get("synergy_score", 0.0)), 2),
                    "tc":       round(float(d.get("talent_concentration", 0.0)), 2),
                    "pf":       round(float(d.get("position_factor", 0.0)), 2),
                })
            else:
                rows.append({**co, "org_air": 0.0, "vr_score": 0.0, "hr_score": 0.0,
                             "synergy": 0.0, "tc": 0.0, "pf": 0.0})
        except Exception:
            rows.append({**co, "org_air": 0.0, "vr_score": 0.0, "hr_score": 0.0,
                         "synergy": 0.0, "tc": 0.0, "pf": 0.0})
    return rows


# ============================================================================
# Page: Portfolio Overview
# ============================================================================
def page_portfolio() -> None:
    st.title("Portfolio Overview — Org-AI-R Intelligence")
    st.caption("Composite AI Readiness scores for the 5 CS3 portfolio companies.")

    with st.spinner("Loading scores..."):
        rows = _load_scores()
    df = pd.DataFrame(rows)

    scored   = df[df["org_air"] > 0]
    fund_air = round(scored["org_air"].mean(), 1) if not scored.empty else 0.0
    leaders  = int((df["org_air"] >= 70).sum())
    laggards = int((df["org_air"] < 50).sum())
    avg_vr   = round(scored["vr_score"].mean(), 1) if not scored.empty else 0.0
    avg_hr   = round(scored["hr_score"].mean(), 1) if not scored.empty else 0.0

    st.markdown("### Fund-Level Metrics")

    def _card(col, label, value, sub=""):
        col.markdown(
            f'<div class="cs5-metric-card">'
            f'<div class="cs5-metric-label">{label}</div>'
            f'<div class="cs5-metric-value">{value}</div>'
            f'<div class="cs5-metric-sub">{sub}</div>'
            f'</div>', unsafe_allow_html=True)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    _card(c1, "Fund-AI-R", f"{fund_air:.1f}", "portfolio avg")
    _card(c2, "Companies", str(len(df)),       "in portfolio")
    _card(c3, "Leaders",   str(leaders),        "score ≥ 70")
    _card(c4, "Laggards",  str(laggards),       "score < 50")
    _card(c5, "Avg V^R",   f"{avg_vr:.1f}",    "vertical AI")
    _card(c6, "Avg H^R",   f"{avg_hr:.1f}",    "human readiness")

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

    def _color(v: float) -> str:
        if v >= 80:   return "background-color:#dcfce7;color:#15803d;font-weight:700;"
        elif v >= 60: return "background-color:#fef9c3;color:#854d0e;"
        elif v > 0:   return "background-color:#fee2e2;color:#991b1b;"
        return ""

    st.dataframe(disp.style.map(_color, subset=["Org-AI-R"]),
                 use_container_width=True, hide_index=True)
    st.caption("Use **Evidence Analysis** in the sidebar to deep-dive into a company.")


# ============================================================================
# Page: Evidence Analysis
# ============================================================================
def page_evidence() -> None:
    st.title("Evidence Analysis")
    tickers = [co["ticker"] for co in CS3_PORTFOLIO]
    sel = st.session_state.get("selected_ticker", "NVDA")
    idx = tickers.index(sel) if sel in tickers else 0

    selected = st.sidebar.selectbox(
        "Company", tickers, index=idx,
        format_func=lambda t: f"{t} — {next(c['name'] for c in CS3_PORTFOLIO if c['ticker']==t)}",
        key="evidence_ticker_select",
    )
    st.session_state["selected_ticker"] = selected
    render_company_evidence_panel(selected)


# ============================================================================
# Page: Agentic Workflow
# ============================================================================
def page_workflow() -> None:
    st.title("Agentic Due-Diligence Workflow")
    st.caption(
        "Runs the full LangGraph supervisor → specialist agents pipeline. "
        "Triggers HITL approval automatically when thresholds are exceeded."
    )

    tickers = [co["ticker"] for co in CS3_PORTFOLIO]
    col_sel, col_type, col_run = st.columns([2, 2, 1])

    with col_sel:
        ticker = st.selectbox(
            "Company", tickers,
            format_func=lambda t: f"{t} — {next(c['name'] for c in CS3_PORTFOLIO if c['ticker']==t)}",
            index=tickers.index(st.session_state.get("workflow_ticker", "NVDA")),
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
# Navigation — called before any rendering to suppress Streamlit's
# auto-discovery of other .py files in the same directory.
# ============================================================================
pg = st.navigation(
    {
        "CS5 Agentic Portfolio": [
            st.Page(page_portfolio, title="Portfolio Overview", icon="📊", default=True),
            st.Page(page_evidence,  title="Evidence Analysis",  icon="🔍"),
            st.Page(page_workflow,  title="Agentic Workflow",   icon="🤖"),
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
    for co in CS3_PORTFOLIO:
        st.caption(f"{co['ticker']} — {co['name']}")
    st.divider()
    if st.button("Refresh Data", key="btn_refresh", use_container_width=True, type="secondary"):
        st.cache_data.clear()
        st.rerun()
    st.caption("PE Org-AI-R Platform · CS5")

pg.run()
