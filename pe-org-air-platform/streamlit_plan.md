# CS5 Streamlit Dashboard — Implementation Plan

> **Goal**: Implement a Streamlit dashboard that matches the HTML mockup in `cs5_dashboard_mockup.html` exactly. 10 pages total (7 core + 3 bonus). All data must come from CS1-CS4 services — NO hardcoded/mock data in production code, but use fallback demo data ONLY when CS1-CS4 services are not running (clearly labeled).

---

## 0. Prerequisites & Setup

```bash
pip install streamlit plotly pandas nest-asyncio structlog httpx python-docx prometheus-client mem0ai chromadb
```

Add `nest_asyncio` at the top of every Streamlit file:
```python
import nest_asyncio
nest_asyncio.apply()
```

---

## 1. File Structure

```
cs5_agentic_portfolio/
├── src/
│   ├── dashboard/
│   │   ├── app.py                          # Main Streamlit app (entry point)
│   │   ├── components/
│   │   │   ├── evidence_display.py         # Task 9.5 — evidence cards
│   │   │   ├── agent_flow.py              # Agent workflow visualization
│   │   │   ├── document_generator.py      # Bonus — IC Memo / LP Letter
│   │   │   └── memory_panel.py            # Bonus — Mem0 display
│   │   └── pages/
│   │       ├── portfolio_overview.py       # Page 1 — Task 9.6
│   │       ├── evidence_analysis.py        # Page 2 — Task 9.5
│   │       ├── assessment_history.py       # Page 3 — Task 9.4
│   │       ├── agentic_workflow.py         # Page 4 — Tasks 10.1-10.4
│   │       ├── fund_air_analytics.py       # Page 5 — Task 10.5
│   │       ├── mcp_server_view.py          # Page 6 — Tasks 9.2, 9.3
│   │       ├── prometheus_metrics.py       # Page 7 — Task 10.6
│   │       ├── document_gen_page.py        # Page 8 — Bonus IC/LP
│   │       ├── investment_tracker.py       # Page 9 — Bonus ROI
│   │       └── mem0_memory.py             # Page 10 — Bonus Mem0
│   ├── services/
│   │   ├── integration/
│   │   │   └── portfolio_data_service.py   # Task 9.1
│   │   ├── tracking/
│   │   │   └── assessment_history.py       # Task 9.4
│   │   ├── analytics/
│   │   │   └── fund_air.py                # Task 10.5
│   │   ├── observability/
│   │   │   └── metrics.py                 # Task 10.6
│   │   ├── memory/
│   │   │   └── mem0_config.py             # Bonus Mem0
│   │   ├── documents/
│   │   │   ├── ic_memo_generator.py       # Bonus IC Memo
│   │   │   └── lp_letter_generator.py     # Bonus LP Letter
│   │   ├── cs1_client.py
│   │   ├── cs2_client.py
│   │   ├── cs3_client.py
│   │   ├── cs4_client.py
│   │   └── value_creation/
│   │       ├── ebitda.py
│   │       └── gap_analysis.py
│   ├── mcp/
│   │   └── server.py                      # Task 9.2, 9.3
│   ├── agents/
│   │   ├── state.py                       # Task 10.1
│   │   ├── specialists.py                 # Task 10.2
│   │   └── supervisor.py                  # Task 10.3
│   └── exercises/
│       └── agentic_due_diligence.py       # Task 10.4
├── tests/
│   └── test_mcp_integration.py
└── README.md
```

---

## 2. Main App — `dashboard/app.py`

This is the Streamlit entry point. It sets up the sidebar and routes to pages.

### Sidebar (matches mockup exactly)
```
Sidebar layout:
├── Title: "PE Org-AI-R" (bold, 18px equivalent)
├── Subtitle: "AGENTIC INTELLIGENCE" (small caps, muted)
├── Input: "Fund ID" → text_input, default "growth_fund_v"
├── Select: "Company" → selectbox with ["NVDA — NVIDIA", "MSFT — Microsoft", "AMZN — Amazon", "JPM — JPMorgan", "UNH — UnitedHealth"]
├── Select: "Assessment Type" → selectbox with ["Full", "Limited", "Screening"]
├── Label: "Pages"
├── Radio/selectbox navigation with these options:
│   ├── "◻ Portfolio Overview"
│   ├── "◆ Evidence Analysis"
│   ├── "↻ Assessment History"
│   ├── "⚙ Agentic Workflow"
│   ├── "◆ Fund-AI-R Analytics"
│   ├── "▶ MCP Server"
│   ├── "◎ Prometheus Metrics"
│   ├── --- separator ---
│   ├── Label: "BONUS (+20 PTS)"
│   ├── "✍ IC Memo / LP Letter"
│   ├── "★ Investment Tracker"
│   └── "◇ Mem0 Memory"
└── Status: green dot + "CS1-CS4 services connected" (or red if not)
```

