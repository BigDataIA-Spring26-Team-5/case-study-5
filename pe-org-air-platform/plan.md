# CS5: Agentic Portfolio Intelligence — Implementation Plan & Context

> **Purpose:** This file captures full context so the next conversation can continue from Phase 2 without re-reading the codebase.

---

## Architecture Context

**Project:** PE Org-AI-R Platform — a PE AI Readiness assessment system with 4-stage pipeline (CS1→CS4). CS5 adds an MCP server + LangGraph agents on top.

**Stack:** FastAPI (Python 3.11+), Snowflake, Redis, ChromaDB, LiteLLM (OpenAI/Anthropic/Groq), Streamlit.

**Key pattern:** All singletons created in `app/core/lifespan.py` → attached to `app.state` → accessed via `app/core/dependencies.py` providers using `Depends()`.

**Portfolio companies:** NVDA, JPM, WMT, GE, DG (mapped in `app/config/company_mappings.py` as `CS3_PORTFOLIO`).

**Scoring formula:** `Org-AI-R = V^R + Synergy×β + PF×δ − H^R×λ` (α=0.60, β=0.12, δ=0.15, λ=0.25).

**7 Dimensions:** data_infrastructure, ai_governance, technology_stack, talent, leadership, use_case_portfolio, culture.

---

## Existing CS1-CS4 Services (what CS5 wraps)

| Service | Location | Key Methods |
|---------|----------|-------------|
| CS1Client | `app/services/integration/cs1_client.py` | `get_company(ticker)`, `list_companies()` — uses httpx to localhost:8000 |
| CS2Client | `app/services/integration/cs2_client.py` | `get_evidence(ticker, source_types, signal_categories)` — fetches from S3 directly (jobs, patents, techstack, glassdoor, SEC chunks) |
| CS3Client | `app/services/integration/cs3_client.py` | `get_assessment(ticker)` → returns `CompanyAssessment` with dimension_scores, org_air_score, vr, hr, synergy, pf. Also has static rubric data (`_RUBRIC_TEXT`, `SCORE_LEVELS`, `score_to_level()`) |
| CompositeScoringService | `app/services/composite_scoring_service.py` | `compute_orgair(ticker)` → full pipeline TC→VR→PF→HR→Synergy→OrgAIR. Also `compute_full_pipeline(tickers)` |
| JustificationGenerator | `app/services/justification/generator.py` | `generate_justification(ticker, dimension)` → `ScoreJustification` with cited evidence, gaps, LLM summary |
| HybridRetriever | `app/services/retrieval/hybrid.py` | `retrieve(query, k, filter_metadata)` → BM25+ChromaDB RRF fusion |
| ModelRouter | `app/services/llm/router.py` | `complete(task, messages)` — multi-provider LLM routing |

**Important constants in `composite_scoring_service.py`:** `COMPANY_SECTORS`, `COMPANY_NAMES`, `MARKET_CAP_PERCENTILES`, `CS3_PORTFOLIO` (re-exported from config).

**Settings** (`app/config/__init__.py`): Pydantic `Settings` with `ALPHA_VR_WEIGHT`, `BETA_SYNERGY_WEIGHT`, `LAMBDA_PENALTY`, `DELTA_POSITION`, `W_DATA_INFRA` through `W_CULTURE`, `HITL_SCORE_CHANGE_THRESHOLD`, `HITL_EBITDA_PROJECTION_THRESHOLD`.

---

## Execution Status

| Step | Phase | Task | Pts | Status |
|------|-------|------|-----|--------|
| 1 | 0 | Dependencies | — | DONE |
| 2 | 1 | CS4 Client + PortfolioDataService + Value Creation | 8 | DONE |
| 3 | 4 | Assessment History Tracking | 6 | DONE |
| 4 | 11 | Fund-AI-R Calculator | 5 | DONE |
| 5 | 12 | Prometheus Metrics | 5 | DONE |
| 6 | 2 | MCP Server Core (6 tools) | 12 |DONE |
| 7 | 3 | MCP Resources & Prompts | 8 | DONE |
| 8 | 7 | LangGraph State | 8 | DONE |
| 9 | 8 | Specialist Agents | 12 | DONE |
| 10 | 9 | Supervisor + HITL | 10 | DONE |
| 11 | 5 | Evidence Display Component | 6 | **TODO — START HERE** |
| 12 | 6 | Portfolio Dashboard (cs5_app.py) | 10 | TODO |
| 13 | 10 | DD Workflow Exercise | 10 | TODO |

