"""
streamlit/components/evidence_display.py

Reusable Streamlit components for rendering CS4 RAG evidence and
score justifications in the portfolio dashboard.

Data comes directly from FastAPI endpoints via requests — no imports
from app.services are needed so this runs cleanly in the Streamlit process.

FastAPI endpoints used:
  GET /api/v1/rag/justify/{ticker}/{dimension}   → JustifyResponse dict
  GET /api/v1/rag/evidence/{ticker}?dimension=.. → CompanyEvidenceListResponse dict
"""

import streamlit as st
import pandas as pd
import requests
from typing import Dict, List, Optional, Any

BASE_URL = "http://localhost:8000"

DIMENSIONS = [
    "data_infrastructure",
    "ai_governance",
    "technology_stack",
    "talent",
    "leadership",
    "use_case_portfolio",
    "culture",
]

LEVEL_COLORS: Dict[int, str] = {
    1: "#ef4444",   # red    — Nascent
    2: "#f97316",   # orange — Developing
    3: "#eab308",   # yellow — Adequate
    4: "#22c55e",   # green  — Good
    5: "#14b8a6",   # teal   — Excellent
}

LEVEL_NAMES: Dict[int, str] = {
    1: "Nascent",
    2: "Developing",
    3: "Adequate",
    4: "Good",
    5: "Excellent",
}

_STRENGTH_COLORS = {
    "strong":   "#22c55e",
    "moderate": "#eab308",
    "weak":     "#ef4444",
}


# ---------------------------------------------------------------------------
# Data fetching helpers (call FastAPI, return plain dicts)
# ---------------------------------------------------------------------------

def fetch_justification(ticker: str, dimension: str) -> Optional[Dict[str, Any]]:
    """Call GET /api/v1/rag/justify/{ticker}/{dimension}.

    Returns the JustifyResponse dict on success, or None on error.
    Caches per (ticker, dimension) in st.session_state.
    """
    cache_key = f"justification_{ticker}_{dimension}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/rag/justify/{ticker}/{dimension}",
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            st.session_state[cache_key] = data
            return data
        st.error(f"API error {r.status_code}: {r.text[:200]}")
    except requests.exceptions.ConnectionError:
        st.error("Cannot reach FastAPI. Make sure uvicorn is running on localhost:8000.")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
    return None


def fetch_all_justifications(ticker: str) -> Dict[str, Dict[str, Any]]:
    """Fetch justifications for all 7 dimensions, skipping failures."""
    results: Dict[str, Dict[str, Any]] = {}
    progress = st.progress(0, text="Fetching justifications...")
    for i, dim in enumerate(DIMENSIONS):
        progress.progress((i + 1) / len(DIMENSIONS), text=f"Generating: {dim}...")
        data = fetch_justification(ticker, dim)
        if data:
            results[dim] = data
    progress.empty()
    return results


def fetch_evidence(
    ticker: str, dimension: Optional[str] = None, limit: int = 10
) -> List[Dict[str, Any]]:
    """Call GET /api/v1/rag/evidence/{ticker}.

    Returns list of EvidenceItemResponse dicts.
    """
    params: Dict[str, Any] = {"limit": limit}
    if dimension:
        params["dimension"] = dimension
    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/rag/evidence/{ticker}",
            params=params,
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("evidence", [])
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# render_evidence_card  — accepts a JustifyResponse dict
# ---------------------------------------------------------------------------