**Implementation**: Use `st.sidebar.radio()` for navigation. Store selection in `st.session_state`. Use `st.sidebar.markdown()` with `unsafe_allow_html=True` for the status indicator with green dot.

### Data Loading Pattern
```python
@st.cache_data(ttl=300)
def load_portfolio(_fund_id: str):
    async def _load():
        return await portfolio_data_service.get_portfolio_view(_fund_id)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_load())
    finally:
        loop.close()
```

**Fallback**: Wrap every data load in try/except. On failure, show `st.warning("CS1-CS4 not available — showing demo data")` and load from a `_demo_data()` function. This way the dashboard works for grading even if services are down, but it's clear it's NOT hardcoded production data.

---

## 3. Page-by-Page Implementation

### PAGE 1: Portfolio Overview (`pages/portfolio_overview.py`) — Task 9.6, 10 pts

**Title**: "Portfolio Overview"
**Subtitle**: "Fund-level AI-readiness metrics from CS1-CS4 · {fund_id}"

**Row 1 — 4 metric cards** (use `st.columns(4)`):
| Label | Value | Delta |
|-------|-------|-------|
| Fund-AI-R | `{fund_air:.1f}` | `▲ +{delta:.1f} vs entry` (green) |
| Companies | `{count}` | "Active positions" (gray) |
| Avg V^R | `{avg_vr:.1f}` | `▲ +{vr_delta:.1f}` (green) |
| Avg Delta | `+{avg_delta:.1f}` | "All improving" (green) |

Use `st.metric()` for each.

**Chart 1 — Bubble Scatter**: "AI-Readiness Map — V^R vs H^R"
- Use `plotly.express.scatter()` with `size="org_air"`, `color="sector"`, `hover_name="name"`
- X-axis: "V^R (Idiosyncratic)", Y-axis: "H^R (Systematic)"
- Add dashed threshold lines at x=60 and y=60 using `fig.add_hline()` and `fig.add_vline()`
- Color scheme: Technology=#6366f1, Financial=#f59e0b, Healthcare=#14b8a6

**Table — Portfolio Companies**:
- Columns: Ticker, Name, Sector, Org-AI-R, V^R, H^R, Synergy, Delta, Evidence
- Org-AI-R column: color-coded badges (≥70 green, ≥55 amber, <55 orange)
- Delta column: green text with + prefix
- Use `st.dataframe()` with `column_config` for formatting, OR build with `st.markdown()` + HTML table for exact badge styling

**Row 2 — Two charts side by side** (`st.columns(2)`):
- Left: "Org-AI-R by Company" — horizontal bar chart (`plotly.express.bar`, orientation='h')
- Right: "Sector Allocation" — doughnut chart (`plotly.express.pie`, hole=0.4)

---

### PAGE 2: Evidence Analysis (`pages/evidence_analysis.py`) — Task 9.5, 6 pts

**Title**: "Evidence Analysis: {company_id}"
**Subtitle**: "CS4 RAG justifications across 7 Org-AI-R dimensions"

**Row 1 — 4 metrics**: Total Evidence, Avg Level, Strong Evidence (x/7), Dimensions Scored

**Dimension Tabs**: Use `st.tabs()` with 7 tabs:
`["Data Infra", "AI Governance", "Tech Stack", "Talent", "Leadership", "Use Cases", "Culture"]`

**Inside each tab — Evidence Card** (use `components/evidence_display.py`):
```
┌─────────────────────────────────────────────────────┐
│ Data Infrastructure          [L4 badge]  78.5       │
│                                        [Strong]     │
│ ┌─ Rubric: Enterprise-grade data platform...  ─────┐│
│                                                     │
│ Supporting Evidence (from CS4 RAG)                   │
│ ├── [SEC 10-K] Investment in data center... — 92%   │
│ ├── [JOB POST] Senior Data Platform Eng... — 88%    │
│ ├── [PATENT] US-2024-0142892: Distrib... — 85%      │
│ └── [PRESS] DGX Cloud partnership...     — 80%      │
│                                                     │
│ Gaps: [Data lineage tooling] [Cross-region latency] │
└─────────────────────────────────────────────────────┘
```

