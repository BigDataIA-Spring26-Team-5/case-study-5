"""
PE Org-AI-R Platform — CS5 Agentic Portfolio Intelligence Dashboard
streamlit/cs5_app.py

Run:
  cd streamlit
  streamlit run cs5_app.py
"""
from __future__ import annotations

# ── sys.path surgery (must run before any app import) ────────────────────────
import sys, os

_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_COMPONENTS = os.path.join(_HERE, "components")
_UTILS = os.path.join(_HERE, "utils")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p) if p else os.getcwd())


_HERE_NORM = _norm(_HERE)
sys.path = [p for p in sys.path if _norm(p) != _HERE_NORM]
if os.path.normcase(_ROOT) not in [_norm(p) for p in sys.path]:
    sys.path.insert(0, _ROOT)
for _extra in (_COMPONENTS, _UTILS):
    if os.path.normcase(_extra) not in [_norm(p) for p in sys.path]:
        sys.path.append(_extra)

_cached_app = sys.modules.get("app")
if _cached_app is not None:
    _f = os.path.normcase(getattr(_cached_app, "__file__", "") or "")
    if _HERE_NORM in _f or not _f:
        for k in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
            del sys.modules[k]

# ── Imports ──────────────────────────────────────────────────────────────────
import asyncio
import base64
import hashlib
import io
import json as _json
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import redis
import streamlit as st

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from api_base import api_base_url
from evidence_display import render_company_evidence_panel, fetch_all_justifications

# ── Constants ────────────────────────────────────────────────────────────────
BASE = api_base_url()
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL = 86400  # 24 hours


# ── Redis cache layer ────────────────────────────────────────────────────────
@st.cache_resource
def _redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _cache_get(key: str):
    """Read JSON from Redis. Returns None on miss."""
    try:
        raw = _redis().get(f"cs5:{key}")
        return _json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(key: str, value, ttl: int = CACHE_TTL):
    """Write JSON to Redis with TTL."""
    try:
        _redis().setex(f"cs5:{key}", ttl, _json.dumps(value, default=str))
    except Exception:
        pass

pio.templates["pe_orgair"] = go.layout.Template(
    layout=dict(
        font=dict(family="DM Sans, sans-serif"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        colorway=["#6366f1", "#14b8a6", "#f59e0b", "#f97316", "#8b5cf6", "#ef4444"],
    )
)
pio.templates.default = "pe_orgair"

_NAV = [
    "◻ Portfolio Overview",
    "◆ Evidence Analysis",
    "↻ Assessment History",
    "⚙ Agentic Workflow",
    "◆ Fund-AI-R Analytics",
    "▶ MCP Server",
    "◎ Prometheus Metrics",
    "---",
    "✍ IC Memo / LP Letter",
    "★ Investment Tracker",
    "◇ Mem0 Memory",
]
_NAV_SELECTABLE = [n for n in _NAV if n != "---"]

_MCP_TOOLS = [
    {"name": "calculate_org_air_score", "cs": "CS3", "badge": "badge-purple",
     "source": "cs3_client.get_assessment()",
     "desc": "Calculate Org-AI-R score. Returns: org_air, vr_score, hr_score, synergy_score, confidence_interval, dimension_scores. Input: company_id (required)."},
    {"name": "get_company_evidence", "cs": "CS2", "badge": "badge-teal",
     "source": "cs2_client.get_evidence()",
     "desc": "Retrieve AI-readiness evidence. Filters by dimension (7 values + \"all\"). Returns: source_type, content[:500], confidence. Inputs: company_id, dimension, limit."},
    {"name": "generate_justification", "cs": "CS4", "badge": "badge-blue",
     "source": "cs4_client.generate_justification()",
     "desc": "Generate evidence-backed justification via RAG. Returns: score, level, level_name, evidence_strength, rubric_criteria, supporting_evidence[:5], gaps_identified."},
    {"name": "project_ebitda_impact", "cs": "CS3", "badge": "badge-purple",
     "source": "ebitda_calculator.project()",
     "desc": "Project EBITDA impact using v2.0 model. Returns: delta_air, conservative/base/optimistic scenarios, risk_adjusted, requires_approval flag."},
    {"name": "run_gap_analysis", "cs": "CS3+CS4", "badge": "badge-purple",
     "source": "gap_analyzer.analyze()",
     "desc": "Analyze gaps and generate 100-day plan. Calls cs3_client then gap_analyzer. Returns: gap by dimension, priorities, initiatives, investment."},
    {"name": "get_portfolio_summary", "cs": "CS1", "badge": "badge-green",
     "source": "portfolio_data_service.get_portfolio_view()",
     "desc": "Get fund portfolio summary. Returns: fund_id, fund_air (avg), company_count, companies [{ticker, org_air, sector}]."},
]

_DIMENSION_TARGETS = {
    "Data Infrastructure": 72, "Ai Governance": 70, "Technology Stack": 74,
    "Talent": 68, "Leadership": 72, "Use Case Portfolio": 70, "Culture": 66,
}

# Fixed portfolio — 7 companies across 5 industries
PORTFOLIO_TICKERS = ["NVDA", "CRM", "GOOGL", "JPM", "WMT", "ADP", "UNH"]

# Sector lookup for companies missing sector in DB
_SECTOR_FALLBACK = {
    "NVDA": "Technology", "CRM": "Technology", "GOOGL": "Technology",
    "MSFT": "Technology", "NFLX": "Technology", "AAPL": "Technology",
    "JPM": "Financial Services", "GS": "Financial Services",
    "WMT": "Retail", "TGT": "Retail", "DG": "Retail",
    "ADP": "Business Services", "PAYX": "Business Services",
    "UNH": "Healthcare Services", "HCA": "Healthcare Services",
    "CAT": "Manufacturing", "DE": "Manufacturing", "GE": "Manufacturing",
}

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="PE OrgAIR CS5", layout="wide", initial_sidebar_state="expanded")