**Done: 74 pts | Remaining: 26 pts**

---

## DONE — What Was Created

### Phase 0: requirements.txt
Added to end of `requirements.txt`:
```
mcp>=1.0.0
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-openai>=0.2.0
langchain-anthropic>=0.2.0
prometheus-client>=0.20.0
nest_asyncio>=1.6.0
```

### Phase 1: `app/services/integration/cs4_client.py`
- `CS4Client(justification_generator, hybrid_retriever)` — wraps JustificationGenerator + HybridRetriever
- `generate_justification(ticker, dimension)` → `JustificationResult` dataclass
- `generate_all_justifications(ticker)` → Dict of all 7 dimensions
- `search_evidence(query, ticker, k)` → list of dicts

### Phase 1: `app/services/portfolio_data_service.py`
- `PortfolioDataService(cs1_client, cs2_client, cs3_client, cs4_client, composite_scoring_service)`
- **This is the ONLY data source for MCP tools + agents**
- Methods: `get_company_assessment()`, `get_company_evidence()`, `generate_justification()`, `compute_org_air_score()`, `project_ebitda_impact()`, `run_gap_analysis()`, `get_portfolio_view()`
- Contains `EBITDACalculator` and `GapAnalyzer` instances internally
- Returns `CompanyView` / `PortfolioView` dataclasses (both have `.to_dict()`)

### Phase 1: `app/services/value_creation/ebitda.py`
- `EBITDACalculator.project(company_id, entry_score, target_score, h_r_score, sector)` → `EBITDAProjection`
- Uses `SECTOR_EBITDA_MULTIPLIERS` and `IMPLEMENTATION_COST_FACTOR` dicts
- HR risk adjustment: `hr_risk_factor = min(1.0, max(0.5, h_r_score / 80.0))`

### Phase 1: `app/services/value_creation/gap_analysis.py`
- `GapAnalyzer.analyze(company_id, dimension_scores, current_org_air, target_org_air)` → `GapAnalysisResult`
- Contains `DimensionGap` per dimension with current/target scores, priority, next-level criteria, improvement actions
- `IMPROVEMENT_PRIORITY` dict ranks dimensions by cost-effectiveness

### Phase 4: `app/services/tracking/history_service.py`
- `AssessmentHistoryService(cs3_client)` — in-memory cache of `AssessmentSnapshot` objects
- `record_assessment(company_id)`, `get_history(company_id, days)`, `calculate_trend(company_id)` → `AssessmentTrend`

### Phase 11: `app/services/analytics/fund_air.py`
- `FundAIRCalculator(cs3_client).calculate(fund_id, tickers)` → `FundMetrics`
- EV-weighted Fund-AI-R, sector quartiles, HHI concentration, leader/laggard counts
- `SECTOR_BENCHMARKS` dict for quartile calculation

### Phase 12: `app/services/observability/metrics.py`
- Prometheus metrics: `mcp_tool_calls_total`, `mcp_tool_duration_seconds`, `agent_invocations_total`, `agent_duration_seconds`, `hitl_approvals_total`, `cs_client_calls_total`
- Decorators: `@track_mcp_tool(name)`, `@track_agent(name)`, `@track_cs_client(service, endpoint)`

### Modified: `app/core/lifespan.py`
Added Section 7 after Task Store (line ~155):
```python
app.state.cs1_client = CS1Client()
app.state.cs3_client = CS3Client()
app.state.cs4_client = CS4Client(justification_generator=..., hybrid_retriever=...)
app.state.portfolio_data_service = PortfolioDataService(cs1, cs2, cs3, cs4, composite_scoring)
app.state.history_service = AssessmentHistoryService(cs3_client=...)
app.state.fund_air_calculator = FundAIRCalculator(cs3_client=...)
```