- Badge colors: L5=#14b8a6, L4=#22c55e, L3=#eab308, L2=#f97316, L1=#ef4444
- Evidence strength badges: Strong=green, Moderate=amber, Weak=red
- Source type tags: monospace, small, purple background
- Gap tags: amber background, rounded
- Use `st.expander()` for each evidence item, OR render with `st.markdown(unsafe_allow_html=True)`

**Dimension Summary Table**: All 7 dimensions in one table with Score, Level badge, Evidence strength badge, Items count, Gaps count

**Radar Chart**: `plotly` radar/scatterpolar chart with company scores vs sector average (dashed line)

---

### PAGE 3: Assessment History (`pages/assessment_history.py`) — Task 9.4, 6 pts

**Title**: "Assessment History: {company_id}"

**Row 1 — 5 trend cards** (`st.columns(5)`):
| Current Org-AI-R | Entry Org-AI-R | Delta Since Entry | 30-Day Delta | Trend |
|82.1|74.0|+8.1 (green)|+2.3 (green)|▲ Improving (green)|

**Line Chart**: "Org-AI-R Score Over Time"
- 3 lines: Org-AI-R (solid purple, filled area), V^R (dashed green), H^R (dashed amber)
- X-axis: timestamps, Y-axis: scores (60-90 range)
- Use `plotly.graph_objects.Scatter` with `fill='tozeroy'` for Org-AI-R

**Snapshots Table**: Timestamp (monospace), Type, Org-AI-R (bold), V^R, H^R, Synergy, Evidence count, Assessor ID

---

### PAGE 4: Agentic Workflow (`pages/agentic_workflow.py`) — Tasks 10.1-10.4, 38 pts total

**Title**: "Agentic Due Diligence: {company_id}"
**Subtitle**: "LangGraph supervisor + specialist agents · {assessment_type} assessment"

**HITL Banner** (conditional, show when `requires_approval=True`):
```
⚠ HITL Approval Required — Score 82.1 is outside normal range [40, 85].
                                              [Approve] [Reject]
```
- Amber background, amber border
- Use `st.warning()` container with `st.columns()` for approve/reject buttons inside

**Agent Flow Visualization**: Horizontal pipeline showing nodes:
```
[Supervisor] → [SEC Analyst] → [Supervisor] → [Scorer] → [Supervisor] → [HITL Gate] → [Evidence Agent] → [Value Creator] → [Complete]
```
- Node states: done=green bg, active=purple bg+glow, hitl=amber bg, pending=gray
- Build with `st.markdown()` + inline HTML/CSS using flexbox, OR use `st.columns()` with styled containers
- Each node shows name + small subtitle (e.g., "CS2 evidence", "CS3 scores")

**Two columns** (`st.columns(2)`):

**Left — Agent Outputs**:
- 3 cards (use `st.container()` or `st.expander()`):
  - "SEC Analysis Agent [Done]" — findings count, dimensions, key signals
  - "Scoring Agent [Done]" — Org-AI-R, V^R, H^R, confidence, HITL flag
  - "Evidence Agent [Running]" — current status message

**Right — Execution Log**:
- Dark background terminal-style log panel
- Use `st.code()` or `st.markdown()` with dark background HTML
- Color-coded: timestamps=gray, info=blue, warn=amber, success=green, agent=purple
- Show each step with timestamp, agent name, action

**Right — DueDiligenceState viewer**:
- JSON display of current state
- Use `st.json()` or styled code block
- Show: company_id, assessment_type, requires_approval, approval_status, next_agent, messages count, total_tokens, started_at

**Run button**: `st.button("▶ Run Due Diligence")` that triggers `agentic_due_diligence.run_due_diligence()`

---

### PAGE 5: Fund-AI-R Analytics (`pages/fund_air_analytics.py`) — Task 10.5, 5 pts

**Title**: "Fund-AI-R Analytics"
**Subtitle**: "EV-weighted portfolio aggregation · FundAIRCalculator"