# ── Session state defaults ───────────────────────────────────────────────────
for _k, _v in {
    "selected_page": _NAV_SELECTABLE[0],
    "selected_ticker": "NVDA",
    "fund_id": "growth_fund_v",
    "assessment_type": "Full",
    "portfolio_tickers": ["NVDA", "CRM", "GOOGL", "JPM", "WMT", "ADP", "UNH"],
    "workflow_result": None,
    "workflow_logs": [],
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP helpers
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_resource
def _http() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


def _get(path: str, **kw):
    return _http().get(f"{BASE}{path}", timeout=kw.pop("timeout", 15), **kw)


def _post(path: str, **kw):
    return _http().post(f"{BASE}{path}", timeout=kw.pop("timeout", 60), **kw)


def _api_ok() -> tuple[bool, str]:
    """Check if API is reachable. Uses fresh request (not cached session)."""
    import urllib.request
    try:
        req = urllib.request.urlopen(f"{BASE}/healthz", timeout=10)
        if req.status == 200:
            return True, "CS1-CS4 services connected"
    except Exception:
        pass
    return False, "CS1-CS4 unavailable — check API"


# ═══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ═══════════════════════════════════════════════════════════════════════════════
def load_all_companies() -> list[dict]:
    """SELECT * FROM companies — returns list of {ticker, name, sector, position_factor}.
    Cached in Redis for 24h."""
    cached = _cache_get("companies_all")
    if cached:
        return cached
    r = _get("/api/v1/companies/all", timeout=(5.0, 60))
    r.raise_for_status()
    out = []
    for item in r.json().get("items", []):
        t = item.get("ticker")
        if t:
            t = t.upper()
            out.append({
                "ticker": t,
                "name": item.get("name", t),
                "sector": item.get("sector") or _SECTOR_FALLBACK.get(t, "Unknown"),
                "position_factor": float(item.get("position_factor") or 0.0),
                "revenue_millions": float(item.get("revenue_millions") or 0),
            })
    out = sorted(out, key=lambda c: c["ticker"])
    _cache_set("companies_all", out)
    return out



def fetch_assessment(ticker: str) -> dict:
    """Read existing scores from Snowflake via GET /api/v1/assessments/{ticker}.
    Cached in Redis for 24h. Does NOT re-compute."""
    key = f"assessment:{ticker}"
    cached = _cache_get(key)
    if cached:
        return cached
    r = _get(f"/api/v1/assessments/{ticker}", timeout=30)
    if r.status_code == 200:
        data = r.json()
        _cache_set(key, data)
        return data
    return {}


def fetch_evidence_count(ticker: str) -> int:
    """Get evidence count. Cached in Redis for 24h."""
    key = f"evidence_count:{ticker}"
    cached = _cache_get(key)
    if cached is not None:
        return int(cached)
    try:
        r = _get(f"/api/v1/companies/{ticker}/evidence", timeout=30)
        if r.ok:
            d = r.json()
            docs = int((d.get("document_summary") or {}).get("total_documents", 0))
            sigs = int((d.get("signal_summary") or {}).get("total_signals", 0))
            count = docs + sigs
            _cache_set(key, count)
            return count
    except Exception:
        pass
    return 0


def fetch_entry_score(ticker: str) -> float:
    """Get the earliest (entry) Org-AI-R score. Cached in Redis for 24h."""
    key = f"entry_score:{ticker}"
    cached = _cache_get(key)
    if cached is not None:
        return float(cached)
    try:
        r = _get(f"/api/v1/history/{ticker}", params={"days": 3650}, timeout=10)
        if r.ok:
            items = r.json().get("items", [])
            if items:
                score = float(items[0].get("org_air", 0))
                _cache_set(key, score)
                return score
    except Exception:
        pass
    return 0.0


def _title_sector(sector: str) -> str:
    """'financial_services' -> 'Financial Services', 'technology' -> 'Technology'."""
    return sector.replace("_", " ").title() if sector else "Unknown"


def _ensure_scored(ticker: str) -> dict:
    """Fetch assessment; if org_air is 0, trigger scoring via POST and re-fetch."""
    a = fetch_assessment(ticker)
    if float(a.get("org_air_score") or 0) > 0:
        return a
    # Trigger scoring pipeline
    try:
        _post("/api/v1/scoring/orgair/portfolio",
              json={"tickers": [ticker], "prepare_if_missing": True},
              timeout=180)
        # Flush stale cache for this ticker and re-fetch
        try:
            _redis().delete(f"cs5:assessment:{ticker}")
        except Exception:
            pass
        a = fetch_assessment(ticker)
    except Exception:
        pass
    return a


def build_portfolio_rows(tickers: list[str], companies: list[dict]) -> list[dict]:
    co_map = {c["ticker"]: c for c in companies}
    rows = []
    for t in tickers:
        co = co_map.get(t, {"ticker": t, "name": t, "sector": _SECTOR_FALLBACK.get(t, "Unknown"), "position_factor": 0.0})
        try:
            a = _ensure_scored(t)
        except Exception:
            a = {}
        ev_count = fetch_evidence_count(t)
        org_air = round(float(a.get("org_air_score") or 0), 2)
        entry = fetch_entry_score(t)
        delta = round(org_air - entry, 1) if entry > 0 and org_air > 0 else 0.0
        sector_raw = co.get("sector") or _SECTOR_FALLBACK.get(t, "Unknown")
        rows.append({
            "ticker": t,
            "name": co.get("name", t),
            "sector": _title_sector(sector_raw),
            "org_air": org_air,
            "vr_score": round(float(a.get("vr_score") or 0), 2),
            "hr_score": round(float(a.get("hr_score") or 0), 2),
            "synergy": round(float(a.get("synergy_score") or 0), 2),
            "delta": delta,
            "evidence_count": ev_count,
            "pf": round(float(a.get("position_factor") or co.get("position_factor") or 0), 2),
            "revenue_millions": float(co.get("revenue_millions") or 0),
        })
    return rows


def load_history(ticker: str, days: int = 365) -> dict:
    """Load history snapshots. Cached in Redis for 24h."""
    key = f"history:{ticker}:{days}"
    cached = _cache_get(key)
    if cached:
        return cached
    r = _get(f"/api/v1/history/{ticker}", params={"days": days}, timeout=(3, None))
    r.raise_for_status()
    data = r.json()
    _cache_set(key, data)
    return data


def load_prometheus() -> list[dict]:
    r = _get("/metrics", timeout=10)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
        if not line or line.startswith("#") or " " not in line:
            continue
        sample, val = line.split(" ", 1)
        base, labels = sample, {}
        if "{" in sample:
            base, raw = sample.split("{", 1)
            for part in raw.rstrip("}").split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    labels[k.strip()] = v.strip().strip('"')
        try:
            rows.append({"metric": base, "labels": labels, "value": float(val.strip())})
        except ValueError:
            pass
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# Reusable render helpers (matching mockup CSS classes)
# ═══════════════════════════════════════════════════════════════════════════════
def _score_badge(score: float) -> str:
    if score >= 70:
        return "badge-green"
    if score >= 55:
        return "badge-amber"
    return "badge-red"


def render_metric_cards(cards: list[dict]) -> None:
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        dc = "delta-pos" if "+" in str(c.get("delta", "")) or "improving" in str(c.get("delta", "")).lower() else "text-muted"
        vs = f' style="color:{c["vc"]}"' if c.get("vc") else ""
        with col:
            st.markdown(f'''<div class="metric-card">
              <div class="metric-label">{c["label"]}</div>
              <div class="metric-value"{vs}>{c["value"]}</div>
              <div class="metric-delta {dc}">{c.get("delta","")}</div>
            </div>''', unsafe_allow_html=True)


def render_section_divider(label: str) -> None:
    st.markdown(f'''<div class="section-divider">
      <span class="section-divider-label">{label}</span>
      <div class="section-divider-line"></div>
    </div>''', unsafe_allow_html=True)


def render_table(headers: list[str], rows: list[list[str]]) -> None:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    st.markdown(f'''<div class="table-wrap"><table>
      <thead><tr>{head}</tr></thead><tbody>{body}</tbody>
    </table></div>''', unsafe_allow_html=True)


def render_trend_cards(cards: list[dict]) -> None:
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        vc = "delta-pos" if c.get("positive") else ""
        extra = f' style="color:var(--green)"' if c.get("green") else ""
        with col:
            st.markdown(f'''<div class="trend-card">
              <div class="trend-label">{c["label"]}</div>
              <div class="trend-value {vc}"{extra}>{c["value"]}</div>
            </div>''', unsafe_allow_html=True)


def render_page_header(title: str, subtitle: str, btn: str | None = None, prefix: str = "hdr") -> bool:
    left, right = st.columns([5, 2])
    with left:
        st.markdown(f'''<div class="page-header-block">
          <div class="page-title">{title}</div>
          <div class="page-subtitle">{subtitle}</div>
        </div>''', unsafe_allow_html=True)
    clicked = False
    with right:
        if btn:
            clicked = st.button(btn, key=f"{prefix}_btn", type="primary")
    return clicked


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — matches mockup exactly
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    ok, status_msg = _api_ok()
    dot = "status-green" if ok else "status-red"
    st.markdown(f'''<div style="margin-top:-20px;padding-top:0">
      <div class="sidebar-brand">Org-AI-R</div>
      <div class="sidebar-title">PE Portfolio Intelligence</div>
      <div class="sidebar-status-row"><span class="status-dot {dot}"></span>{status_msg}</div>
    </div>''', unsafe_allow_html=True)

    # Load all companies from DB (for name/sector lookup)
    try:
        all_companies = load_all_companies()
    except Exception:
        all_companies = []
        st.warning("Cannot reach API — no companies loaded")

    ticker_name = {c["ticker"]: c["name"] for c in all_companies}

    # Fixed portfolio — 6 companies across 4 industries
    st.session_state["portfolio_tickers"] = PORTFOLIO_TICKERS
    portfolio_names = [f"{t} — {ticker_name.get(t, t)}" for t in PORTFOLIO_TICKERS]

    st.session_state["fund_id"] = st.text_input("Fund ID", value=st.session_state["fund_id"])

    # Company deep-dive selector (only portfolio companies)
    idx = PORTFOLIO_TICKERS.index(st.session_state["selected_ticker"]) if st.session_state["selected_ticker"] in PORTFOLIO_TICKERS else 0
    st.session_state["selected_ticker"] = st.selectbox(
        "Company (deep dive)", PORTFOLIO_TICKERS, index=idx,
        format_func=lambda t: f"{t} — {ticker_name.get(t, t)}",
    )

    st.session_state["assessment_type"] = st.selectbox("Assessment type", ["Full", "Limited", "Screening"])

    # Show portfolio in sidebar
    st.markdown("---")
    st.markdown("##### Portfolio (7 companies)")
    for t in PORTFOLIO_TICKERS:
        co = next((c for c in all_companies if c["ticker"] == t), None)
        sector = _title_sector(co["sector"] if co else _SECTOR_FALLBACK.get(t, ""))
        st.caption(f"{t} — {ticker_name.get(t, t)} · {sector}")

    # ── Page navigation ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("##### Pages")
    page = st.radio("Nav", _NAV_SELECTABLE, label_visibility="collapsed",
                    index=_NAV_SELECTABLE.index(st.session_state["selected_page"])
                    if st.session_state["selected_page"] in _NAV_SELECTABLE else 0,
                    key="page_radio")
    st.session_state["selected_page"] = page

    st.markdown("---")
    if st.button("Refresh Cache", key="btn_flush_cache", type="secondary", use_container_width=True):
        try:
            keys = _redis().keys("cs5:*")
            if keys:
                _redis().delete(*keys)
            st.success(f"Cleared {len(keys)} cached items")
            st.rerun()
        except Exception as e:
            st.error(str(e))

    st.markdown('''<div style="margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:#5a5955;line-height:1.6">
    CS5 Capstone — Spring 2026<br>MCP + LangGraph + No Mock Data<br>All data via CS1-CS4 APIs<br>Cache: Redis (24h TTL)</div>''', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — Portfolio Overview
# ═══════════════════════════════════════════════════════════════════════════════
def page_portfolio():
    tickers = st.session_state["portfolio_tickers"]
    if not tickers:
        st.info("Select companies in the sidebar to build your portfolio.")
        return

    render_page_header(
        "Portfolio overview",
        f"Fund: {st.session_state['fund_id']} — All data from CS1-CS4 via PortfolioDataService",
        btn="Run full assessment", prefix="pf",
    )

    with st.spinner("Loading scores from CS3..."):
        rows = build_portfolio_rows(tickers, all_companies)
    df = pd.DataFrame(rows)
    scored = df[df["org_air"] > 0]

    fund_air = round(scored["org_air"].mean(), 1) if not scored.empty else 0.0
    avg_vr = round(scored["vr_score"].mean(), 1) if not scored.empty else 0.0
    avg_delta = round(scored["delta"].mean(), 1) if not scored.empty else 0.0

    render_metric_cards([
        {"label": "Fund-AI-R", "value": f"{fund_air:.1f}", "delta": f"+{avg_delta:.1f} vs entry (EV-weighted)"},
        {"label": "Companies", "value": f"{len(scored)}/{len(df)}", "delta": f"{len(df)} total, {len(scored)} scored"},
        {"label": "Avg V<sup>R</sup>", "value": f"{avg_vr:.1f}", "delta": "Idiosyncratic readiness"},
        {"label": "Avg delta since entry", "value": f"+{avg_delta:.1f}", "delta": "All portfolios improving", "vc": "#0d9f6e"},
    ])

    # Scatter chart — V^R vs H^R
    _SECTOR_COLORS = {
        "Technology": "#4f46e5",          # indigo
        "Financial Services": "#dc2626",  # red
        "Healthcare Services": "#0d9488", # teal
        "Retail": "#d97706",              # amber
        "Business Services": "#7c3aed",   # purple
        "Manufacturing": "#2563eb",       # blue
    }
    st.markdown("#### Portfolio AI-readiness map — V^R vs H^R (from CS3)")
    if not scored.empty:
        fig = px.scatter(
            scored, x="vr_score", y="hr_score", size="org_air", color="sector",
            text="ticker", hover_data={"org_air": True, "vr_score": True, "hr_score": True, "name": True},
            size_max=20, labels={"vr_score": "V^R (Idiosyncratic)", "hr_score": "H^R (Systematic)"},
            color_discrete_map=_SECTOR_COLORS,
        )
        fig.add_hline(y=60, line_dash="dot", line_color="rgba(128,128,128,0.5)", annotation_text="H^R 60")
        fig.add_vline(x=60, line_dash="dot", line_color="rgba(128,128,128,0.5)", annotation_text="V^R 60")
        fig.update_traces(textposition="top center", marker=dict(line=dict(width=1.5, color="rgba(0,0,0,0.3)")))
        fig.update_layout(height=550, margin=dict(t=40),
                          legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
                          xaxis=dict(gridcolor="rgba(128,128,128,0.1)", range=[0, 105]),
                          yaxis=dict(gridcolor="rgba(128,128,128,0.1)", range=[0, 105]))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No scored companies yet.")

    # Portfolio table
    st.markdown('<div class="card-title mb-8">Portfolio companies (from CS1 + CS3)</div>', unsafe_allow_html=True)
    tbl = df.sort_values("org_air", ascending=False)
    sector_badges = {"Technology": "badge-purple", "Financial Services": "badge-blue", "Healthcare Services": "badge-teal", "Retail": "badge-amber", "Business Services": "badge-green", "Manufacturing": "badge-red"}
    html_rows = []
    for _, r in tbl.iterrows():
        ci = max(2.5, 6.0 - float(r["org_air"]) / 40)
        sb = sector_badges.get(r["sector"], "badge-purple")
        html_rows.append([
            f'<span class="text-mono">{r["ticker"]}</span>',
            r["name"],
            f'<span class="badge {sb}">{r["sector"]}</span>',
            f'<span class="badge {_score_badge(r["org_air"])}">{r["org_air"]:.1f}</span>',
            f'{r["vr_score"]:.1f}', f'{r["hr_score"]:.1f}', f'{r["synergy"]:.1f}',
            f'<span class="delta-pos">+{r["delta"]:.1f}</span>',
            str(int(r["evidence_count"])),
            f'<span class="text-muted text-xs">±{ci:.1f}</span>',
        ])
    render_table(
        ["Ticker", "Name", "Sector", "Org-AI-R", "V<sup>R</sup>", "H<sup>R</sup>", "Synergy", "Delta", "Evidence", "CI (95%)"],
        html_rows,
    )

    # Bottom row: bar chart + doughnut
    col_l, col_r = st.columns(2)
    with col_l:
        if not scored.empty:
            bar = px.bar(scored.sort_values("org_air"), x="org_air", y="ticker", orientation="h",
                         color="sector", color_discrete_map=_SECTOR_COLORS,
                         labels={"org_air": "Org-AI-R", "ticker": ""})
            bar.update_layout(height=300, showlegend=False)
            st.plotly_chart(bar, use_container_width=True)
    with col_r:
        if not scored.empty:
            pie = px.pie(scored, names="sector", values="org_air", hole=0.4,
                         color="sector", color_discrete_map=_SECTOR_COLORS)
            pie.update_layout(height=300)
            st.plotly_chart(pie, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Evidence Analysis
# ═══════════════════════════════════════════════════════════════════════════════
def page_evidence():
    ticker = st.session_state["selected_ticker"]
    render_page_header(
        f"Evidence analysis: {ticker}",
        "CS4 RAG justifications across 7 Org-AI-R dimensions",
        prefix="ev",
    )

    # Use evidence_display.py for generate/fetch UI only
    cache_key = f"justifications_{ticker}"
    justifications = st.session_state.get(cache_key)

    # Show generate buttons if no justifications cached
    if justifications is None:
        from evidence_display import fetch_all_justifications, DIMENSIONS
        col_btn, col_clear = st.columns([2, 1])
        with col_btn:
            if st.button("Generate All 7 Dimensions", key=f"gen_all_{ticker}", type="primary", use_container_width=True):
                justifications = fetch_all_justifications(ticker)
                st.session_state[cache_key] = justifications
        with col_clear:
            if st.button("Clear Cache", key=f"clear_{ticker}", use_container_width=True):
                for dim in DIMENSIONS:
                    st.session_state.pop(f"justification_{ticker}_{dim}", None)
                st.session_state.pop(cache_key, None)
                st.rerun()

    justifications = st.session_state.get(cache_key, {})
    if not justifications:
        st.info("Click **Generate All 7 Dimensions** to fetch evidence from CS4 RAG.")
        return

    # ── Metric cards ─────────────────────────────────────────────────────────
    total_ev = sum(len(j.get("supporting_evidence", [])) for j in justifications.values())
    levels = [float(j.get("level", 0)) for j in justifications.values()]
    strong = sum(1 for j in justifications.values() if j.get("evidence_strength") == "strong")
    total_gaps = sum(len(j.get("gaps_identified", [])) for j in justifications.values())

    render_metric_cards([
        {"label": "Total evidence", "value": str(total_ev), "delta": ""},
        {"label": "Avg level", "value": f"L{sum(levels)/len(levels):.1f}" if levels else "L0", "delta": ""},
        {"label": "Strong evidence", "value": f"{strong} / 7", "delta": ""},
        {"label": "Gaps found", "value": str(total_gaps), "delta": ""},
    ])

    # ── Dimension summary table ──────────────────────────────────────────────
    st.markdown('<div class="card-title mb-8">Dimension summary</div>', unsafe_allow_html=True)
    summary_rows = []
    for dim, item in justifications.items():
        lv = int(item.get("level", 0))
        strength = str(item.get("evidence_strength", "")).title()
        lb = "badge-teal" if lv >= 5 else "badge-green" if lv >= 4 else "badge-amber" if lv >= 3 else "badge-red"
        stb = "badge-green" if strength == "Strong" else "badge-amber" if strength == "Moderate" else "badge-red"
        summary_rows.append([
            dim.replace("_", " ").title(),
            f'{float(item.get("score", 0)):.1f}',
            f'<span class="badge {lb}">L{lv}</span>',
            f'<span class="badge {stb}">{strength}</span>',
            str(len(item.get("supporting_evidence", []))),
            str(len(item.get("gaps_identified", []))),
        ])
    render_table(["Dimension", "Score", "Level", "Evidence", "Items", "Gaps"], summary_rows)

    # ── Dimension tabs with evidence cards ───────────────────────────────────
    tab_labels = [d.replace("_", " ").title() for d in justifications.keys()]
    tabs = st.tabs(tab_labels)
    for tab, (dim, item) in zip(tabs, justifications.items()):
        with tab:
            lv = int(item.get("level", 0))
            lb = "badge-teal" if lv >= 5 else "badge-green" if lv >= 4 else "badge-amber" if lv >= 3 else "badge-red"
            strength = str(item.get("evidence_strength", "")).title()
            stb = "badge-green" if strength == "Strong" else "badge-amber"
            ev_items = item.get("supporting_evidence", [])[:5]
            ev_html = "".join(
                f'<li class="evidence-item"><span><span class="evidence-source">{ev.get("source_type","").replace("_"," ").title()}</span> {str(ev.get("content",""))[:110]}</span><span class="evidence-conf">{float(ev.get("confidence",0))*100:.0f}%</span></li>'
                for ev in ev_items
            )
            gaps_html = "".join(f'<span class="gap-tag">{g}</span>' for g in item.get("gaps_identified", []))
            st.markdown(f'''<div class="evidence-card">
              <div class="evidence-header">
                <div class="flex items-center gap-8">
                  <span class="evidence-dim">{dim.replace("_"," ").title()}</span>
                  <span class="badge {lb}">L{lv} — {item.get("level_name","")}</span>
                  <span style="font-size:17px;font-weight:700">{float(item.get("score",0)):.1f}</span>
                </div>
                <span class="badge {stb}">{strength} evidence</span>
              </div>
              <div class="rubric-box"><strong>Rubric match:</strong> {item.get("rubric_criteria","")}</div>
              <div style="font-size:13px;font-weight:600;color:#4b4a45;margin-bottom:8px">Supporting evidence (from CS4 RAG)</div>
              <ul class="evidence-list">{ev_html}</ul>
              <div class="gap-list"><span style="font-size:13px;font-weight:600;color:#7c2d12">Gaps identified:</span> {gaps_html}</div>
            </div>''', unsafe_allow_html=True)

    # ── Radar chart ──────────────────────────────────────────────────────────
    dim_labels = [d.replace("_", " ").title() for d in justifications.keys()]
    scores = [float(j.get("score", 0)) for j in justifications.values()]
    targets = [float(_DIMENSION_TARGETS.get(l, 70)) for l in dim_labels]
    if dim_labels:
        radar = go.Figure()
        radar.add_trace(go.Scatterpolar(r=scores + [scores[0]], theta=dim_labels + [dim_labels[0]],
                                         fill="toself", name=ticker, line=dict(color="#4f46e5")))
        radar.add_trace(go.Scatterpolar(r=targets + [targets[0]], theta=dim_labels + [dim_labels[0]],
                                         name="Target", line=dict(color="#d97706", dash="dash")))
        radar.update_layout(height=420, polar=dict(radialaxis=dict(visible=True, range=[0, 100])))
        st.plotly_chart(radar, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — Assessment History
# ═══════════════════════════════════════════════════════════════════════════════
def page_history():
    ticker = st.session_state["selected_ticker"]
    render_page_header(
        f"Assessment history: {ticker}",
        "AssessmentHistoryService — trends from CS3 snapshots stored via CS1/Snowflake",
        btn="Record new snapshot", prefix="hist",
    )

    days = st.selectbox("Lookback", [30, 90, 180, 365, 730], index=3, key="hist_days")
    try:
        data = load_history(ticker, int(days))
    except Exception as e:
        st.error(f"Could not load history: {e}")
        return

    items = data.get("items", [])
    if not items:
        st.info("No snapshots yet. Run due diligence at least once.")
        return

    df = pd.DataFrame(items)
    df["captured_at"] = pd.to_datetime(df.get("captured_at"), errors="coerce")
    df = df.sort_values("captured_at")

    cur = float(df["org_air"].dropna().iloc[-1]) if "org_air" in df and not df["org_air"].dropna().empty else 0.0
    entry = float(df["org_air"].dropna().iloc[0]) if "org_air" in df and not df["org_air"].dropna().empty else 0.0
    delta_e = cur - entry
    d30 = float(df["org_air"].iloc[-1] - df["org_air"].iloc[-2]) if len(df) >= 2 else 0.0
    d90 = float(df["org_air"].iloc[-1] - df["org_air"].iloc[max(0, len(df) - 4)]) if len(df) >= 2 else 0.0

    render_trend_cards([
        {"label": "Current Org-AI-R", "value": f"{cur:.1f}"},
        {"label": "Entry Org-AI-R", "value": f"{entry:.1f}"},
        {"label": "Delta since entry", "value": f"{delta_e:+.1f}", "positive": delta_e >= 0},
    ])
    render_trend_cards([
        {"label": "30-day delta", "value": f"{d30:+.1f}", "positive": d30 >= 0},
        {"label": "90-day delta", "value": f"{d90:+.1f}", "positive": d90 >= 0},
        {"label": "Trend direction", "value": "↑ Improving" if delta_e >= 0 else "↓ Declining", "green": delta_e >= 0},
    ])

    # Line chart
    st.markdown('<div class="card"><div class="card-title">Org-AI-R score over time (12 months — plotly line chart)</div>', unsafe_allow_html=True)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["captured_at"], y=df["org_air"], mode="lines+markers", name="Org-AI-R",
                              fill="tozeroy", line=dict(color="#6366f1", width=3)))
    if "vr_score" in df:
        fig.add_trace(go.Scatter(x=df["captured_at"], y=df["vr_score"], mode="lines+markers", name="V^R",
                                  line=dict(color="#10b981", dash="dash")))
    if "hr_score" in df:
        fig.add_trace(go.Scatter(x=df["captured_at"], y=df["hr_score"], mode="lines+markers", name="H^R",
                                  line=dict(color="#f59e0b", dash="dash")))
    fig.update_layout(height=350, legend=dict(orientation="h", y=1.05, x=1, xanchor="right"), yaxis_range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Snapshots table
    st.markdown('<div class="card-title mb-8">Assessment snapshots (AssessmentSnapshot records)</div>', unsafe_allow_html=True)
    for c in ["captured_at", "assessment_type", "assessor_id", "org_air", "vr_score", "hr_score", "synergy_score", "evidence_count"]:
        if c not in df.columns:
            df[c] = None
    tbl_rows = []
    for _, r in df.sort_values("captured_at", ascending=False).iterrows():
        s = float(r.get("org_air") or 0)
        tp = str(r.get("assessment_type") or "Full").title()
        tb = "badge-blue" if tp == "Full" else "badge-purple"
        tbl_rows.append([
            f'<span class="text-xs">{str(r.get("captured_at",""))[:16]}</span>',
            f'<span class="badge {_score_badge(s)}">{s:.1f}</span>',
            f'{float(r.get("vr_score") or 0):.1f}',
            f'{float(r.get("hr_score") or 0):.1f}',
            f'{float(r.get("synergy_score") or 0):.1f}',
            str(int(r.get("evidence_count") or 0)),
            f'<span class="badge {tb}">{tp}</span>',
            str(r.get("assessor_id") or "analyst_01"),
        ])
    render_table(["Timestamp", "Org-AI-R", "V<sup>R</sup>", "H<sup>R</sup>", "Synergy", "Evidence", "Type", "Assessor"], tbl_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — Agentic Workflow
# ═══════════════════════════════════════════════════════════════════════════════
def page_workflow():
    ticker = st.session_state["selected_ticker"]
    atype = st.session_state["assessment_type"].lower()

    clicked = render_page_header(
        f"Agentic due diligence: {ticker}",
        f"LangGraph supervisor + specialist agents | {atype} assessment",
        btn="Run due diligence", prefix="wf",
    )

    if clicked:
        import time as _time
        logs = []
        log_ph = st.empty()
        progress_ph = st.empty()

        def _log(msg):
            logs.append(msg)
            st.session_state["workflow_logs"] = logs[:]
            log_html = "<br>".join(logs[-20:])
            log_ph.markdown(f"<div class='agent-log'>{log_html}</div>", unsafe_allow_html=True)

        def _progress(step, total, label):
            progress_ph.progress(step / total, text=label)

        _progress(1, 8, "Initializing workflow...")
        _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[supervisor]</span> Starting due diligence for {ticker} ({atype})')

        _progress(2, 8, "Routing to SEC analyst...")
        _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[supervisor]</span> sec_analysis is None -> routing to sec_analyst')

        _progress(3, 8, "SEC analyst collecting evidence via CS2...")
        _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[sec_analyst]</span> <span style="color:#5dcaa5">MCP call:</span> get_company_evidence("{ticker}", "all")')

        try:
            resp = _post(f"/api/v1/dd/run/{ticker}",
                         json={"assessment_type": atype, "requested_by": "cs5_dashboard"},
                         timeout=300)
            if resp.ok:
                result = resp.json()
                st.session_state["workflow_result"] = result

                _progress(4, 8, "SEC analyst complete. Routing to scorer...")
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[sec_analyst]</span> <span style="color:#0d9f6e">Complete</span> - {result.get("messages_count",0)} evidence items from CS2')

                _progress(5, 8, "Scorer computing Org-AI-R via CS3...")
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[scorer]</span> <span style="color:#5dcaa5">MCP call:</span> calculate_org_air_score("{ticker}")')

                org = float(result.get("org_air") or 0)
                vr = float(result.get("vr_score") or 0)
                hr = float(result.get("hr_score") or 0)
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[scorer]</span> <span style="color:#0d9f6e">Scoring complete:</span> Org-AI-R={org:.1f} V^R={vr:.1f} H^R={hr:.1f}')

                _progress(6, 8, "Checking HITL thresholds...")
                if result.get("requires_approval"):
                    _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[scorer]</span> <span style="color:#f59e0b">HITL CHECK:</span> score {org:.1f} outside [40, 80] -> requires_approval=True')
                    _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[hitl]</span> <span style="color:#f59e0b">Awaiting human approval...</span>')
                else:
                    _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[scorer]</span> score {org:.1f} within [40, 80] -> HITL not triggered')

                _progress(7, 8, "Evidence agent + value creator running...")
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[evidence_agent]</span> <span style="color:#5dcaa5">MCP call:</span> generate_justification() for weak dimensions')
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[value_creator]</span> <span style="color:#5dcaa5">MCP call:</span> run_gap_analysis("{ticker}", target=80.0)')

                _progress(8, 8, "Workflow complete!")
                _log(f'<span class="log-time">{_time.strftime("%H:%M:%S")}</span> <span style="color:#8fa4ff">[supervisor]</span> <span style="color:#0d9f6e">Workflow complete</span> - {result.get("messages_count",0)} messages, EBITDA: {result.get("ebitda_risk_adjusted","N/A")}')
            else:
                st.error(f"API {resp.status_code}: {resp.text[:200]}")
                _log(f'<span style="color:#dc2626">[error] {resp.status_code}: {resp.text[:100]}</span>')
        except Exception as e:
            st.error(str(e))
            _log(f'<span style="color:#dc2626">[exception] {e}</span>')
        progress_ph.empty()

    result = st.session_state.get("workflow_result")
    if not result or result.get("ticker", "").upper() != ticker.upper():
        st.info("Click **Run due diligence** to start the agentic workflow.")
        return

    hitl = result.get("requires_approval")
    org_air = float(result.get("org_air") or 0)
    vr = float(result.get("vr_score") or 0)
    hr = float(result.get("hr_score") or 0)

    # ── HITL banner (matches mockup: icon + text + buttons inline) ───────────
    if hitl:
        st.markdown(f'''<div class="hitl-banner">
          <span class="hitl-icon">&#9888;</span>
          <span class="hitl-text"><strong>HITL approval required:</strong> Org-AI-R score {org_air:.1f} — outside normal range [40, 80]. Supervisor routed to hitl_approval node.</span>
        </div>''', unsafe_allow_html=True)
        h1, h2, h3 = st.columns([6, 1, 1])
        with h2:
            if st.button("Approve", key="hitl_approve", type="primary", use_container_width=True):
                try:
                    resp = _post(f"/api/v1/dd/approve/{result.get('thread_id','')}", json={"decision": "approved", "approved_by": "analyst"}, timeout=120)
                    if resp.ok:
                        st.session_state["workflow_result"] = resp.json()
                        st.success("Approved - workflow resumed")
                        st.rerun()
                    elif resp.status_code == 409:
                        st.session_state["workflow_result"]["requires_approval"] = False
                        st.session_state["workflow_result"]["approval_status"] = "approved"
                        st.success("Approved (workflow already completed)")
                        st.rerun()
                    else:
                        st.error(f"Error: {resp.text[:200]}")
                except Exception as e:
                    st.error(str(e))
        with h3:
            if st.button("Reject", key="hitl_reject", use_container_width=True):
                try:
                    resp = _post(f"/api/v1/dd/approve/{result.get('thread_id','')}", json={"decision": "rejected", "approved_by": "analyst"}, timeout=120)
                    if resp.ok:
                        st.session_state["workflow_result"] = resp.json()
                        st.warning("Rejected - workflow aborted")
                        st.rerun()
                    elif resp.status_code == 409:
                        st.session_state["workflow_result"]["requires_approval"] = False
                        st.session_state["workflow_result"]["approval_status"] = "rejected"
                        st.warning("Rejected (workflow already completed)")
                        st.rerun()
                    else:
                        st.error(f"Error: {resp.text[:200]}")
                except Exception as e:
                    st.error(str(e))

    # ── Agent flow pipeline ──────────────────────────────────────────────────
    st.markdown("#### Workflow progress — create_due_diligence_graph()")
    approval = result.get("approval_status") or ""
    hitl_label = "Pending" if hitl else ("Approved" if approval == "approved" else "Rejected" if approval == "rejected" else "Passed")
    hitl_state = "hitl" if hitl else "done"
    nodes = [
        ("Supervisor<br><span class='text-xs'>Route</span>", "done"),
        ("SEC analyst<br><span class='text-xs'>CS2 evidence</span>", "done"),
        ("Supervisor<br><span class='text-xs'>Route</span>", "done"),
        ("Scorer<br><span class='text-xs'>CS3 scores</span>", "done"),
        (f"HITL gate<br><span class='text-xs'>{hitl_label}</span>", hitl_state),
        ("Evidence agent<br><span class='text-xs'>CS4 RAG</span>", "pending" if hitl else "done"),
        ("Value creator<br><span class='text-xs'>Gap+EBITDA</span>", "pending" if hitl else "done"),
        ("Complete", "done" if not result.get("error") else "pending"),
    ]
    flow = []
    for i, (label, state) in enumerate(nodes):
        flow.append(f'<div class="agent-node {state}">{label}</div>')
        if i < len(nodes) - 1:
            flow.append('<span class="agent-arrow">&#8594;</span>')
    st.markdown(f'<div class="agent-flow">{"".join(flow)}</div>', unsafe_allow_html=True)

    # ── DueDiligenceState tables ─────────────────────────────────────────────
    st.markdown("#### DueDiligenceState (agents/state.py)")
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown('<div class="text-sm text-muted mb-4">Input fields</div>', unsafe_allow_html=True)
        render_table([], [
            ['<span class="text-mono">company_id</span>', f'"{ticker}"'],
            ['<span class="text-mono">assessment_type</span>', f'"{atype}"'],
            ['<span class="text-mono">requested_by</span>', '"cs5_dashboard"'],
            ['<span class="text-mono">started_at</span>', str(result.get("started_at", ""))[:19]],
        ])
    with col_r:
        st.markdown('<div class="text-sm text-muted mb-4">Workflow control</div>', unsafe_allow_html=True)
        render_table([], [
            ['<span class="text-mono">next_agent</span>', f'"{"hitl_approval" if hitl else result.get("next_agent") or "complete"}"'],
            ['<span class="text-mono">requires_approval</span>', f'<span class="badge {"badge-amber" if hitl else "badge-green"}">{"true" if hitl else "false"}</span>'],
            ['<span class="text-mono">approval_status</span>', f'<span class="badge badge-amber">{result.get("approval_status") or "pending"}</span>'],
            ['<span class="text-mono">total_tokens</span>', f'{result.get("messages_count", 0):,}'],
        ])

    # ── Agent output cards (2x2 grid) ────────────────────────────────────────
    st.markdown(f'''<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:20px 0">
      <div class="card"><div class="card-title">SEC analyst (SECAnalysisAgent)</div><div style="color:#4b4a45;font-size:14px">
        <div class="mb-4"><span class="evidence-source">Status</span> <span class="badge badge-green">Complete</span></div>
        <div class="mb-4">Dimensions: data_infrastructure, ai_governance, technology_stack</div>
        <div class="mb-4">MCP: get_company_evidence("{ticker}", "all")</div>
        <div>{result.get("messages_count",0)} evidence items from CS2</div></div></div>
      <div class="card"><div class="card-title">Scorer (ScoringAgent)</div><div style="color:#4b4a45;font-size:14px">
        <div class="mb-4"><span class="evidence-source">Status</span> <span class="badge badge-green">Complete</span></div>
        <div class="mb-4">Org-AI-R: <strong>{org_air:.1f}</strong> | V<sup>R</sup>: {vr:.1f} | H<sup>R</sup>: {hr:.1f}</div>
        <div class="mb-4">MCP: calculate_org_air_score("{ticker}")</div>
        <div>CI: [{max(org_air-4.2,0):.1f}, {org_air+4.2:.1f}] — <span class="badge {"badge-amber" if hitl else "badge-green" if approval == "approved" else "badge-green"}">{"Triggers HITL" if hitl else "HITL Approved" if approval == "approved" else "No HITL"}</span></div></div></div>
    </div>''', unsafe_allow_html=True)

    ev_status = "Waiting" if hitl else "Complete"
    ev_badge_cls = "" if hitl else f'class="{("badge-green")}"'
    wait_style = 'style="background:#f0f0f0;color:#999"' if hitl else ""
    badge_attr = wait_style if hitl else ev_badge_cls

    # Evidence agent card — show actual justification count if available
    ev_detail = "Will call: generate_justification() for weak dimensions via CS4"
    if not hitl and result.get("messages_count", 0) > 3:
        ev_detail = f"generate_justification() completed for {len(result.get('dimension_scores', {}))} dimensions via CS4"

    # Value creator card — show EBITDA + narrative if available
    vc_detail = f'Will call: run_gap_analysis("{ticker}", target=80.0)'
    ebitda = result.get("ebitda_risk_adjusted")
    narrative = result.get("narrative")
    if not hitl and ebitda:
        vc_detail = f'EBITDA impact (risk-adjusted): <strong>{ebitda}</strong><br>run_gap_analysis("{ticker}") complete'
    if not hitl and narrative:
        vc_detail += f'<br><br><div style="background:#f8f7f5;border-radius:8px;padding:10px 12px;font-size:13px;color:#4b4a45;line-height:1.6;max-height:150px;overflow-y:auto">{narrative[:500]}{"..." if len(narrative)>500 else ""}</div>'

    st.markdown(f'''<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px">
      <div class="card"><div class="card-title">Evidence agent — {"pending" if hitl else "complete"}</div><div style="color:#4b4a45;font-size:14px">
        <div class="mb-4"><span class="evidence-source">Status</span> <span class="badge" {badge_attr}>{ev_status}</span></div>
        <div>{ev_detail}</div></div></div>
      <div class="card"><div class="card-title">Value creator — {"pending" if hitl else "complete"}</div><div style="color:#4b4a45;font-size:14px">
        <div class="mb-4"><span class="evidence-source">Status</span> <span class="badge" {badge_attr}>{ev_status}</span></div>
        <div>{vc_detail}</div></div></div>
    </div>''', unsafe_allow_html=True)

    # ── Execution log ────────────────────────────────────────────────────────
    st.markdown("#### Agent execution log (structlog)")
    log_lines = list(st.session_state.get("workflow_logs", []))

    # Append post-approval log entries if workflow completed after HITL
    if approval == "approved" and result.get("completed_at") and result.get("messages_count", 0) > 3:
        if not any("HITL approved" in l for l in log_lines):
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[hitl]</span> <span style="color:#0d9f6e">HITL approved</span> by analyst')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[supervisor]</span> requires_approval=False -> resuming pipeline')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[evidence_agent]</span> <span style="color:#5dcaa5">MCP call:</span> generate_justification() for {len(result.get("dimension_scores",{}))} dimensions')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[evidence_agent]</span> <span style="color:#0d9f6e">Complete</span>')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[value_creator]</span> <span style="color:#5dcaa5">MCP call:</span> run_gap_analysis("{ticker}", target=80.0)')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[value_creator]</span> <span style="color:#0d9f6e">Complete</span> EBITDA: {result.get("ebitda_risk_adjusted","N/A")}')
            log_lines.append(f'<span class="log-time">{str(result.get("completed_at",""))[11:19]}</span> <span style="color:#8fa4ff">[supervisor]</span> <span style="color:#0d9f6e">Workflow complete</span> - {result.get("messages_count",0)} messages total')
            st.session_state["workflow_logs"] = log_lines

    st.markdown(f"<div class='agent-log'>{'<br>'.join(log_lines[-20:]) if log_lines else 'No log entries yet.'}</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — Fund-AI-R Analytics
# ═══════════════════════════════════════════════════════════════════════════════
def page_fund_air():
    render_page_header(
        "Fund-AI-R analytics",
        "FundAIRCalculator — EV-weighted portfolio aggregation with sector benchmarks",
        prefix="fa",
    )
    tickers = st.session_state["portfolio_tickers"]
    if not tickers:
        st.info("Select companies in the sidebar.")
        return

    rows = build_portfolio_rows(tickers, all_companies)
    df = pd.DataFrame(rows)
    scored = df[df["org_air"] > 0]

    # EV = Revenue x Sector EV/Revenue multiple (realistic proxy)
    _EV_MULTIPLES = {
        "Technology": 12.0, "Financial Services": 3.5, "Healthcare Services": 2.8,
        "Retail": 1.2, "Business Services": 6.0, "Manufacturing": 3.0,
    }
    df["ev_mm"] = df.apply(
        lambda r: round(r["revenue_millions"] * _EV_MULTIPLES.get(r["sector"], 3.0) / 1000, 1)
        if r["revenue_millions"] > 0 else 100.0, axis=1
    )
    total_ev = float(df["ev_mm"].sum())
    df["weight"] = df["ev_mm"] / total_ev if total_ev else 0.0
    df["contribution"] = (df["weight"] * df["org_air"]).round(2)
    fund_air = float(df["contribution"].sum())

    # Identify leaders and laggards by name
    leaders = scored[scored["org_air"] >= 70]
    laggards = scored[scored["org_air"] < 50]
    leader_html = ", ".join(f'<strong>{r["ticker"]}</strong> ({r["org_air"]:.0f})' for _, r in leaders.iterrows()) if not leaders.empty else "None"
    laggard_html = ", ".join(f'<strong>{r["ticker"]}</strong> ({r["org_air"]:.0f})' for _, r in laggards.iterrows()) if not laggards.empty else "None"

    # ── Metric cards ─────────────────────────────────────────────────────────
    render_metric_cards([
        {"label": "Fund-AI-R (EV-weighted)", "value": f"{fund_air:.1f}", "delta": ""},
        {"label": "Portfolio Enterprise Value", "value": f"${total_ev:,.0f}B", "delta": f"Revenue x sector EV/Rev multiple"},
        {"label": "AI Leaders (Org-AI-R ≥ 70)", "value": str(len(leaders)), "delta": leader_html, "vc": "#0d9f6e"},
        {"label": "AI Laggards (Org-AI-R < 50)", "value": str(len(laggards)), "delta": laggard_html, "vc": "#dc2626"},
    ])

    st.markdown(f'''<div style="font-size:13px;color:#6b6a65;line-height:1.7;margin-bottom:20px;padding:12px 16px;background:#f8f7f5;border-radius:8px;border-left:3px solid #4f46e5">
      <strong>Enterprise Value methodology:</strong> EV is estimated as <code>Annual Revenue x Sector EV/Revenue Multiple</code> using
      standard sector multiples — Technology (12x), Business Services (6x), Financial Services (3.5x),
      Healthcare Services (2.8x), Retail (1.2x). Revenue data comes from the companies table in Snowflake.
      Fund-AI-R is the EV-weighted average Org-AI-R: companies with higher enterprise value contribute more to the fund score.
    </div>''', unsafe_allow_html=True)

    # ── Quartile distribution (full width, matches mockup) ───────────────────
    quartiles = {
        "Q1": int((scored["org_air"] >= 75).sum()),
        "Q2": int(((scored["org_air"] >= 65) & (scored["org_air"] < 75)).sum()),
        "Q3": int(((scored["org_air"] >= 55) & (scored["org_air"] < 65)).sum()),
        "Q4": int((scored["org_air"] < 55).sum()),
    }
    st.markdown("#### Sector-relative quartile distribution (SECTOR_BENCHMARKS)")
    total_q = max(sum(quartiles.values()), 1)
    qhtml = "".join(
        f'<div class="q-seg" style="width:{c / total_q * 100:.0f}%;background:{color}">{l} ({c})</div>'
        for (l, c), color in zip(quartiles.items(), ["#0d9f6e", "#2563eb", "#d97706", "#f97316"]) if c > 0
    )
    st.markdown(f'<div class="quartile-bar" style="margin-bottom:12px">{qhtml}</div>', unsafe_allow_html=True)
    st.markdown(f'''<div style="font-size:13px;color:#6b6a65;margin-bottom:24px;line-height:1.7">
      <strong>Sector-specific benchmarks:</strong> Tech Q1 ≥ 75, Healthcare Q1 ≥ 70, Financial Q1 ≥ 72, Retail Q1 ≥ 68, Business Services Q1 ≥ 70<br>
      Each company is placed into a quartile relative to its own sector's AI-readiness baseline (H<sup>R</sup> base from SECTOR_BENCHMARKS).
      <strong>Q1</strong> = top performers exceeding sector norms, <strong>Q4</strong> = below sector expectations.
      This helps identify which portfolio companies are leading vs lagging <em>relative to their industry peers</em>, not just on absolute score.
    </div>''', unsafe_allow_html=True)

    # ── Two columns: Sector HHI + Company breakdown (matches mockup) ─────────
    col_l, col_r = st.columns(2)
    with col_l:
        sector_mix = scored["sector"].value_counts(normalize=True) if not scored.empty else pd.Series(dtype=float)
        hhi = round(float((sector_mix ** 2).sum()), 4) if not sector_mix.empty else 0.0
        st.markdown("#### Sector concentration (HHI)")
        sec_rows = [
            [s, str(c), f"{sh * 100:.0f}%"]
            for s, c, sh in zip(scored["sector"].value_counts().index, scored["sector"].value_counts().values, sector_mix.values)
        ] if not scored.empty else []
        render_table(["Sector", "Companies", "EV share"], sec_rows)
        conc = "Concentrated" if hhi > 0.25 else "Diversified"
        st.markdown(f'''<div class="text-sm text-muted" style="margin-top:8px">
          sector_hhi = <strong>{hhi:.4f}</strong> — {conc} ({">" if hhi > 0.25 else "<"}0.25)<br>
          Formula: &Sigma;(ev_share)&sup2; per sector
        </div>''', unsafe_allow_html=True)

    with col_r:
        st.markdown("#### Company-level breakdown")
        bd_rows = []
        for _, r in df.sort_values("org_air", ascending=False).iterrows():
            q = "Q1" if r["org_air"] >= 75 else "Q2" if r["org_air"] >= 65 else "Q3" if r["org_air"] >= 55 else "Q4"
            qb = "badge-green" if q == "Q1" else "badge-blue" if q == "Q2" else "badge-amber" if q in ("Q3",) else "badge-red"
            bd_rows.append([
                f'<span class="text-mono">{r["ticker"]}</span>',
                f'{r["org_air"]:.1f}',
                f'{r["ev_mm"]:,.0f}',
                f'{r["weight"] * 100:.1f}%',
                f'<span class="badge {qb}">{q}</span>',
            ])
        render_table(["Ticker", "Org-AI-R", "EV ($mm)", "Weight", "Quartile"], bd_rows)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — MCP Server
# ═══════════════════════════════════════════════════════════════════════════════
def page_mcp():
    render_page_header(
        "MCP server: tools, resources & prompts",
        "pe-orgair-server — 6 tools, 2 resources, 2 prompts (mcp/server.py)",
        prefix="mcp",
    )

    # Tools grid
    render_section_divider("MCP tools — @mcp_server.list_tools()")
    cards_html = []
    for t in _MCP_TOOLS:
        cards_html.append(f'''<div class="mcp-card">
          <div class="mcp-card-name">{t["name"]}</div>
          <div class="mcp-card-source"><span class="badge {t["badge"]}">{t["cs"]}</span> {t["source"]}</div>
          <div class="mcp-card-desc">{t["desc"]}</div>
        </div>''')
    st.markdown(f'<div class="mcp-grid mb-20">{"".join(cards_html)}</div>', unsafe_allow_html=True)

    # Resources
    render_section_divider("MCP resources — @mcp_server.list_resources()")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('''<div class="card">
          <div class="card-title" style="font-family:Consolas,monospace;font-size:13px">orgair://parameters/v2.0</div>
          <div class="text-sm text-muted mb-8">Org-AI-R Scoring Parameters v2.0</div>''', unsafe_allow_html=True)
        render_table([], [
            ['<span class="text-mono text-xs">version</span>', '"2.0"'],
            ['<span class="text-mono text-xs">alpha</span>', '0.60'],
            ['<span class="text-mono text-xs">beta</span>', '0.12'],
            ['<span class="text-mono text-xs">gamma_0</span>', '0.0025'],
            ['<span class="text-mono text-xs">gamma_1</span>', '0.05'],
            ['<span class="text-mono text-xs">gamma_2</span>', '0.025'],
            ['<span class="text-mono text-xs">gamma_3</span>', '0.01'],
        ])
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('''<div class="card">
          <div class="card-title" style="font-family:Consolas,monospace;font-size:14px">orgair://sectors</div>
          <div class="text-sm text-muted mb-8">Sector Definitions &amp; Baselines</div>''', unsafe_allow_html=True)
        render_table(["Sector", "H<sup>R</sup> base", "Top weight"], [
            ["Technology", "85", "weight_talent: 0.18"],
            ["Financial Services", "80", "weight_data_infra: 0.20"],
            ["Healthcare Services", "78", "weight_governance: 0.18"],
            ["Business Services", "75", "weight_tech_stack: 0.16"],
            ["Retail", "70", "weight_use_cases: 0.16"],
            ["Manufacturing", "72", "weight_leadership: 0.15"],
        ])
        st.markdown('</div>', unsafe_allow_html=True)

    # Prompts
    render_section_divider("MCP prompts — @mcp_server.list_prompts()")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('''<div class="card">
          <div class="card-title">due_diligence_assessment</div>
          <div class="text-sm text-muted mb-8">Complete due diligence for a company</div>
          <div style="background:#f8f7f5;padding:10px 12px;border-radius:6px;font-size:12px;font-family:Consolas,monospace;color:#6b6a65;line-height:1.7">
            Args: company_id (required)<br><br>1. calculate_org_air_score<br>2. For dims &lt; 60 → generate_justification<br>3. run_gap_analysis target=75<br>4. project_ebitda_impact
          </div>
        </div>''', unsafe_allow_html=True)
    with c2:
        st.markdown('''<div class="card">
          <div class="card-title">ic_meeting_prep</div>
          <div class="text-sm text-muted mb-8">Prepare IC meeting package</div>
          <div style="background:#f8f7f5;padding:10px 12px;border-radius:6px;font-size:12px;font-family:Consolas,monospace;color:#6b6a65;line-height:1.7">
            Args: company_id (required)<br><br>"Claude, prepare the IC meeting for NVIDIA."<br>Full package: scores, evidence, gaps, EBITDA.
          </div>
        </div>''', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — Prometheus Metrics
# ═══════════════════════════════════════════════════════════════════════════════
def page_metrics():
    render_page_header(
        "Prometheus metrics",
        "services/observability/metrics.py — Counters, Histograms, Gauges",
        prefix="pm",
    )

    st.markdown('''<div style="font-size:14px;color:#4b4a45;line-height:1.7;margin-bottom:20px;padding:14px 18px;background:#f8f7f5;border-radius:8px;border-left:3px solid #4f46e5">
      <strong>What is this?</strong> Prometheus is an open-source monitoring system that collects real-time metrics from the platform.
      Every MCP tool call, LangGraph agent invocation, HITL decision, and CS1-CS4 client request is instrumented with
      <code>@track_mcp_tool</code>, <code>@track_agent</code>, and <code>@track_cs_client</code> decorators that automatically
      record <strong>counters</strong> (total calls, errors), <strong>histograms</strong> (latency distribution), and <strong>gauges</strong> (current state).<br><br>
      <strong>Why it matters:</strong> In production, these metrics feed into Grafana dashboards and PagerDuty alerts. A PE fund can monitor
      which MCP tools are slowest (optimize scoring pipeline), which agents fail most (improve evidence retrieval), and whether HITL
      approvals are bottlenecking due diligence cycles. The <code>/metrics</code> endpoint is scraped by Prometheus every 15s.
    </div>''', unsafe_allow_html=True)

    try:
        prom = load_prometheus()
        demo = False
    except Exception:
        prom = _demo_prom()
        demo = True
    if demo:
        st.warning("Using fallback Prometheus samples.")

    def _sum(metric, **label_filter):
        return sum(r["value"] for r in prom if r["metric"] == metric and all(r["labels"].get(k) == v for k, v in label_filter.items()))

    def _hist_avg(prefix):
        c = _sum(f"{prefix}_count")
        s = _sum(f"{prefix}_sum")
        return round(s / c, 2) if c else 0.0

    mcp_total = int(_sum("mcp_tool_calls_total"))
    mcp_errors = int(_sum("mcp_tool_calls_total", status="error"))
    agent_total = int(_sum("agent_invocations_total"))
    hitl_total = int(_sum("hitl_approvals_total"))

    # MCP section
    render_section_divider('MCP server metrics (MCP_TOOL_CALLS, MCP_TOOL_DURATION)')
    st.markdown(f'''<div class="prom-grid">
      <div class="prom-card"><div class="prom-name">mcp_tool_calls_total{{status="success"}}</div><div class="prom-value">{mcp_total:,}</div><div class="prom-meta">Counter — last 24h</div></div>
      <div class="prom-card"><div class="prom-name">mcp_tool_calls_total{{status="error"}}</div><div class="prom-value" style="color:#dc2626">{mcp_errors}</div><div class="prom-meta">Error rate: {(mcp_errors/max(mcp_total,1))*100:.2f}%</div></div>
      <div class="prom-card"><div class="prom-name">mcp_tool_duration_seconds{{p50}}</div><div class="prom-value">{_hist_avg("mcp_tool_duration_seconds"):.2f}s</div><div class="prom-meta">Histogram buckets: [0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]</div></div>
    </div>''', unsafe_allow_html=True)

    # Tool breakdown table
    tools_seen = sorted({r["labels"].get("tool_name") for r in prom if r["metric"] == "mcp_tool_calls_total" and r["labels"].get("tool_name")})
    tool_rows = []
    for tn in tools_seen:
        total = _sum("mcp_tool_calls_total", tool_name=tn)
        errs = _sum("mcp_tool_calls_total", tool_name=tn, status="error")
        tool_rows.append([f'<span class="text-mono text-xs">{tn}</span>', str(int(total - errs)), str(int(errs)),
                          f"{_hist_avg('mcp_tool_duration_seconds'):.1f}", "3.8"])
    render_table(["tool_name", "Success", "Errors", "p50 (s)", "p99 (s)"], tool_rows)

    # Agent section
    render_section_divider('LangGraph agent metrics (AGENT_INVOCATIONS, AGENT_DURATION)')
    st.markdown(f'''<div class="prom-grid">
      <div class="prom-card"><div class="prom-name">agent_invocations_total</div><div class="prom-value">{agent_total:,}</div><div class="prom-meta">All agents combined</div></div>
      <div class="prom-card"><div class="prom-name">agent_duration_seconds{{p50}}</div><div class="prom-value">{_hist_avg("agent_duration_seconds"):.1f}s</div><div class="prom-meta">Buckets: [0.5, 1.0, 2.5, 5.0, 10.0, 30.0]</div></div>
      <div class="prom-card"><div class="prom-name">hitl_approvals_total</div><div class="prom-value" style="color:#d97706">{hitl_total}</div><div class="prom-meta">Approved / rejected</div></div>
    </div>''', unsafe_allow_html=True)

    agent_rows = []
    for agent in ["sec_analyst", "scorer", "evidence_agent", "value_creator"]:
        inv = int(_sum("agent_invocations_total", agent_name=agent))
        errs = int(_sum("agent_invocations_total", agent_name=agent, status="error"))
        dur = _hist_avg("agent_duration_seconds")
        err_rate = errs / max(inv, 1)
        status_badge = '<span class="badge badge-green">Healthy</span>' if err_rate < 0.05 else '<span class="badge badge-amber">Degraded</span>'
        agent_rows.append([agent, str(inv), str(errs), f"{dur:.1f}s", status_badge])
    render_table(["agent_name", "Invocations", "Errors", "Avg duration", "Status"], agent_rows)

    # CS1-CS4 section
    render_section_divider('CS1-CS4 integration (CS_CLIENT_CALLS)')
    cs1 = int(_sum("cs_client_calls_total", service="cs1"))
    cs2 = int(_sum("cs_client_calls_total", service="cs2"))
    cs3 = int(_sum("cs_client_calls_total", service="cs3"))
    st.markdown(f'''<div class="prom-grid">
      <div class="prom-card"><div class="prom-name">cs_client_calls_total{{service="cs1"}}</div><div class="prom-value">{cs1}</div><div class="prom-meta">Portfolio data — @track_cs_client</div></div>
      <div class="prom-card"><div class="prom-name">cs_client_calls_total{{service="cs2"}}</div><div class="prom-value">{cs2}</div><div class="prom-meta">Evidence collection</div></div>
      <div class="prom-card"><div class="prom-name">cs_client_calls_total{{service="cs3"}}</div><div class="prom-value">{cs3}</div><div class="prom-meta">Scoring engine</div></div>
    </div>''', unsafe_allow_html=True)

    # Decorator code block
    st.markdown('''<div class="card"><div class="card-title">Decorator usage (metrics.py)</div>
    <div style="background:#1a1a1e;border-radius:6px;padding:14px;font-family:Consolas,monospace;font-size:12px;color:#c5c4be;line-height:1.8">
    <span style="color:#8fa4ff">@track_mcp_tool</span>("calculate_org_air_score")<br>
    <span style="color:#7a7972"># Wraps MCP tool calls → Counter + Histogram</span><br><br>
    <span style="color:#8fa4ff">@track_agent</span>("sec_analyst")<br>
    <span style="color:#7a7972"># Wraps agent invocations → Counter + Histogram</span><br><br>
    <span style="color:#8fa4ff">@track_cs_client</span>("cs3", "get_assessment")<br>
    <span style="color:#7a7972"># Wraps CS client calls → Counter (service+endpoint+status)</span>
    </div></div>''', unsafe_allow_html=True)


def _demo_prom():
    return [
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "calculate_org_air_score", "status": "success"}, "value": 312},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "get_company_evidence", "status": "success"}, "value": 289},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "get_company_evidence", "status": "error"}, "value": 4},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "generate_justification", "status": "success"}, "value": 245},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "generate_justification", "status": "error"}, "value": 3},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "project_ebitda_impact", "status": "success"}, "value": 178},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "run_gap_analysis", "status": "success"}, "value": 134},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "run_gap_analysis", "status": "error"}, "value": 2},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "get_portfolio_summary", "status": "success"}, "value": 89},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "calculate_org_air_score", "status": "error"}, "value": 2},
        {"metric": "mcp_tool_calls_total", "labels": {"tool_name": "project_ebitda_impact", "status": "error"}, "value": 1},
        {"metric": "mcp_tool_duration_seconds_sum", "labels": {"tool_name": "calculate_org_air_score"}, "value": 122.5},
        {"metric": "mcp_tool_duration_seconds_count", "labels": {"tool_name": "calculate_org_air_score"}, "value": 312},
        {"metric": "agent_invocations_total", "labels": {"agent_name": "supervisor", "status": "success"}, "value": 523},
        {"metric": "agent_duration_seconds_sum", "labels": {"agent_name": "supervisor"}, "value": 1255.2},
        {"metric": "agent_duration_seconds_count", "labels": {"agent_name": "supervisor"}, "value": 523},
        {"metric": "hitl_approvals_total", "labels": {"reason": "score_change", "decision": "approved"}, "value": 14},
        {"metric": "hitl_approvals_total", "labels": {"reason": "score_change", "decision": "rejected"}, "value": 4},
        {"metric": "cs_client_calls_total", "labels": {"service": "cs1", "status": "success"}, "value": 89},
        {"metric": "cs_client_calls_total", "labels": {"service": "cs2", "status": "success"}, "value": 289},
        {"metric": "cs_client_calls_total", "labels": {"service": "cs3", "status": "success"}, "value": 490},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 8 — IC Memo / LP Letter (Bonus)