def render_evidence_card(justification: Dict[str, Any]) -> None:
    """Render one dimension's justification card.

    justification is a JustifyResponse dict from FastAPI:
      dimension, score, level, level_name, evidence_strength,
      rubric_criteria, generated_summary, supporting_evidence, gaps_identified
    """
    level = int(justification.get("level", 3))
    score = float(justification.get("score", 0.0))
    color = LEVEL_COLORS.get(level, "#6b7280")
    strength = justification.get("evidence_strength", "weak")
    strength_color = _STRENGTH_COLORS.get(strength, "#6b7280")

    with st.container():
        # ── Header row ───────────────────────────────────────────────────────
        col1, col2, col3 = st.columns([4, 1, 1])
        with col1:
            dim_label = justification.get("dimension", "").replace("_", " ").title()
            st.markdown(f"### {dim_label}")
        with col2:
            st.markdown(
                f'<span style="background-color:{color};color:white;'
                f'padding:4px 12px;border-radius:12px;font-weight:bold;">'
                f'L{level}</span>',
                unsafe_allow_html=True,
            )
        with col3:
            st.markdown(f"**{score:.1f}**")

        # ── Evidence strength ─────────────────────────────────────────────────
        st.markdown(
            f"Evidence strength: "
            f"<span style='color:{strength_color};font-weight:bold;'>"
            f"{strength.title()}</span>",
            unsafe_allow_html=True,
        )

        # ── Rubric criteria ───────────────────────────────────────────────────
        rubric = justification.get("rubric_criteria", "")
        if rubric:
            st.info(f"**Rubric Match:** {rubric}")

        # ── LLM summary ───────────────────────────────────────────────────────
        summary = justification.get("generated_summary", "")
        if summary:
            with st.expander("AI-generated summary", expanded=False):
                st.write(summary)

        # ── Supporting evidence ───────────────────────────────────────────────
        evidence_items: List[Dict[str, Any]] = justification.get("supporting_evidence", [])[:5]
        st.markdown("**Supporting Evidence:**")
        if evidence_items:
            for ev in evidence_items:
                content = ev.get("content", "")
                source_type = ev.get("source_type", "unknown")
                confidence = float(ev.get("confidence", 0.0))
                source_url = ev.get("source_url", "")
                label = f"[{source_type}] {content[:60]}..."
                with st.expander(label, expanded=False):
                    st.write(content)
                    st.caption(f"Confidence: {confidence:.0%}")
                    if source_url:
                        st.markdown(f"[Source]({source_url})")
        else:
            st.caption("No supporting evidence retrieved.")

        # ── Gaps identified ───────────────────────────────────────────────────
        gaps: List[str] = justification.get("gaps_identified", [])
        if gaps:
            st.warning("**Gaps Identified:**")
            for gap in gaps:
                st.markdown(f"- {gap}")

        st.divider()


# ---------------------------------------------------------------------------
# render_evidence_summary_table — accepts dict of JustifyResponse dicts
# ---------------------------------------------------------------------------