**Row 1 — 4 metrics**:
| Fund-AI-R (EV-weighted) | Total EV ($MM) | AI Leaders (≥70) | AI Laggards (<50) |
|68.4|4,820|3 (green)|0 (red)|

**Two columns**:

**Left card — Quartile Distribution**:
- Horizontal stacked bar: Q1(green) Q2(teal) Q3(amber) Q4(coral)
- Show count in each segment
- Below: "Based on sector-specific benchmarks from SECTOR_BENCHMARKS"
- Below that: "Concentration (Sector HHI)" = 0.5200, "Moderate concentration — 3/5 companies in Technology"

**Right card — EV-Weighted Breakdown table**:
| Company | EV ($MM) | Weight | Org-AI-R | Contribution |
|NVDA|1,800|37.3%|82.1|30.6|

**EBITDA Impact Projection table**:
| Company | Entry Score | Target | Conservative | Base | Optimistic | Risk-Adjusted |
- Conservative = muted gray, Base = bold, Optimistic = green, Risk-Adjusted = bold

---

### PAGE 6: MCP Server (`pages/mcp_server_view.py`) — Tasks 9.2 & 9.3, 20 pts

**Title**: "MCP Server"
**Subtitle**: "Tools, Resources & Prompts — pe-orgair-server"

**Section 1 — Tools (6 registered)**:
Table with columns: Tool Name (monospace, purple), CS Source, Endpoint (monospace), Description
- `calculate_org_air_score` → CS3
- `get_company_evidence` → CS2
- `generate_justification` → CS4
- `project_ebitda_impact` → CS3 (local)
- `run_gap_analysis` → CS3+CS4
- `get_portfolio_summary` → CS1

**Section 2 — Resources**:
Two styled cards:
- URI: `orgair://parameters/v2.0` (monospace, purple) → Name, Description with parameter values
- URI: `orgair://sectors` → Sector baselines

**Section 3 — Prompts**:
Two template blocks (dashed border, light gray bg, pre-formatted):
- `due_diligence_assessment` with 4-step template
- `ic_meeting_prep`

---

### PAGE 7: Prometheus Metrics (`pages/prometheus_metrics.py`) — Task 10.6, 5 pts

**Title**: "Prometheus Metrics"
**Subtitle**: "Observability for MCP tools, LangGraph agents, CS1-CS4 clients"

**Section 1 — MCP Server Metrics** (3 cards in row):
| mcp_tool_calls_total | mcp_tool_duration_seconds | mcp_tool_errors |
|1,247 (counter)|0.34s (histogram p50)|12 (red, counter)|

**Section 2 — LangGraph Agent Metrics** (3 cards):
| agent_invocations_total | agent_duration_seconds | hitl_approvals_total |
|486|2.1s|23 (amber)|

**Section 3 — CS1-CS4 Client Metrics** (3 cards):
| cs1: 312 | cs2: 589 | cs3: 445 |

**Tool Call Breakdown table**:
| Tool | Total | Success (green) | Error (red) | Avg Duration | p95 Duration |

**Stacked bar chart**: "Tool Call Volume (last 7 days)"
- 4 series: calculate_org_air (purple), get_evidence (teal), generate_justification (amber), Other (gray)
- Stacked, 7 days on x-axis

---

### PAGE 8: IC Memo / LP Letter (`pages/document_gen_page.py`) — Bonus +5/+5

**Title**: "Document Generator"
**Subtitle**: "IC Memo & LP Letter — auto-generated from CS1-CS4 data · Download as .docx"

**Two columns**:

**Left — IC Memo Generator**:
- Description text
- Company selectbox
- Preview card (light gray bg, bordered):
  - Header: "CONFIDENTIAL" (small caps), "Investment Committee Memorandum", date
  - Fields: Company, Sector, Org-AI-R (with level), Confidence interval, V^R, H^R, Synergy
  - Sections: Executive Summary, Dimension Scores (inline), EBITDA Impact, Recommendation
- `st.download_button("Download IC Memo (.docx)")` → calls `ic_memo_generator.generate()`

**Right — LP Letter Generator**:
- Reporting period selectbox (Q1 2026, Q4 2025, Q3 2025)
- Preview card:
  - Header: "CONFIDENTIAL — For Limited Partners Only", "Quarterly LP Update: Growth Fund V"
  - "Dear Limited Partners," opening
  - Fund Performance: 4 colored metric boxes (Fund-AI-R, Portfolio count, AI Leaders, Sector HHI)
  - Portfolio Highlights: per-company summary paragraph
  - Outlook paragraph
  - Signature