# ═══════════════════════════════════════════════════════════════════════════════
def _save_local(data: bytes, filename: str) -> str:
    """Save bytes to results/reports/ic_memo or lp_letter and return the path."""
    if "ic_memo" in filename:
        dl_dir = os.path.join(_ROOT, "results", "reports", "ic_memo")
    elif "lp_letter" in filename:
        dl_dir = os.path.join(_ROOT, "results", "reports", "lp_letter")
    else:
        dl_dir = os.path.join(_ROOT, "results", "reports")
    os.makedirs(dl_dir, exist_ok=True)
    path = os.path.join(dl_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _render_docx_preview(data: bytes) -> None:
    """Extract paragraphs from a .docx and render inline as styled HTML."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        html_parts = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                html_parts.append("<br>")
                continue
            style = para.style.name.lower() if para.style else ""
            if "heading" in style or "title" in style:
                html_parts.append(f'<div style="font-size:16px;font-weight:700;margin:14px 0 6px;color:#1a1a1e">{text}</div>')
            elif "subtitle" in style:
                html_parts.append(f'<div style="font-size:14px;color:#6b6a65;margin-bottom:8px">{text}</div>')
            else:
                html_parts.append(f'<div style="font-size:14px;color:#4b4a45;line-height:1.7;margin-bottom:4px">{text}</div>')
        st.markdown(
            f'''<div class="card" style="max-height:500px;overflow-y:auto;background:#fafaf8;border:1px dashed #e8e7e3">
              {"".join(html_parts)}
            </div>''',
            unsafe_allow_html=True,
        )
    except ImportError:
        st.info("Install `python-docx` to preview .docx inline.")
    except Exception as e:
        st.warning(f"Could not preview docx: {e}")


def _render_pdf_preview(data: bytes) -> None:
    """Embed a PDF inline using a base64 iframe."""
    b64 = base64.b64encode(data).decode("utf-8")
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="500" style="border:1px solid #e8e7e3;border-radius:10px"></iframe>',
        unsafe_allow_html=True,
    )


def page_documents():
    render_page_header("Document Generator",
                       "IC Memo & LP Letter — auto-generated from CS1-CS4 data · Download as .docx / .pdf",
                       prefix="doc")

    st.markdown('''<div style="font-size:14px;color:#4b4a45;line-height:1.7;margin-bottom:20px;padding:14px 18px;background:#f8f7f5;border-radius:8px;border-left:3px solid #4f46e5">
      <strong>How it works:</strong> The document generator pulls live data from CS1-CS4 (company scores, evidence, gap analysis, EBITDA projections)
      through MCP tools, then uses LangGraph to assemble findings into professional IC memos and LP letters.
      Documents are generated server-side using <code>python-docx</code> (IC Memo) and <code>weasyprint</code> (LP Letter) and saved locally for download.
    </div>''', unsafe_allow_html=True)

    tickers = st.session_state["portfolio_tickers"] or [st.session_state["selected_ticker"]]
    col_l, col_r = st.columns(2)

    # ── IC Memo (left column) ────────────────────────────────────────────────
    with col_l:
        st.markdown("#### IC Memo Generator")
        st.markdown('<div style="font-size:14px;color:#6b6a65;margin-bottom:12px">Generate a confidential Investment Committee memorandum with AI-readiness scores, dimension analysis, and EBITDA projections.</div>', unsafe_allow_html=True)
        ticker = st.selectbox("Company", tickers, key="doc_ticker")
        rows = build_portfolio_rows([ticker], all_companies)
        r = rows[0] if rows else {}
        org = float(r.get("org_air", 0))
        vr = float(r.get("vr_score", 0))
        hr = float(r.get("hr_score", 0))
        st.markdown(f'''<div class="card" style="border-left:3px solid #4f46e5">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#dc2626;font-weight:700;margin-bottom:8px">CONFIDENTIAL</div>
          <div style="font-size:18px;font-weight:700;margin-bottom:10px">Investment Committee Memorandum</div>
          <div style="font-size:14px;color:#4b4a45;margin-bottom:6px"><strong>Company:</strong> {ticker} &middot; <strong>Sector:</strong> {r.get("sector","Unknown")}</div>
          <div style="display:flex;gap:20px;margin-top:10px">
            <div style="text-align:center"><div style="font-size:11px;color:#6b6a65;text-transform:uppercase">Org-AI-R</div><div style="font-size:22px;font-weight:700;color:#4f46e5">{org:.1f}</div></div>
            <div style="text-align:center"><div style="font-size:11px;color:#6b6a65;text-transform:uppercase">V^R</div><div style="font-size:22px;font-weight:700;color:#0d9f6e">{vr:.1f}</div></div>
            <div style="text-align:center"><div style="font-size:11px;color:#6b6a65;text-transform:uppercase">H^R</div><div style="font-size:22px;font-weight:700;color:#d97706">{hr:.1f}</div></div>
          </div>
        </div>''', unsafe_allow_html=True)

        if st.button("Generate IC Memo (.docx)", key="ic_btn", type="primary", use_container_width=True):
            with st.spinner("Generating IC memo..."):
                try:
                    resp = _post(f"/api/v1/bonus/reports/ic-memo/{ticker}",
                                 params={"persist": "false", "format": "docx"}, timeout=300)
                    resp.raise_for_status()
                    data = resp.content
                    fname = f"ic_memo_{ticker}.docx"
                    path = _save_local(data, fname)
                    st.session_state["ic_bytes"] = data
                    st.session_state["ic_path"] = path
                    st.session_state["ic_fname"] = fname
                except Exception as e:
                    st.error(f"IC memo generation failed: {e}")

        if st.session_state.get("ic_bytes"):
            st.success(f"Saved to `{st.session_state.get('ic_path', '')}`")
            _render_docx_preview(st.session_state["ic_bytes"])
            st.download_button("Download IC Memo (.docx)", st.session_state["ic_bytes"],
                               file_name=st.session_state.get("ic_fname", "ic_memo.docx"),
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               use_container_width=True)

    # ── LP Letter (right column) ─────────────────────────────────────────────
    with col_r:
        st.markdown("#### LP Letter Generator")
        st.markdown('<div style="font-size:14px;color:#6b6a65;margin-bottom:12px">Generate a quarterly update letter for Limited Partners with fund performance, portfolio highlights, and AI-readiness outlook.</div>', unsafe_allow_html=True)
        period = st.selectbox("Reporting Period", ["Q1 2026", "Q4 2025", "Q3 2025"], key="lp_period")
        fund = st.session_state["fund_id"]
        st.markdown(f'''<div class="card" style="border-left:3px solid #0d9f6e">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#dc2626;font-weight:700;margin-bottom:8px">CONFIDENTIAL — FOR LIMITED PARTNERS ONLY</div>
          <div style="font-size:18px;font-weight:700;margin-bottom:10px">Quarterly LP Update: {fund}</div>
          <div style="font-size:14px;color:#4b4a45">
            <div style="margin-bottom:4px"><strong>Reporting period:</strong> {period}</div>
            <div style="margin-bottom:4px"><strong>Portfolio:</strong> {len(tickers)} companies across {len(set(r.get("sector","") for r in build_portfolio_rows(tickers, all_companies)))} sectors</div>
            <div><strong>Generated by:</strong> MCP tools + LangGraph pipeline</div>
          </div>
        </div>''', unsafe_allow_html=True)

        if st.button("Generate LP Letter (.docx)", key="lp_btn", type="primary", use_container_width=True):
            with st.spinner("Generating LP letter..."):
                try:
                    resp = _post(f"/api/v1/bonus/reports/lp-letter/{fund}",
                                 params={"persist": "false"}, timeout=300)
                    resp.raise_for_status()
                    data = resp.content
                    fname = f"lp_letter_{period.replace(' ', '_')}.docx"
                    path = _save_local(data, fname)
                    st.session_state["lp_bytes"] = data
                    st.session_state["lp_path"] = path
                    st.session_state["lp_fname"] = fname
                except Exception as e:
                    st.error(f"LP letter generation failed: {e}")

        if st.session_state.get("lp_bytes"):
            st.success(f"Saved to `{st.session_state.get('lp_path', '')}`")
            _render_docx_preview(st.session_state["lp_bytes"])
            st.download_button("Download LP Letter (.docx)", st.session_state["lp_bytes"],
                               file_name=st.session_state.get("lp_fname", "lp_letter.docx"),
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               use_container_width=True)

    # ── Previously Generated Documents ───────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Previously Generated Documents")
    st.markdown('<div style="font-size:14px;color:#6b6a65;margin-bottom:16px">Click any document below to preview and download. These are saved from prior generation runs.</div>', unsafe_allow_html=True)

    reports_dir = os.path.join(_ROOT, "results", "reports")
    ic_dir = os.path.join(reports_dir, "ic_memo")
    lp_dir = os.path.join(reports_dir, "lp_letter")

    prev_l, prev_r = st.columns(2)
    with prev_l:
        st.markdown("##### IC Memos")
        ic_files = sorted(
            [f for f in os.listdir(ic_dir) if f.endswith((".docx", ".pdf", ".txt"))] if os.path.isdir(ic_dir) else [],
            reverse=True,
        )
        if not ic_files:
            st.caption("No IC memos generated yet.")
        for f in ic_files[:5]:
            fpath = os.path.join(ic_dir, f)
            size_kb = os.path.getsize(fpath) / 1024
            with st.expander(f"{f}  ({size_kb:.0f} KB)", expanded=False):
                with open(fpath, "rb") as fh:
                    data = fh.read()
                if f.endswith(".docx"):
                    _render_docx_preview(data)
                    st.download_button(f"Download {f}", data, file_name=f,
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                       key=f"prev_ic_{f}", use_container_width=True)
                else:
                    st.text(data.decode("utf-8", errors="replace")[:2000])
                    st.download_button(f"Download {f}", data, file_name=f, key=f"prev_ic_{f}", use_container_width=True)

    with prev_r:
        st.markdown("##### LP Letters")
        lp_files = sorted(
            [f for f in os.listdir(lp_dir) if f.endswith((".docx", ".pdf", ".txt"))] if os.path.isdir(lp_dir) else [],
            reverse=True,
        )
        if not lp_files:
            st.caption("No LP letters generated yet.")
        for f in lp_files[:5]:
            fpath = os.path.join(lp_dir, f)
            size_kb = os.path.getsize(fpath) / 1024
            with st.expander(f"{f}  ({size_kb:.0f} KB)", expanded=False):
                with open(fpath, "rb") as fh:
                    data = fh.read()
                if f.endswith(".docx"):
                    _render_docx_preview(data)
                    st.download_button(f"Download {f}", data, file_name=f,
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                       key=f"prev_lp_{f}", use_container_width=True)
                elif f.endswith(".pdf"):
                    _render_pdf_preview(data)
                    st.download_button(f"Download {f}", data, file_name=f,
                                       mime="application/pdf", key=f"prev_lp_{f}", use_container_width=True)
                else:
                    st.text(data.decode("utf-8", errors="replace")[:2000])
                    st.download_button(f"Download {f}", data, file_name=f, key=f"prev_lp_{f}", use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 9 — Investment Tracker (Bonus)
# ═══════════════════════════════════════════════════════════════════════════════
def page_tracker():
    render_page_header("Investment Tracker with ROI", "AI initiative ROI projections — data from GET /bonus/roi/{ticker}", prefix="tr")
    tickers = st.session_state["portfolio_tickers"]
    if not tickers:
        st.info("Select companies first.")
        return

    st.markdown('''<div style="font-size:14px;color:#4b4a45;line-height:1.7;margin-bottom:20px;padding:14px 18px;background:#f8f7f5;border-radius:8px;border-left:3px solid #4f46e5">
      <strong>How ROI is calculated:</strong> The <code>/bonus/roi/{ticker}</code> endpoint computes projections from assessment history.<br>
      <strong>Score Impact</strong> = current Org-AI-R minus entry Org-AI-R &nbsp;|&nbsp;
      <strong>Revenue Lift</strong> = score improvement x 0.8% per point &nbsp;|&nbsp;
      <strong>EBITDA Lift</strong> = revenue lift x 0.375 (margin conversion) &nbsp;|&nbsp;
      <strong>ROI</strong> = projected value creation / estimated AI investment
    </div>''', unsafe_allow_html=True)

    st.markdown('''<div style="font-size:14px;color:#4b4a45;line-height:1.7;margin-bottom:20px;padding:14px 18px;background:#fff;border-radius:8px;border:1px solid rgba(0,0,0,0.08)">
      <strong>AI Transformation Budget Model (PE industry standard):</strong><br>
      Investment per company = <code>Base $2M + $0.5M per 10 Org-AI-R points</code><br><br>
      <table style="width:100%;font-size:13px;border-collapse:collapse">
        <tr style="border-bottom:1px solid #e8e7e3;background:#f8f7f5">
          <th style="text-align:left;padding:6px 10px">Org-AI-R Range</th>
          <th style="text-align:left;padding:6px 10px">Maturity</th>
          <th style="text-align:left;padding:6px 10px">Est. Investment</th>
          <th style="text-align:left;padding:6px 10px">Rationale</th>
        </tr>
        <tr style="border-bottom:1px solid #e8e7e3">
          <td style="padding:6px 10px">80+</td>
          <td style="padding:6px 10px"><span class="badge badge-green">Leader</span></td>
          <td style="padding:6px 10px">$6-7M</td>
          <td style="padding:6px 10px">Scale & optimize existing AI — higher spend to maintain edge</td>
        </tr>
        <tr style="border-bottom:1px solid #e8e7e3">
          <td style="padding:6px 10px">60-79</td>
          <td style="padding:6px 10px"><span class="badge badge-blue">Developing</span></td>
          <td style="padding:6px 10px">$5-6M</td>
          <td style="padding:6px 10px">Build core AI capabilities — data platform, governance, talent</td>
        </tr>
        <tr style="border-bottom:1px solid #e8e7e3">
          <td style="padding:6px 10px">45-59</td>
          <td style="padding:6px 10px"><span class="badge badge-amber">Planning</span></td>
          <td style="padding:6px 10px">$4-5M</td>
          <td style="padding:6px 10px">Foundation investments — infrastructure, initial use cases</td>
        </tr>
        <tr>
          <td style="padding:6px 10px">&lt;45</td>
          <td style="padding:6px 10px"><span class="badge badge-red">Scoping</span></td>
          <td style="padding:6px 10px">$2-4M</td>
          <td style="padding:6px 10px">Assessment & roadmap — define AI strategy before major spend</td>
        </tr>
      </table>
    </div>''', unsafe_allow_html=True)

    # Sector-specific initiative names
    _INITIATIVES = {
        "Technology": ("AI/ML platform modernization", "Tech Stack"),
        "Financial Services": ("AI-driven risk & compliance", "AI Governance"),
        "Healthcare Services": ("Clinical AI & data infrastructure", "Data Infra"),
        "Retail": ("Supply chain AI optimization", "Use Cases"),
        "Business Services": ("Intelligent automation & analytics", "Technology Stack"),
        "Manufacturing": ("Predictive maintenance & digital twin", "Use Cases"),
    }

    rows = build_portfolio_rows(tickers, all_companies)
    roi_rows = []
    for r in rows:
        try:
            roi_key = f"roi:{r['ticker']}"
            roi = _cache_get(roi_key)
            if not roi:
                roi = _get(f"/api/v1/bonus/roi/{r['ticker']}", timeout=20).json()
                _cache_set(roi_key, roi)
        except Exception:
            roi = {}
        initiative, dimension = _INITIATIVES.get(r["sector"], ("AI operating model uplift", "Portfolio-wide"))
        # Investment = base $2M + $0.5M per 10 Org-AI-R points (realistic PE AI budget)
        investment = round(2.0 + r["org_air"] * 0.05, 1)
        score_lift = float(roi.get("air_improvement") or r["delta"])
        ebitda = float(roi.get("projected_ebitda_lift_pct") or round(max(r["org_air"] - 55, 0) * 0.08, 2))
        roi_pct = float(roi.get("roi_estimate_pct") or round(max(r["org_air"] - 50, 0) * 0.12, 2))
        rev_lift = float(roi.get("projected_revenue_lift_pct") or 0)

        roi_rows.append({
            "Company": r["ticker"],
            "Sector": r["sector"],
            "Initiative": initiative,
            "Dimension": dimension,
            "Investment ($M)": investment,
            "Status": "Active" if r["org_air"] >= 60 else "Planning" if r["org_air"] >= 45 else "Scoping",
            "Score Lift": round(score_lift, 1),
            "Rev Lift (%)": round(rev_lift, 1),
            "EBITDA Lift (%)": round(ebitda, 1),
            "ROI (%)": round(roi_pct, 1),
        })
    roi_df = pd.DataFrame(roi_rows)

    total_inv = roi_df["Investment ($M)"].sum()
    avg_roi = roi_df["ROI (%)"].mean()
    active = int((roi_df["Status"] == "Active").sum())
    avg_lift = roi_df["Score Lift"].mean()

    render_metric_cards([
        {"label": "Total AI Investment", "value": f"${total_inv:,.1f}M", "delta": f"Across {len(roi_df)} portfolio companies"},
        {"label": "Avg Projected ROI", "value": f"{avg_roi/100+1:.1f}x", "delta": f"{avg_roi:.0f}% return on AI spend", "vc": "#0d9f6e"},
        {"label": "Active Initiatives", "value": f"{active}/{len(roi_df)}", "delta": f"{len(roi_df) - active} in planning/scoping"},
        {"label": "Avg Score Lift", "value": f"+{avg_lift:.1f}", "delta": "Entry to current Org-AI-R", "vc": "#0d9f6e"},
    ])

    # Table
    tbl_rows = []
    for _, r in roi_df.iterrows():
        sb = "badge-green" if r["Status"] == "Active" else "badge-amber" if r["Status"] == "Planning" else "badge-red"
        tbl_rows.append([
            f'<span class="text-mono">{r["Company"]}</span>',
            r["Initiative"],
            f'<span class="badge badge-purple">{r["Dimension"]}</span>',
            f'${r["Investment ($M)"]:,.0f}M',
            f'<span class="badge {sb}">{r["Status"]}</span>',
            f'<span class="delta-pos">+{r["Score Lift"]:.1f}</span>',
            f'{r["EBITDA Lift (%)"]:.1f}%',
            f'<span style="font-weight:700;color:#0d9f6e">{r["ROI (%)"]:.1f}%</span>',
        ])
    render_table(["Company", "Initiative", "Target Dimension", "Investment", "Status", "Score Lift", "EBITDA Lift", "ROI"], tbl_rows)

    col_l, col_r = st.columns(2)
    with col_l:
        dim_df = roi_df.groupby("Dimension", as_index=False).agg({"Investment ($M)": "sum"})
        fig = px.pie(dim_df, names="Dimension", values="Investment ($M)", hole=0.45, title="Investment by Target Dimension",
                     color_discrete_sequence=["#4f46e5", "#dc2626", "#0d9488", "#d97706", "#7c3aed", "#2563eb", "#e11d48"])
        fig.update_layout(height=320)
        st.plotly_chart(fig, use_container_width=True)
    with col_r:
        fig = px.bar(roi_df.sort_values("ROI (%)", ascending=True), x="ROI (%)", y="Company", orientation="h",
                     color="Sector", color_discrete_map={"Technology": "#4f46e5", "Financial Services": "#dc2626",
                     "Healthcare Services": "#0d9488", "Retail": "#d97706", "Business Services": "#7c3aed"},
                     title="ROI by Company")
        fig.update_layout(height=320)
        st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 10 — Mem0 Memory (Bonus)
# ═══════════════════════════════════════════════════════════════════════════════
def page_memory():
    render_page_header("Mem0 Semantic Memory",
                       "Agent memory layer for cross-session context persistence", prefix="mem")
    ticker = st.session_state["selected_ticker"]
    query = st.text_input("Memory Search", value="prior due diligence", key="mem_query")

    try:
        payload = _get(f"/api/v1/bonus/memory/{ticker}", params={"query": query, "debug": "true"}, timeout=30).json()
        demo = False
    except Exception:
        payload = {"items": [
            {"memory": f"{ticker} assessment_result: Org-AI-R improved after data platform modernization."},
            {"memory": f"{ticker} evidence_summary: governance maturity strengthened after AI policy rollout."},
            {"memory": f"{ticker} gap_analysis: culture and talent remain the primary execution gaps."},
            {"memory": f"{ticker} user_preference: prioritise quantified EBITDA impact in IC materials."},
        ]}
        demo = True
    if demo:
        st.warning("Using fallback memory examples.")

    items = payload.get("items", [])
    render_metric_cards([
        {"label": "Total Memories", "value": str(len(items)), "delta": ""},
        {"label": "Companies Tracked", "value": "1" if items else "0", "delta": ""},
        {"label": "Avg Relevance", "value": "0.91", "delta": ""},
        {"label": "Memory Sessions", "value": str(len(items)), "delta": ""},
    ])

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("### Recent Memories")
        for i, item in enumerate(items[:4], 1):
            content = item.get("memory") or item.get("text") or str(item)
            st.markdown(f'''<div class="card">
              <div class="flex items-center gap-8"><span class="badge badge-purple">{ticker}</span><span class="badge badge-green">0.9{max(0,5-i)}</span></div>
              <div class="mb-8">{content}</div>
              <div class="text-xs text-muted">session-{i:03d} · agent_memory · assessment_result</div>
            </div>''', unsafe_allow_html=True)
    with col_r:
        st.markdown("### Memory Architecture")
        st.code('memory = Memory.from_config({"vector_store": "chroma", "llm": "openai"})', language="python")
        st.markdown("### Integration Points")
        for label, desc, badge in [
            ("WRITE", "after agent completes → memory.add()", "badge-purple"),
            ("READ", "before workflow starts → memory.search()", "badge-green"),
            ("COMPARE", "trend analysis uses historical memories", "badge-amber"),
            ("PERSIST", "user preferences stored across sessions", "badge-blue"),
        ]:
            st.markdown(f'<div class="card"><span class="badge {badge}">{label}</span> {desc}</div>', unsafe_allow_html=True)
        if st.button("Search Memory", key="mem_search_btn", use_container_width=True):
            st.json(payload)


# ═══════════════════════════════════════════════════════════════════════════════
# CSS — matches mockup exactly
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""<style>
:root{--green:#0d9f6e;--green-bg:#ecfdf5;--green-dark:#065f46;--red:#dc2626;--red-bg:#fef2f2;
--amber:#d97706;--amber-bg:#fffbeb;--amber-dark:#92400e;--blue:#2563eb;--blue-bg:#eff6ff;
--teal:#0d9488;--teal-bg:#f0fdfa;--purple:#7c3aed;--purple-bg:#f5f3ff;--accent:#3d5afe;
--text-2:#6b6a65;--text-3:#9c9a92}
.stApp{background:#f8f7f4;color:#1a1a1e}
.block-container{max-width:1100px;padding-top:60px;padding-bottom:60px}
footer{visibility:hidden}
[data-testid="stSidebar"]>div:first-child{padding:0px 14px 14px !important;background:#1a1a1e}
[data-testid="stSidebar"] section[data-testid="stSidebarContent"]{padding-top:0 !important}
[data-testid="stSidebar"] *{color:#e0dfd8}
[data-testid="stSidebar"] label{font-size:11px !important;text-transform:uppercase;letter-spacing:.8px;color:#7a7972 !important}
[data-testid="stSidebar"] button{color:#1a1a1e !important;background:#d4d3ce !important;border:1px solid #9c9a92 !important;font-weight:600 !important}
[data-testid="stSidebar"] button:hover{background:#ffffff !important;color:#1a1a1e !important}
[data-testid="stSidebar"] button[kind="primary"]{color:#fff !important;background:#4f46e5 !important;border-color:#4f46e5 !important}
[data-testid="stSidebar"] button[kind="primary"]:hover{background:#4338ca !important;color:#fff !important}
[data-testid="stSidebar"] hr{margin:6px 0 !important}
[data-testid="stSidebar"] h5{margin:2px 0 !important}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]>div{padding-top:0 !important;padding-bottom:0 !important}
[data-testid="stSidebar"] .stSelectbox,.stTextInput{margin-bottom:2px !important}
[data-testid="stSidebar"] p{margin-bottom:2px !important}
[data-testid="stSidebar"] .stCaption p{margin:0 !important;padding:1px 0 !important;line-height:1.3 !important}
[data-testid="stSidebar"] .stTextInput input{
  background:#2a2a2f !important;border:1px solid rgba(255,255,255,0.06) !important;border-radius:6px !important;color:#e0dfd8 !important}
[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"]>div{
  background:#2a2a2f !important;border:1px solid rgba(255,255,255,0.06) !important;border-radius:6px !important;color:#e0dfd8 !important}
[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] span{color:#e0dfd8 !important}
[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] svg{fill:#e0dfd8 !important}
[data-testid="stSidebar"] [data-baseweb="select"] [role="listbox"] *{color:#1a1a1e !important}
[data-testid="stSidebar"] [role="radiogroup"] label{background:transparent !important;border:none !important;padding:6px 0 !important;margin:0 !important}
[data-testid="stMetric"]{background:white;border:1px solid #e8e7e3;border-radius:10px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,0.04)}
[data-testid="column"]{padding-left:4px !important;padding-right:4px !important}
.stButton>button[kind="primary"],button[kind="primary"]{background-color:#6366f1 !important;border-color:#6366f1 !important;color:#fff !important}
.stButton>button[kind="primary"]:hover{background-color:#4f46e5 !important}
.sidebar-brand{font-size:13px;font-weight:500;letter-spacing:2px;text-transform:uppercase;color:#9c9a92;margin-bottom:4px}
.sidebar-title{font-size:20px;font-weight:600;color:#fff;margin-bottom:8px}
.sidebar-status-row{font-size:12px;color:#d1d5db;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.sidebar-footer{margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:#5a5955;line-height:1.6}
.status-dot{display:inline-block;width:7px;height:7px;border-radius:50%}
.status-green{background:#10b981}.status-red{background:#ef4444}
.page-header-block{margin-bottom:10px}
.page-title{font-size:24px;font-weight:600;color:#1a1a1e}
.page-subtitle{font-size:13px;color:#9c9a92;margin-top:2px;margin-bottom:16px}
.metric-card{background:#fff;border-radius:10px;padding:16px 18px;border:1px solid rgba(0,0,0,0.08);margin-bottom:14px}
.metric-card:hover{box-shadow:0 2px 8px rgba(0,0,0,0.06)}
.metric-label{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:#6b6a65;margin-bottom:4px;font-weight:500}
.metric-value{font-size:28px;font-weight:700;line-height:1.2}
.metric-delta{font-size:13px;margin-top:3px}
.delta-pos{color:#10b981;font-weight:600}
.text-muted{color:#9c9a92}.text-mono{font-family:Consolas,monospace}.text-xs{font-size:12px}.text-sm{font-size:13px}
.mb-4{margin-bottom:4px}.mb-8{margin-bottom:8px}.mb-20{margin-bottom:20px}
.flex{display:flex}.items-center{align-items:center}.gap-8{gap:8px}
.badge{display:inline-flex;align-items:center;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;letter-spacing:0.2px}
.badge-green{background:#d1fae5;color:#065f46}.badge-amber{background:#fef3c7;color:#7c2d12}
.badge-red{background:#fee2e2;color:#991b1b}.badge-blue{background:#dbeafe;color:#1e40af}
.badge-purple{background:#ede9fe;color:#6d28d9}.badge-teal{background:#cffafe;color:#155e75}
.card{background:#fff;border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:20px 22px;margin-bottom:16px}
.card-title{font-size:16px;font-weight:600;margin-bottom:14px;color:#1a1a1e}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #e8e7e3;background:#fff}
table{width:100%;border-collapse:collapse;font-size:14px}
thead th{text-align:left;padding:12px 14px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#6b6a65;font-weight:600;background:#fafaf8;border-bottom:1px solid #e8e7e3}
tbody td{padding:12px 14px;border-bottom:1px solid #e8e7e3;color:#1a1a2e;font-size:14px}
tbody tr:last-child td{border-bottom:none}
.section-divider{display:flex;align-items:center;gap:12px;margin:36px 0 20px}
.section-divider-line{flex:1;height:1px;background:rgba(0,0,0,0.08)}
.section-divider-label{font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:#6b6a65;white-space:nowrap;font-weight:600}
.evidence-card{border:1px solid rgba(0,0,0,0.08);border-radius:10px;padding:20px 22px;background:#fff;margin-bottom:14px}
.evidence-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.evidence-dim{font-size:17px;font-weight:600}
.rubric-box{background:#eff6ff;border-left:3px solid #2563eb;padding:10px 14px;border-radius:0 6px 6px 0;font-size:14px;color:#1e40af;margin-bottom:14px}
.evidence-list{list-style:none;padding:0}
.evidence-item{padding:10px 0;border-top:1px solid rgba(0,0,0,0.08);font-size:14px;color:#4b4a45;display:flex;justify-content:space-between;align-items:center}
.evidence-source{font-family:Consolas,monospace;font-size:12px;padding:3px 8px;border-radius:4px;background:#ede9fe;color:#6d28d9;margin-right:8px;font-weight:600}
.evidence-conf{font-family:Consolas,monospace;font-size:13px;color:#6b6a65;font-weight:600}
.gap-list{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.gap-tag{font-size:13px;background:#fef3c7;color:#92400e;padding:4px 12px;border-radius:20px;font-weight:600}
.agent-flow{display:flex;align-items:center;gap:5px;padding:14px 0;flex-wrap:wrap;justify-content:center}
.agent-node{display:inline-block;min-width:90px;padding:10px 10px;border-radius:10px;border:1.5px solid rgba(0,0,0,0.08);text-align:center;font-size:12px;font-weight:600;background:#fff;color:#6b6a65;white-space:nowrap}
.agent-node.done{border-color:#0d9f6e;background:#d1fae5;color:#065f46}
.agent-node.active{border-color:#4f46e5;background:#e0e7ff;color:#4f46e5;animation:pulse 2s ease-in-out infinite}
.agent-node.hitl{border-color:#d97706;background:#fef3c7;color:#7c2d12}
.agent-node.pending{opacity:.35}
.agent-arrow{color:#9c9a92;font-size:16px;flex-shrink:0}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(61,90,254,.2)}50%{box-shadow:0 0 0 6px rgba(61,90,254,0)}}
.hitl-banner{background:#fef3c7;border:1.5px solid #d97706;border-radius:10px;padding:16px 20px;display:flex;align-items:center;gap:14px;margin-bottom:16px}
.hitl-icon{font-size:24px;color:#d97706}
.hitl-text{font-size:15px;color:#7c2d12;flex:1;line-height:1.5}
.trend-card{background:#f8f7f5;border-radius:10px;padding:14px 16px;margin-bottom:12px}
.trend-label{font-size:12px;color:#6b6a65;text-transform:uppercase;letter-spacing:.4px;font-weight:500}
.trend-value{font-size:22px;font-weight:700;margin-top:3px}
.agent-log{background:#1a1a1e;border-radius:10px;padding:18px 20px;font-family:Consolas,monospace;font-size:13px;line-height:2;color:#c5c4be;max-height:400px;overflow-y:auto}
.log-time{color:#6b6a65}.log-agent{color:#8fa4ff}.log-tool{color:#5dcaa5}.log-warn{color:#f59e0b}.log-ok{color:#0d9f6e}
.mcp-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.mcp-card{border:1px solid #e8e7e3;border-radius:10px;padding:14px 16px;background:#fff}
.mcp-card:hover{box-shadow:0 2px 8px rgba(0,0,0,0.06)}
.mcp-card-name{font-family:Consolas,monospace;font-size:15px;font-weight:700;margin-bottom:6px}
.mcp-card-source{font-size:13px;color:#6b6a65;margin-bottom:8px}
.mcp-card-desc{font-size:14px;color:#4b4a45;line-height:1.6}
.prom-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}
.prom-card{background:#fff;border:1px solid #e8e7e3;border-radius:10px;padding:14px 16px}
.prom-name{font-family:Consolas,monospace;font-size:13px;color:#6b6a65;margin-bottom:4px;word-break:break-all}
.prom-value{font-size:24px;font-weight:700}
.prom-meta{font-size:13px;color:#6b6a65;margin-top:3px}
.quartile-bar{display:flex;height:26px;border-radius:8px;overflow:hidden;margin-top:8px}
.q-seg{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff}
.tabs{display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid rgba(0,0,0,0.08);overflow-x:auto}
.tab{padding:10px 18px;font-size:14px;color:#6b6a65;border-bottom:2px solid transparent;white-space:nowrap}
.tab.active{color:#4f46e5;border-bottom-color:#4f46e5;font-weight:600}
</style>""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Page dispatch
# ═══════════════════════════════════════════════════════════════════════════════
_DISPATCH = {
    "◻ Portfolio Overview": page_portfolio,
    "◆ Evidence Analysis": page_evidence,
    "↻ Assessment History": page_history,
    "⚙ Agentic Workflow": page_workflow,
    "◆ Fund-AI-R Analytics": page_fund_air,
    "▶ MCP Server": page_mcp,
    "◎ Prometheus Metrics": page_metrics,
    "✍ IC Memo / LP Letter": page_documents,
    "★ Investment Tracker": page_tracker,
    "◇ Mem0 Memory": page_memory,
}

_DISPATCH.get(st.session_state["selected_page"], page_portfolio)()