def render_evidence_summary_table(justifications: Dict[str, Dict[str, Any]]) -> None:
    """Render compact summary table with level colour coding."""
    if not justifications:
        return

    data = []
    for dim, just in justifications.items():
        level = int(just.get("level", 3))
        data.append({
            "Dimension":  dim.replace("_", " ").title(),
            "Score":      round(float(just.get("score", 0.0)), 1),
            "Level":      f"L{level}",
            "Level Name": LEVEL_NAMES.get(level, just.get("level_name", "")),
            "Evidence":   just.get("evidence_strength", "").title(),
            "Items":      len(just.get("supporting_evidence", [])),
            "Gaps":       len(just.get("gaps_identified", [])),
        })

    df = pd.DataFrame(data)

    def _color_level(val: str) -> str:
        try:
            bg = LEVEL_COLORS.get(int(val[1]), "#ffffff")
            return f"background-color: {bg}; color: white; font-weight: bold;"
        except (IndexError, ValueError):
            return ""

    try:
        styled = df.style.map(_color_level, subset=["Level"])
    except AttributeError:
        styled = df.style.applymap(_color_level, subset=["Level"])  # type: ignore

    st.dataframe(styled, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# render_company_evidence_panel — full interactive panel with buttons
# ---------------------------------------------------------------------------

def render_company_evidence_panel(
    company_id: str,
    justifications: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Interactive evidence panel for a company.

    CS5 spec: receives pre-fetched justifications dict. If not provided,
    shows generate buttons to fetch via FastAPI.

    Args:
        company_id: ticker symbol
        justifications: pre-fetched Dict[dimension, JustifyResponse dict].
                        If None, shows interactive fetch UI.
    """
    ticker = company_id
    st.header(f"Evidence Analysis: {ticker}")

    cache_key_all = f"justifications_{ticker}"

    # If pre-fetched data was passed in, use it directly (CS5 spec path)
    if justifications is not None:
        st.session_state[cache_key_all] = justifications
    else:
        # Interactive fetch UI (utility path)
        col_btn, col_clear = st.columns([2, 1])
        with col_btn:
            if st.button(
                "Generate All 7 Dimensions",
                key=f"gen_all_{ticker}",
                type="primary",
                use_container_width=True,
            ):
                for dim in DIMENSIONS:
                    st.session_state.pop(f"justification_{ticker}_{dim}", None)
                st.session_state.pop(cache_key_all, None)
                fetched = fetch_all_justifications(ticker)
                st.session_state[cache_key_all] = fetched

        with col_clear:
            if st.button(
                "Clear Cache",
                key=f"clear_{ticker}",
                type="secondary",
                use_container_width=True,
            ):
                for dim in DIMENSIONS:
                    st.session_state.pop(f"justification_{ticker}_{dim}", None)
                st.session_state.pop(cache_key_all, None)
                st.rerun()

        st.markdown("---")
        st.markdown("**Generate a single dimension:**")
        sel_col, btn_col = st.columns([3, 1])
        with sel_col:
            selected_dim = st.selectbox(
                "Dimension",
                DIMENSIONS,
                format_func=lambda d: d.replace("_", " ").title(),
                key=f"dim_select_{ticker}",
                label_visibility="collapsed",
            )
        with btn_col:
            if st.button(
                "Generate",
                key=f"gen_single_{ticker}",
                use_container_width=True,
            ):
                st.session_state.pop(f"justification_{ticker}_{selected_dim}", None)
                with st.spinner(f"Generating {selected_dim}..."):
                    data = fetch_justification(ticker, selected_dim)
                if data:
                    cached = st.session_state.get(cache_key_all, {})
                    cached[selected_dim] = data
                    st.session_state[cache_key_all] = cached
                    st.success(f"{selected_dim} generated.")

    justifications = st.session_state.get(cache_key_all, {})

    if not justifications:
        st.info("Click **Generate All 7 Dimensions** or select a dimension above to start.")
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.markdown("---")
    levels         = [int(j.get("level", 3)) for j in justifications.values()]
    avg_level      = sum(levels) / len(levels) if levels else 0
    avg_score      = sum(float(j.get("score", 0)) for j in justifications.values()) / len(justifications)
    total_gaps     = sum(len(j.get("gaps_identified", [])) for j in justifications.values())
    total_evidence = sum(len(j.get("supporting_evidence", [])) for j in justifications.values())
    strong_count   = sum(1 for j in justifications.values() if j.get("evidence_strength") == "strong")
    weak_count     = sum(1 for j in justifications.values() if j.get("evidence_strength") == "weak")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Dimensions", len(justifications))
    m2.metric("Avg Score", f"{avg_score:.1f}")
    m3.metric("Avg Level", f"L{avg_level:.1f}")
    m4.metric("Total Gaps", total_gaps)
    m5.metric(
        "Evidence Items",
        total_evidence,
        help=f"Strong: {strong_count}  Weak: {weak_count}. "
             "0 means ChromaDB has no indexed chunks for this ticker/dimension.",
    )

    # ── Summary table ─────────────────────────────────────────────────────────
    render_evidence_summary_table(justifications)

    # ── Per-dimension tabs ────────────────────────────────────────────────────
    st.markdown("---")
    tab_labels = [d.replace("_", " ").title() for d in justifications.keys()]
    tabs = st.tabs(tab_labels)
    for tab, (dim, just) in zip(tabs, justifications.items()):
        with tab:
            render_evidence_card(just)