- `st.download_button("Download LP Letter (.docx)")` → calls `lp_letter_generator.generate()`

**Below both — Pipeline Log card**:
- Shows 7-step generation flow in terminal style
- Steps: MCP calls → LangGraph assembly → python-docx render

**Implementation**: Use `python-docx` to generate actual .docx files. `st.download_button()` with the bytes output.

---

### PAGE 9: Investment Tracker (`pages/investment_tracker.py`) — Bonus +5

**Title**: "Investment Tracker with ROI"

**Row 1 — 4 metrics**:
| Total Invested | Projected ROI | Active Initiatives | Avg Score Lift |
|$12.4M|3.2x (green)|14|+4.8 (green)|

**Investment Initiatives Table**:
| Company | Initiative | Dimension | Investment | Status | Score Impact | EBITDA Impact | ROI |
- Status badges: Active=green, Planning=amber, Scoping=orange
- ROI: green for actual, amber for estimated

**Two charts** (`st.columns(2)`):
- Left: "Investment by Dimension" — doughnut (Data Infra, Tech Stack, AI Governance, Culture, Use Cases)
- Right: "ROI by Company" — horizontal bar chart

---

### PAGE 10: Mem0 Memory (`pages/mem0_memory.py`) — Bonus +5

**Title**: "Mem0 Semantic Memory"
**Subtitle**: "Agent memory layer for cross-session context persistence"

**Row 1 — 4 metrics**: Total Memories, Companies Tracked, Avg Relevance, Memory Sessions

**Description paragraph**: Explain how Mem0 integrates with CS5 (vector embeddings, similarity search, cross-session persistence)

**Two columns**:

**Left — Recent Memories**:
4 memory cards, each with:
- Header: "{company} — {context type}" + relevance badge (0.94, 0.91, etc.)
- Body: quoted memory content
- Footer: session ID, agent name, memory type (monospace, small)
- Types: `assessment_result`, `evidence_summary`, `gap_analysis`, `user_preference`

**Right**:
- **Memory Architecture card**: Code snippet showing `mem0_config.py` (Chroma vector store + OpenAI LLM config)
- **Integration Points card**: 4 rows with colored badges:
  - WRITE (purple): after agent completes → `memory.add()`
  - READ (green): before workflow starts → `memory.search()`
  - COMPARE (amber): trend analysis uses historical memories
  - PERSIST (blue): user preferences stored over time
- **Memory Search**: text input + search button (calls `memory.search()`)

---

## 4. Styling Guidelines

### Custom CSS (inject via `st.markdown` at top of app.py)
```python
st.markdown("""
<style>
    /* Metric cards */
    [data-testid="stMetric"] {
        background: white;
        border: 1px solid #e8e7e3;
        border-radius: 10px;
        padding: 16px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }
    /* Badge styles */
    .badge-l5 { background: #f0fdfa; color: #0f766e; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-l4 { background: #ecfdf5; color: #065f46; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-l3 { background: #fffbeb; color: #92400e; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-l2 { background: #fef2f2; color: #b45309; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-l1 { background: #fef2f2; color: #991b1b; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-strong { background: #ecfdf5; color: #065f46; }
    .badge-moderate { background: #fffbeb; color: #92400e; }
    .gap-tag { background: #fffbeb; color: #92400e; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; margin-right: 6px; display: inline-block; }
    .ev-source { font-family: monospace; font-size: 11px; color: #6366f1; background: #eef2ff; padding: 2px 6px; border-radius: 4px; }
    /* Agent flow nodes */
    .agent-node { display: inline-block; min-width: 90px; padding: 8px 12px; border-radius: 8px; border: 1.5px solid #e8e7e3; text-align: center; font-size: 12px; font-weight: 500; margin: 0 4px; }
    .agent-done { border-color: #10b981; background: #ecfdf5; color: #065f46; }
    .agent-active { border-color: #6366f1; background: #eef2ff; color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,0.12); }
    .agent-hitl { border-color: #f59e0b; background: #fffbeb; color: #92400e; }
    .agent-pending { border-color: #e8e7e3; color: #9b9baa; }
    /* Log panel */
    .log-panel { background: #1e1e2e; border-radius: 10px; padding: 14px 16px; font-family: monospace; font-size: 11px; line-height: 1.8; color: #a6adc8; }
    .log-time { color: #585b70; }
    .log-info { color: #89b4fa; }
    .log-warn { color: #f9e2af; }
    .log-success { color: #a6e3a1; }
    .log-agent { color: #cba6f7; }
    /* Status dot */
    .status-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
    .status-green { background: #10b981; }
    .status-red { background: #ef4444; }
    /* Resource card */
    .resource-uri { font-family: monospace; font-size: 12px; color: #6366f1; }
    /* Prompt template */
    .prompt-template { background: #fafaf8; border: 1px dashed #e8e7e3; border-radius: 10px; padding: 14px 16px; white-space: pre-line; }
    /* Prometheus card */
    .prom-name { font-family: monospace; font-size: 11px; color: #9b9baa; }
</style>
""", unsafe_allow_html=True)
```