### Modified: `app/core/dependencies.py`
Added 6 providers before the correlation_id section:
```python
get_cs1_client, get_cs3_client, get_cs4_client,
get_portfolio_data_service, get_history_service, get_fund_air_calculator
```

---

## TODO — Remaining Phase Specs

### Phase 2: MCP Server Core — Task 9.2 (12 pts)

**New files:**
- `app/mcp/__init__.py`
- `app/mcp/server.py`

**6 MCP Tools** (all delegate to `PortfolioDataService`):
1. `calculate_org_air_score(company_id)` → `portfolio_data_service.compute_org_air_score()`
2. `get_company_evidence(company_id, dimension?, limit?)` → `portfolio_data_service.get_company_evidence()`
3. `generate_justification(company_id, dimension)` → `portfolio_data_service.generate_justification()`
4. `project_ebitda_impact(company_id, entry_score, target_score, h_r_score)` → `portfolio_data_service.project_ebitda_impact()`
5. `run_gap_analysis(company_id, target_org_air)` → `portfolio_data_service.run_gap_analysis()`
6. `get_portfolio_summary(fund_id)` → `portfolio_data_service.get_portfolio_view()`

**Entry point:** `python -m app.mcp.server` — stdio transport. Must use `mcp` library's `Server` class with `list_tools()` and `call_tool()` handlers.

### Phase 3: MCP Resources & Prompts — Task 9.3 (8 pts)

Added to `app/mcp/server.py`:

**Resources** (via `list_resources()` + `read_resource()` handlers):
- `orgair://parameters/v2.0` — returns alpha, beta, lambda, delta, dimension weights from `app/config/__init__.py` Settings
- `orgair://sectors` — returns `SECTOR_BENCHMARKS` from fund_air.py + `COMPANY_SECTORS` from composite_scoring_service

**Prompts** (via `list_prompts()` + `get_prompt()` handlers):
- `due_diligence_assessment(company_id)` — system prompt instructing agent to: get assessment → get evidence → generate justifications → run gap analysis → project EBITDA
- `ic_meeting_prep(company_id)` — system prompt for IC meeting: portfolio summary → company deep dive → risk flags → value creation plan

### Phase 7: LangGraph State — Task 10.1 (8 pts)

**New files:**
- `app/agents/__init__.py`
- `app/agents/state.py`

```python
class AgentMessage(TypedDict):
    role: str          # "supervisor", "sec_analyst", "scorer", "evidence", "value_creator", "human"
    content: str
    timestamp: str
    agent_name: Optional[str]

class DueDiligenceState(TypedDict):
    # Input
    company_id: str
    assessment_type: str   # "screening" | "limited" | "full"
    requested_by: str
    # Messages (append-only via operator.add)
    messages: Annotated[List[AgentMessage], operator.add]
    # Agent outputs
    sec_analysis: Optional[Dict]
    talent_analysis: Optional[Dict]
    scoring_result: Optional[Dict]
    evidence_justifications: Optional[Dict]
    value_creation_plan: Optional[Dict]
    # Control flow
    next_agent: Optional[str]
    requires_approval: bool
    approval_reason: Optional[str]
    approval_status: Optional[str]   # "pending" | "approved" | "rejected"
    approved_by: Optional[str]
    # Metadata
    started_at: Optional[str]
    completed_at: Optional[str]
    total_tokens: int
    error: Optional[str]
```

### Phase 8: Specialist Agents — Task 10.2 (12 pts)

**New file:** `app/agents/specialists.py`

- `MCPToolCaller` — httpx async client that calls MCP server tools (POST to localhost or in-process). Has method `call_tool(tool_name, arguments) -> dict`.
- 4 `@tool`-decorated wrappers: `get_org_air_score(company_id)`, `get_evidence(company_id, dimension)`, `get_justification(company_id, dimension)`, `get_gap_analysis(company_id, target_score)`
- `SECAnalysisAgent.analyze(state) -> state` — calls get_evidence for SEC source types, summarizes with ChatOpenAI(gpt-4o)
- `ScoringAgent.calculate(state) -> state` — calls get_org_air_score, sets `requires_approval=True` if score outside [40, 85]
- `EvidenceAgent.justify(state) -> state` — calls get_justification for top 3 dimensions
- `ValueCreationAgent.plan(state) -> state` — calls get_gap_analysis, projects EBITDA, sets `requires_approval=True` if EBITDA impact > 5%