### Color Scheme
```
Primary accent: #6366f1 (indigo)
Green: #10b981 / bg: #ecfdf5 / text: #065f46
Red: #ef4444 / bg: #fef2f2 / text: #991b1b
Amber: #f59e0b / bg: #fffbeb / text: #92400e
Blue: #3b82f6 / bg: #eff6ff / text: #1e40af
Teal: #14b8a6
Purple: #8b5cf6
Coral: #f97316
Background: #f8f7f4
Card: #ffffff
Border: #e8e7e3
Text: #1a1a2e
Text secondary: #6b6b80
Text muted: #9b9baa
```

### Plotly Theme
Apply consistent plotly template across all charts:
```python
import plotly.io as pio
pio.templates["pe_orgair"] = go.layout.Template(
    layout=dict(
        font=dict(family="DM Sans, sans-serif"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        colorway=["#6366f1", "#14b8a6", "#f59e0b", "#f97316", "#8b5cf6", "#ef4444"],
    )
)
pio.templates.default = "pe_orgair"
```

---

## 5. Key Implementation Notes

### Data Flow
```
CS1 (companies, portfolios) ──┐
CS2 (evidence, signals)  ─────┤
CS3 (scores, assessments) ────┼──→ PortfolioDataService ──→ Streamlit pages
CS4 (RAG, justifications) ────┘
```

### Async Pattern (use everywhere)
```python
import asyncio
import nest_asyncio
nest_asyncio.apply()

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
```

### Session State Keys
```python
st.session_state keys:
  - "fund_id": str
  - "selected_company": str (ticker)
  - "assessment_type": str
  - "selected_page": str
  - "dd_result": DueDiligenceState (after running workflow)
  - "dd_running": bool
  - "hitl_decision": str ("approved" | "rejected" | None)
```

### Evidence Display Component Pattern
```python
def render_evidence_card(justification):
    level_colors = {1: "#ef4444", 2: "#f97316", 3: "#eab308", 4: "#22c55e", 5: "#14b8a6"}
    # ... render with st.markdown(unsafe_allow_html=True)
```

### Document Generation (python-docx)
```python
from docx import Document
from docx.shared import Inches, Pt
from io import BytesIO

def generate_ic_memo(company_data, scores, evidence, projections) -> bytes:
    doc = Document()
    # ... build document
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

---

## 6. Run Command

```bash
cd cs5_agentic_portfolio/src
streamlit run dashboard/app.py --server.port 8501
```

---

## 7. Checklist Before Submission

- [ ] All 10 pages render and navigate correctly
- [ ] Data comes from CS1-CS4 services (with graceful fallback)
- [ ] Scatter chart has threshold lines at 60/60
- [ ] Evidence tabs switch and show per-dimension data
- [ ] Assessment history shows trend line chart
- [ ] Agent flow visualization shows node states
- [ ] HITL banner appears when score > 85 or < 40
- [ ] Execution log renders in terminal style
- [ ] Fund-AI-R shows EV-weighted calculation
- [ ] MCP tools table shows all 6 tools
- [ ] Prometheus metrics cards show counter/histogram values
- [ ] IC Memo downloads as .docx
- [ ] LP Letter downloads as .docx
- [ ] Investment tracker shows ROI table and charts
- [ ] Mem0 page shows memory cards and integration points
- [ ] `pytest tests/ -v` passes
- [ ] No hardcoded data in production code paths