### Phase 9: Supervisor with HITL — Task 10.3 (10 pts)

**New file:** `app/agents/supervisor.py`

- `supervisor_node(state)` — routing logic:
  1. If `requires_approval` and `approval_status == "pending"` → "hitl_approval"
  2. If `sec_analysis` missing → "sec_analyst"
  3. If `scoring_result` missing → "scorer"
  4. If `evidence_justifications` missing → "evidence_agent"
  5. If `value_creation_plan` missing and type != "screening" → "value_creator"
  6. Else → "complete"
- `hitl_approval_node(state)` — auto-approves in exercise mode, logs to Prometheus `hitl_approvals_total`
- `complete_node(state)` — sets `completed_at`, compiles final report
- `create_due_diligence_graph()` → compiled `StateGraph` with `MemorySaver()` checkpointer

**Graph edges:**
```
START → supervisor
supervisor → {sec_analyst, scorer, evidence_agent, value_creator, hitl_approval, complete} (conditional)
sec_analyst → supervisor
scorer → supervisor
evidence_agent → supervisor
value_creator → supervisor
hitl_approval → supervisor
complete → END
```

### Phase 5: Evidence Display — Task 9.5 (6 pts)

**New file:** `streamlit/components/evidence_display.py`

- `LEVEL_COLORS = {1: "red", 2: "orange", 3: "#FFD700", 4: "green", 5: "teal"}`
- `render_evidence_card(justification: dict)` — st.container with score badge (L1-L5 colored), evidence list, gaps
- `render_company_evidence_panel(company_id, justifications: dict)` — st.tabs for 7 dimensions, calls render_evidence_card per tab
- `render_evidence_summary_table(justifications: dict)` — pandas DataFrame with st.dataframe + gradient styling

### Phase 6: Portfolio Dashboard — Task 9.6 (10 pts)

**New file:** `streamlit/cs5_app.py`

- Uses `nest_asyncio.apply()` at top
- Sidebar: fund_id text input, connection status indicator
- `@st.cache_data(ttl=300)` wrapper for portfolio data loading
- Calls `PortfolioDataService.get_portfolio_view(fund_id)` via HTTP to FastAPI or in-process
- **Metrics row:** Fund-AI-R, total companies, leaders, laggards (st.metric columns)
- **VR vs HR scatter:** Plotly scatter (x=vr_score, y=hr_score, size=org_air_score, color=sector), threshold lines at 60
- **Company table:** st.dataframe with conditional gradient on org_air_score column
- **Evidence panel:** imports `render_company_evidence_panel` from Phase 5, triggered by company selection

### Phase 10: DD Workflow Exercise — Task 10.4 (10 pts)

**New file:** `exercises/agentic_due_diligence.py`

```python
async def main():
    graph = create_due_diligence_graph()
    initial_state = {
        "company_id": "NVDA",
        "assessment_type": "full",
        "requested_by": "analyst@pe-fund.com",
        "messages": [],
        "requires_approval": False,
        "total_tokens": 0,
        ...
    }
    config = {"configurable": {"thread_id": "dd-nvda-001"}}
    result = await graph.ainvoke(initial_state, config)
    # Print: org_air score, HITL status, approval, dimension scores
```

---

## Verification Checklist
- [ ] `python -m app.mcp.server` starts without error
- [ ] Claude Desktop can list and call all 6 tools
- [ ] Stop FastAPI → MCP tools error (no mock data)
- [ ] `python exercises/agentic_due_diligence.py` runs full DD for NVDA
- [ ] NVDA (~90) triggers HITL approval gate
- [ ] `streamlit run streamlit/cs5_app.py` shows portfolio with real scores
- [ ] Prometheus counters populate after tool calls
