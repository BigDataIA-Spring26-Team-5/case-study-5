# CS5: Agentic Portfolio Intelligence — Requirements Checklist

> **Source:** PE_OrgAIR_CaseStudy5_Agentic_v4.pdf
> **Due:** March 27, 2026 at 3:59 PM
> **Weight:** 8% of final grade (100 base + 20 bonus points)
> **Prerequisite:** Working CS1, CS2, CS3, CS4 implementations
> **CRITICAL:** No mock data allowed. All agent tools must call actual CS1-CS4 services.

---

## Part I — Lab 9: MCP Server + Integration (50 pts)

### Task 9.1: Portfolio Data Service (8 pts) [REQUIRED]

**File:** `services/integration/portfolio_data_service.py`
**Existing:** `app/services/portfolio_data_service.py`

- [x] **9.1.1** Import and use CS1-CS4 clients (`CS1Client`, `CS2Client`, `CS3Client`, `CS4Client`)
- [x] **9.1.2** Define `PortfolioCompanyView` dataclass with all fields:
  - `company_id: str`
  - `ticker: str`
  - `name: str`
  - `sector: str`
  - `org_air: float`
  - `vr_score: float`
  - `hr_score: float`
  - `synergy_score: float`
  - `dimension_scores: Dict[str, float]`
  - `confidence_interval: tuple`
  - `entry_org_air: float`
  - `delta_since_entry: float`
  - `evidence_count: int`
- [x] **9.1.3** `PortfolioDataService.__init__()` accepts `cs1_url`, `cs2_url`, `cs3_url` and initializes all 4 CS clients
- [x] **9.1.4** `async get_portfolio_view(fund_id: str) -> List[PortfolioCompanyView]`:
  - Calls `cs1.get_portfolio_companies(fund_id)` to get company list
  - For each company, calls `cs3.get_assessment(company.ticker)` for scores
  - For each company, calls `cs2.get_evidence(company.ticker)` for evidence
  - Calls `_get_entry_score(company.company_id)` for entry Org-AI-R
  - Computes `delta_since_entry = assessment.org_air_score - entry_score`
  - Populates `evidence_count = len(evidence)`
  - Returns list of `PortfolioCompanyView`
- [x] **9.1.5** `async _get_entry_score(company_id: str) -> float` (queries CS1 portfolio_positions table)
- [x] **9.1.6** Singleton instance `portfolio_data_service = PortfolioDataService()` at module level
- [x] **9.1.7** Uses `structlog` for logging
- [x] **9.1.8** NO hardcoded/mock data — all data comes from CS1-CS4 API calls

---

### Task 9.2: MCP Server Core (12 pts) [MCP]

**File:** `mcp/server.py`
**Existing:** `app/mcp/server.py`

#### v4 FIX Requirements:
- [x] **9.2.1** All clients initialized at **module level** (not inside functions)
- [x] **9.2.2** Import `datetime` explicitly (`from datetime import datetime`)

#### Server Setup:
- [x] **9.2.3** Create MCP `Server("pe-orgair-server")`
- [x] **9.2.4** Import `portfolio_data_service` from integration module
- [x] **9.2.5** Initialize module-level clients: `cs2_client`, `cs3_client`, `cs4_client`
- [x] **9.2.6** Import `ebitda_calculator` and `gap_analyzer` from value creation services

#### Tools — `@mcp_server.list_tools()` (minimum 6 tools):
- [x] **9.2.7** **`calculate_org_air_score`** tool:
  - Input: `company_id` (string, required)
  - Calls `cs3_client.get_assessment(company_id)`
  - Returns: `company_id`, `org_air`, `vr_score`, `hr_score`, `synergy_score`, `confidence_interval`, `dimension_scores`
- [x] **9.2.8** **`get_company_evidence`** tool:
  - Input: `company_id` (required), `dimension` (enum of 7 dims + "all", default "all"), `limit` (int, default 10)
  - Calls `cs2_client.get_evidence()`
  - Returns: list of `{source_type, content[:500], confidence, signal_category}`
- [x] **9.2.9** **`generate_justification`** tool:
  - Input: `company_id` (required), `dimension` (required, enum of 7 dims)
  - Calls `cs4_client.generate_justification()`
  - Returns: `dimension`, `score`, `level`, `level_name`, `evidence_strength`, `rubric_criteria`, `supporting_evidence` (list of source/content/confidence), `gaps_identified`
- [x] **9.2.10** **`project_ebitda_impact`** tool:
  - Input: `company_id` (required), `entry_score` (0-100), `target_score` (0-100), `h_r_score` (0-100)
  - Calls `ebitda_calculator.project()`
  - Returns: `delta_air`, `scenarios` (conservative/base/optimistic), `risk_adjusted`, `requires_approval`
- [x] **9.2.11** **`run_gap_analysis`** tool:
  - Input: `company_id` (required), `target_org_air` (0-100)
  - Calls `cs3_client.get_assessment()` for current scores, then `gap_analyzer.analyze()`
  - Returns: JSON analysis result
- [x] **9.2.12** **`get_portfolio_summary`** tool:
  - Input: `fund_id` (required)
  - Calls `portfolio_data_service.get_portfolio_view()`
  - Computes `fund_air` as average Org-AI-R across portfolio
  - Returns: `fund_id`, `fund_air`, `company_count`, `companies` (list of ticker/org_air/sector)

#### `@mcp_server.call_tool()` Router:
- [x] **9.2.13** Routes tool calls by `name` to correct implementation
- [x] **9.2.14** Returns `List[TextContent]` with JSON-serialized results (`indent=2`)
- [x] **9.2.15** Error handling: catches exceptions, logs via structlog, returns `TextContent` error message
- [x] **9.2.16** Unknown tool returns `"Unknown tool: {name}"`

#### Entry Point:
- [x] **9.2.17** `async def main()` uses `stdio_server()` for MCP transport
- [x] **9.2.18** `if __name__ == "__main__"` runs `asyncio.run(main())`

---

### Task 9.3: MCP Resources & Prompts (8 pts) [MCP]

**File:** `mcp/server.py` (continued)

#### Resources — `@mcp_server.list_resources()`:
- [x] **9.3.1** Resource: `orgair://parameters/v2.0`
  - Name: "Org-AI-R Scoring Parameters v2.0"
  - Description: "Current scoring parameters: alpha, beta, gamma values"
- [x] **9.3.2** Resource: `orgair://sectors`
  - Name: "Sector Definitions"
  - Description: "Sector baselines and weights"

#### `@mcp_server.read_resource()`:
- [x] **9.3.3** `orgair://parameters/v2.0` returns JSON with: `version`, `alpha` (0.60), `beta` (0.12), `gamma_0` (0.0025), `gamma_1` (0.05), `gamma_2` (0.025), `gamma_3` (0.01)
- [x] **9.3.4** `orgair://sectors` returns JSON with sector baselines (e.g., technology: `{h_r_base: 85, weight_talent: 0.18}`, healthcare: `{h_r_base: 75, weight_governance: 0.18}`)
- [x] **9.3.5** Default return `"{}"` for unknown URIs

#### Prompts — `@mcp_server.list_prompts()`:
- [x] **9.3.6** Prompt: `due_diligence_assessment`
  - Description: "Complete due diligence assessment for a company"
  - Arguments: `[{name: "company_id", required: True}]`
- [x] **9.3.7** Prompt: `ic_meeting_prep`
  - Description: "Prepare Investment Committee meeting package"
  - Arguments: `[{name: "company_id", required: True}]`

#### `@mcp_server.get_prompt()`:
- [x] **9.3.8** `due_diligence_assessment` returns `PromptMessage` with role="user", content outlining:
  1. Calculate Org-AI-R score using `calculate_org_air_score`
  2. For dimensions below 60, use `generate_justification`
  3. Run `gap_analysis` with `target_org_air=75`
  4. Project EBITDA impact
- [x] **9.3.9** Returns empty list `[]` for unknown prompts

---

### Task 9.4: Assessment History Tracking (6 pts) [NEW]

**File:** `services/tracking/assessment_history.py`
**Existing:** `app/services/tracking/history_service.py`

#### Data Classes:
- [x] **9.4.1** `AssessmentSnapshot` dataclass:
  - `company_id: str`
  - `timestamp: datetime`
  - `org_air: Decimal`
  - `vr_score: Decimal`
  - `hr_score: Decimal`
  - `synergy_score: Decimal`
  - `dimension_scores: Dict[str, Decimal]`
  - `confidence_interval: tuple`
  - `evidence_count: int`
  - `assessor_id: str`
  - `assessment_type: str` ("screening", "limited", "full")
- [x] **9.4.2** `AssessmentTrend` dataclass:
  - `company_id: str`
  - `current_org_air: float`
  - `entry_org_air: float`
  - `delta_since_entry: float`
  - `delta_30d: Optional[float]`
  - `delta_90d: Optional[float]`
  - `trend_direction: str` ("improving", "stable", "declining")
  - `snapshot_count: int`

#### `AssessmentHistoryService`:
- [x] **9.4.3** `__init__(self, cs1_client, cs3_client)` — takes CS1 + CS3 clients, initializes `_cache: Dict[str, List[AssessmentSnapshot]]`
- [x] **9.4.4** `async record_assessment(company_id, assessor_id, assessment_type="full") -> AssessmentSnapshot`:
  - Calls `cs3.get_assessment(company_id)` for current scores
  - Creates `AssessmentSnapshot` with `timestamp=datetime.utcnow()`
  - Stores via `_store_snapshot()` (Snowflake INSERT)
  - Updates in-memory `_cache`
  - Logs with structlog
  - Returns snapshot
- [x] **9.4.5** `async _store_snapshot(snapshot)` — stores to Snowflake via CS1 (stub with `pass` for production INSERT)
- [x] **9.4.6** `async get_history(company_id, days=365) -> List[AssessmentSnapshot]`:
  - Checks `_cache` first with cutoff `datetime.utcnow() - timedelta(days=days)`
  - Falls back to Snowflake query (stub returns `[]`)
- [x] **9.4.7** `async calculate_trend(company_id) -> AssessmentTrend`:
  - Gets history (365 days); if no history, returns trend from current assessment only (direction="stable")
  - Sorts history by timestamp
  - Computes `current = history[-1].org_air`, `entry = history[0].org_air`
  - Computes `delta_30d` (first snapshot >= 30 days old), `delta_90d` (first >= 90 days old)
  - Direction: `delta > 5` → "improving", `delta < -5` → "declining", else "stable"
- [x] **9.4.8** Factory function: `create_history_service(cs1, cs3) -> AssessmentHistoryService`

---

### Task 9.5: Evidence Display Component (6 pts) [NEW]

**File:** `dashboard/components/evidence_display.py`
**Existing:** `streamlit/components/evidence_display.py`

#### Color Coding:
- [x] **9.5.1** `LEVEL_COLORS` dict: 1→"#ef4444" (red/Nascent), 2→"#f97316" (orange/Developing), 3→"#eab308" (yellow/Adequate), 4→"#22c55e" (green/Good), 5→"#14b8a6" (teal/Excellent)
- [x] **9.5.2** `LEVEL_NAMES` dict: 1→"Nascent", 2→"Developing", 3→"Adequate", 4→"Good", 5→"Excellent"

#### `render_evidence_card(justification: ScoreJustification)`:
- [x] **9.5.3** Renders single dimension's evidence card with:
  - Score badge with color coding (L1-L5) using styled HTML `<span>`
  - Dimension name as header (replacing underscores with spaces, title-cased)
  - Score value in bold
  - Evidence strength indicator (color-coded: strong=#22c55e, moderate=#eab308, weak=#ef4444)
  - Rubric criteria via `st.info()`
  - Supporting evidence (up to 5 items) via `st.expander()` with source_type, content[:50], confidence
  - Source URL links if available
  - Gaps identified section via `st.warning()`
  - Divider at end

#### `render_company_evidence_panel(company_id, justifications: Dict[str, ScoreJustification])`:
- [x] **9.5.4** Renders full evidence panel with:
  - Header: "Evidence Analysis: {company_id}"
  - Summary metrics row (4 columns): Total Evidence, Avg Level, Strong Evidence count, Dimensions count
  - Dimension tabs (one tab per dimension, title-cased)
  - Each tab renders `render_evidence_card()` for that dimension

#### `render_evidence_summary_table(justifications: Dict[str, ScoreJustification])`:
- [x] **9.5.5** Compact summary table using pandas DataFrame with columns:
  - Dimension, Score, Level (formatted as "L{level}"), Evidence (strength title-cased), Items count, Gaps count
- [x] **9.5.6** Color-coded Level column using `df.style.applymap(color_level, subset=["Level"])`
- [x] **9.5.7** Displayed with `st.dataframe(styled, use_container_width=True, hide_index=True)`

---

### Task 9.6: Portfolio Dashboard (10 pts) [REQUIRED]

**File:** `dashboard/app.py`
**Existing:** `streamlit/app.py`

#### v4 FIX Requirements:
- [x] **9.6.1** `import nest_asyncio` + `nest_asyncio.apply()` at top for Streamlit async compatibility
- [x] **9.6.2** Proper async pattern: `@st.cache_data(ttl=300)` wrapping `asyncio.new_event_loop()` → `run_until_complete()` → `loop.close()`

#### Page Config:
- [x] **9.6.3** `st.set_page_config(page_title="PE Org-AI-R Dashboard", page_icon="chart_with_upwards_trend", layout="wide")`

#### Sidebar:
- [x] **9.6.4** Title: "PE Org-AI-R"
- [x] **9.6.5** Fund ID text input (default: "growth_fund_v")

#### Data Loading:
- [x] **9.6.6** `load_portfolio(_fund_id)` calls `portfolio_data_service.get_portfolio_view(_fund_id)`
- [x] **9.6.7** Transforms result into pandas DataFrame with columns: `ticker`, `name`, `sector`, `org_air`, `vr_score`, `hr_score`, `delta`, `evidence_count`
- [x] **9.6.8** Error handling: `st.error()` + `st.info("Ensure CS1, CS2, CS3 services are running.")` + `st.stop()` on connection failure
- [x] **9.6.9** Sidebar success message: "Loaded {N} companies from CS1-CS4"

#### Main Content:
- [x] **9.6.10** Title: "Portfolio Overview"
- [x] **9.6.11** Metrics row (4 columns): Fund-AI-R (mean org_air), Companies count, Avg V^R (mean vr_score), Avg Delta (mean delta)
- [x] **9.6.12** V^R vs H^R Scatter plot using `plotly.express.scatter()`:
  - x="vr_score", y="hr_score", size="org_air", color="sector"
  - Horizontal dashed line at y=60 (H^R Threshold)
  - Vertical dashed line at x=60 (V^R Threshold)
  - Title: "Portfolio AI-Readiness Map (from CS3)"
- [x] **9.6.13** Company table with `background_gradient(subset=["org_air"], cmap="RdYlGn")`
- [x] **9.6.14** Evidence summary table integration via `render_evidence_summary_table`

#### Integration:
- [x] **9.6.15** Imports from: `portfolio_data_service`, `create_history_service`, `CS1Client`, `CS3Client`, `render_evidence_summary_table`
- [x] **9.6.16** ALL data comes from CS1-CS4 via `PortfolioDataService` — no hardcoded data

---

## Part II — Lab 10: LangGraph Agents + PE Workflows (50 pts)

### Task 10.1: LangGraph State Definitions (8 pts) [AGENT]

**File:** `agents/state.py`
**Existing:** `app/agents/state.py`

#### v4 FIX: Explicit datetime import
- [x] **10.1.1** `from datetime import datetime` at top of file

#### `AgentMessage(TypedDict)`:
- [x] **10.1.2** Fields:
  - `role: Literal["user", "assistant", "system", "tool"]`
  - `content: str`
  - `agent_name: Optional[str]`
  - `timestamp: datetime`

#### `DueDiligenceState(TypedDict)`:
- [x] **10.1.3** **Input fields:**
  - `company_id: str`
  - `assessment_type: Literal["screening", "limited", "full"]`
  - `requested_by: str`
- [x] **10.1.4** **Messages** (append-only via reducer):
  - `messages: Annotated[List[AgentMessage], operator.add]`
- [x] **10.1.5** **Agent output fields:**
  - `sec_analysis: Optional[Dict[str, Any]]`
  - `talent_analysis: Optional[Dict[str, Any]]`
  - `scoring_result: Optional[Dict[str, Any]]`
  - `evidence_justifications: Optional[Dict[str, Any]]`
  - `value_creation_plan: Optional[Dict[str, Any]]`
- [x] **10.1.6** **Workflow control fields:**
  - `next_agent: Optional[str]`
  - `requires_approval: bool`
  - `approval_reason: Optional[str]`
  - `approval_status: Optional[Literal["pending", "approved", "rejected"]]`
  - `approved_by: Optional[str]`
- [x] **10.1.7** **Metadata fields:**
  - `started_at: datetime`
  - `completed_at: Optional[datetime]`
  - `total_tokens: int`
  - `error: Optional[str]`

---

### Task 10.2: Specialist Agents (12 pts) [AGENT]

**File:** `agents/specialists.py`
**Existing:** `app/agents/specialists.py`

#### v4 FIX Requirements:
- [x] **10.2.1** `from datetime import datetime` (explicit import)
- [x] **10.2.2** Use HTTP client (`MCPToolCaller` with `httpx.AsyncClient`) for MCP tool calls, NOT a non-existent `MCPClient`
- [ ] **10.2.3** `MCPToolCaller` with base_url `http://localhost:3000`, timeout=30s *(uses localhost:8000 instead)*
- [x] **10.2.4** `call_tool(tool_name, arguments)` → POST to `{base_url}/tools/{tool_name}` with JSON args

#### LangChain @tool Functions (wrap MCP calls):
- [x] **10.2.5** `get_org_air_score(company_id)` → calls `calculate_org_air_score` MCP tool
- [x] **10.2.6** `get_evidence(company_id, dimension="all")` → calls `get_company_evidence` MCP tool
- [x] **10.2.7** `get_justification(company_id, dimension)` → calls `generate_justification` MCP tool
- [x] **10.2.8** `get_gap_analysis(company_id, target)` → calls `run_gap_analysis` MCP tool

#### `SECAnalysisAgent`:
- [ ] **10.2.9** LLM: `ChatOpenAI(model="gpt-4o", temperature=0.3)` *(uses ModelRouter instead of explicit ChatOpenAI)*
- [ ] **10.2.10** Tools: `[get_evidence]` *(no explicit tools list declared)*
- [x] **10.2.11** `async analyze(state: DueDiligenceState) -> Dict[str, Any]`:
  - Gets company_id from state
  - Calls `get_evidence.ainvoke({company_id, dimension: "all"})`
  - Returns `sec_analysis` dict with `company_id`, `findings`, `dimensions_covered` (data_infrastructure, ai_governance, technology_stack)
  - Returns message with agent_name="sec_analyst", timestamp=`datetime.utcnow()`

#### `ScoringAgent`:
- [ ] **10.2.12** LLM: `ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0.2)` *(uses ModelRouter instead of explicit ChatAnthropic)*
- [ ] **10.2.13** Tools: `[get_org_air_score, get_justification]` *(no explicit tools list declared)*
- [x] **10.2.14** `async calculate(state: DueDiligenceState) -> Dict[str, Any]`:
  - Calls `get_org_air_score.ainvoke({company_id})`
  - Parses JSON result for `org_air` score
  - **HITL check:** `requires_approval = org_air > 85 or org_air < 40`
  - Sets `approval_reason` if outside normal range [40, 85]
  - Returns `scoring_result`, `requires_approval`, `approval_reason`, `approval_status`
  - Message: agent_name="scorer"

#### `EvidenceAgent`:
- [ ] **10.2.15** LLM: `ChatOpenAI(model="gpt-4o", temperature=0.3)` *(uses ModelRouter instead of explicit ChatOpenAI)*
- [ ] **10.2.16** Tools: `[get_justification]` *(no explicit tools list declared)*
- [x] **10.2.17** `async justify(state: DueDiligenceState) -> Dict[str, Any]`:
  - Iterates dimensions: `["data_infrastructure", "talent", "use_case_portfolio"]`
  - For each, calls `get_justification.ainvoke({company_id, dimension})`
  - Returns `evidence_justifications` dict with all justifications
  - Message: agent_name="evidence_agent"

#### `ValueCreationAgent`:
- [ ] **10.2.18** LLM: `ChatOpenAI(model="gpt-4o", temperature=0.3)` *(uses ModelRouter instead of explicit ChatOpenAI)*
- [ ] **10.2.19** Tools: `[get_gap_analysis]` *(no explicit tools list declared)*
- [x] **10.2.20** `async plan(state: DueDiligenceState) -> Dict[str, Any]`:
  - Calls `get_gap_analysis.ainvoke({company_id, target: 80.0})`
  - **HITL check:** `projected_impact > 5.0` or `state.get("requires_approval", False)`
  - Returns `value_creation_plan`, `requires_approval`, `approval_reason`
  - Message: agent_name="value_creator"

#### Module-Level Instantiation:
- [ ] **10.2.21** `sec_agent = SECAnalysisAgent()` *(instantiated in supervisor.py, not specialists.py)*
- [ ] **10.2.22** `scoring_agent = ScoringAgent()` *(instantiated in supervisor.py, not specialists.py)*
- [ ] **10.2.23** `evidence_agent = EvidenceAgent()` *(instantiated in supervisor.py, not specialists.py)*
- [ ] **10.2.24** `value_agent = ValueCreationAgent()` *(instantiated in supervisor.py, not specialists.py)*

---

### Task 10.3: Supervisor with HITL (10 pts) [AGENT]

**File:** `agents/supervisor.py`
**Existing:** `app/agents/supervisor.py`

#### HITL Approval Gates (must trigger for):
- [x] **10.3.1** Scores outside normal range (below 40 or above 85)
- [x] **10.3.2** EBITDA projections exceeding 5%
- [x] **10.3.3** Any assessment flagged for review

#### `supervisor_node(state: DueDiligenceState) -> Dict[str, Any]`:
- [x] **10.3.4** If `requires_approval` AND `approval_status == "pending"` → route to `"hitl_approval"`
- [x] **10.3.5** Sequential routing logic:
  1. If no `sec_analysis` → `"sec_analyst"`
  2. Elif no `scoring_result` → `"scorer"`
  3. Elif no `evidence_justifications` → `"evidence_agent"`
  4. Elif no `value_creation_plan` AND `assessment_type != "screening"` → `"value_creator"`
  5. Else → `"complete"`
- [x] **10.3.6** Returns `{"next_agent": <agent_name>}`

#### Node Functions:
- [x] **10.3.7** `sec_analyst_node(state)` → calls `sec_agent.analyze(state)`
- [x] **10.3.8** `scorer_node(state)` → calls `scoring_agent.calculate(state)`
- [x] **10.3.9** `evidence_node(state)` → calls `evidence_agent.justify(state)`
- [x] **10.3.10** `value_creator_node(state)` → calls `value_agent.plan(state)`

#### `hitl_approval_node(state: DueDiligenceState) -> Dict[str, Any]`:
- [x] **10.3.11** Logs warning with `company_id` and `approval_reason`
- [x] **10.3.12** For exercise: auto-approves with `approval_status="approved"`, `approved_by="exercise_auto_approve"`
- [x] **10.3.13** Returns message with role="system", agent_name="hitl"

#### `complete_node(state)`:
- [x] **10.3.14** Sets `completed_at: datetime.utcnow()`
- [x] **10.3.15** Returns completion message

#### `create_due_diligence_graph()`:
- [x] **10.3.16** Creates `StateGraph(DueDiligenceState)`
- [x] **10.3.17** Adds all 7 nodes: `supervisor`, `sec_analyst`, `scorer`, `evidence_agent`, `value_creator`, `hitl_approval`, `complete`
- [x] **10.3.18** Conditional edges from `supervisor` using `lambda s: s["next_agent"]` with mapping dict
- [x] **10.3.19** Edges: each specialist agent → `supervisor` (back to supervisor after completion)
- [x] **10.3.20** Edge: `hitl_approval` → `supervisor`
- [x] **10.3.21** Edge: `complete` → `END`
- [x] **10.3.22** Entry point: `"supervisor"`
- [x] **10.3.23** Compiled with `checkpointer=MemorySaver()`
- [x] **10.3.24** Module-level: `dd_graph = create_due_diligence_graph()`

---

### Task 10.4: Agentic Due Diligence Workflow (10 pts) [AGENT]

**File:** `exercises/agentic_due_diligence.py`
**Existing:** `exercises/agentic_due_diligence.py`

- [x] **10.4.1** `async run_due_diligence(company_id, assessment_type="full") -> DueDiligenceState`:
  - Creates `initial_state: DueDiligenceState` with all fields initialized:
    - `company_id`, `assessment_type`, `requested_by="analyst"`
    - `messages=[]`, all analysis fields=`None`
    - `requires_approval=False`, approval fields=`None`
    - `started_at=datetime.utcnow()`, `completed_at=None`, `total_tokens=0`, `error=None`
  - Config with `thread_id: f"dd-{company_id}-{datetime.now().isoformat()}"`
  - Calls `await dd_graph.ainvoke(initial_state, config)`
- [x] **10.4.2** `async def main()`:
  - Prints banner ("PE Org-AI-R: Agentic Due Diligence")
  - Runs `run_due_diligence("NVDA", "full")`
  - Prints results: Org-AI-R score, HITL required, approval status
  - Prints confirmation: "All data came from CS1-CS4 via MCP tools."
- [x] **10.4.3** Entry point: `if __name__ == "__main__": asyncio.run(main())`

---

### Task 10.5: Fund-AI-R Calculator (5 pts) [NEW]

**File:** `services/analytics/fund_air.py`
**Existing:** `app/services/analytics/fund_air.py`

#### Sector Benchmarks:
- [x] **10.5.1** `SECTOR_BENCHMARKS` dict with quartile thresholds (q1-q4) for:
  - technology: {q1: 75, q2: 65, q3: 55, q4: 45}
  - healthcare: {q1: 70, q2: 58, q3: 48, q4: 38}
  - financial_services: {q1: 72, q2: 60, q3: 50, q4: 40}
  - manufacturing: {q1: 68, q2: 55, q3: 45, q4: 35}
  - retail: {q1: 65, q2: 52, q3: 42, q4: 32}
  - energy: {q1: 60, q2: 48, q3: 38, q4: 28}

#### `FundMetrics` dataclass:
- [x] **10.5.2** Fields: `fund_id`, `fund_air` (float, EV-weighted), `company_count`, `quartile_distribution: Dict[int, int]`, `sector_hhi` (float, Herfindahl-Hirschman Index), `avg_delta_since_entry`, `total_ev_mm`, `ai_leaders_count` (score >= 70), `ai_laggards_count` (score < 50)

#### `FundAIRCalculator`:
- [x] **10.5.3** `calculate_fund_metrics(fund_id, companies: List[PortfolioCompanyView], enterprise_values: Dict[str, float]) -> FundMetrics`:
  - Raises `ValueError` for empty portfolio
  - EV-weighted Org-AI-R: `sum(ev * org_air) / sum(ev)` (default EV=100.0)
  - Quartile distribution using `_get_quartile()` per company
  - Sector HHI: `sum((ev_sector / total_ev) ** 2)`
  - Avg delta since entry
  - Total EV in millions
  - AI leaders (org_air >= 70) and laggards (org_air < 50)
- [x] **10.5.4** `_get_quartile(score, sector) -> int`:
  - Uses `SECTOR_BENCHMARKS` (defaults to technology)
  - Q1 if score >= q1, Q2 if >= q2, Q3 if >= q3, else Q4
- [x] **10.5.5** Module-level: `fund_air_calculator = FundAIRCalculator()`

---

### Task 10.6: Prometheus Metrics (5 pts) [NEW]

**File:** `services/observability/metrics.py`
**Existing:** `app/services/observability/metrics.py`

#### MCP Server Metrics:
- [x] **10.6.1** `MCP_TOOL_CALLS` Counter: name="mcp_tool_calls_total", labels=["tool_name", "status"]
- [x] **10.6.2** `MCP_TOOL_DURATION` Histogram: name="mcp_tool_duration_seconds", labels=["tool_name"], buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]

#### LangGraph Agent Metrics:
- [x] **10.6.3** `AGENT_INVOCATIONS` Counter: name="agent_invocations_total", labels=["agent_name", "status"]
- [x] **10.6.4** `AGENT_DURATION` Histogram: name="agent_duration_seconds", labels=["agent_name"], buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0]

#### HITL Metrics:
- [x] **10.6.5** `HITL_APPROVALS` Counter: name="hitl_approvals_total", labels=["reason", "decision"]

#### CS1-CS4 Integration Metrics:
- [x] **10.6.6** `CS_CLIENT_CALLS` Counter: name="cs_client_calls_total", labels=["service", "endpoint", "status"]

#### Decorators:
- [x] **10.6.7** `track_mcp_tool(tool_name)` — decorator that:
  - Times execution with `time.perf_counter()`
  - Increments `MCP_TOOL_CALLS` (success/error)
  - Observes `MCP_TOOL_DURATION`
- [x] **10.6.8** `track_agent(agent_name)` — decorator that:
  - Times execution
  - Increments `AGENT_INVOCATIONS` (success/error)
  - Observes `AGENT_DURATION`
- [x] **10.6.9** `track_cs_client(service, endpoint)` — decorator that:
  - Times execution
  - Increments `CS_CLIENT_CALLS` (success/error)

---

## CS Client Interfaces (Prerequisites)

### CS1 Client (`services/cs1_client.py`)
**Existing:** `app/services/integration/cs1_client.py`

- [x] **P.1** `Sector` enum: TECHNOLOGY, HEALTHCARE, FINANCIAL_SERVICES, MANUFACTURING, RETAIL, ENERGY
- [x] **P.2** `Company` dataclass: `company_id`, `ticker`, `name`, `sector: Sector`, `employee_count: int`, `revenue_mm: float`, `portfolio_entry_date: Optional[str]`
- [x] **P.3** `CS1Client.__init__(base_url="http://localhost:8000")`
- [x] **P.4** `async get_company(company_id) -> Company` — calls `GET /v1/companies/{company_id}`
- [ ] **P.5** `async get_portfolio_companies(fund_id) -> List[Company]` — calls `GET /v1/portfolios/{fund_id}/companies` *(method not implemented)*

### CS3 Client (`services/cs3_client.py`)
**Existing:** `app/services/integration/cs3_client.py`

- [x] **P.6** `Dimension` enum: DATA_INFRASTRUCTURE, AI_GOVERNANCE, TECHNOLOGY_STACK, TALENT, LEADERSHIP, USE_CASE_PORTFOLIO, CULTURE
- [x] **P.7** `DimensionScore` dataclass: `dimension: Dimension`, `score: float`, `level: int`, `evidence_count: int`
- [x] **P.8** `CompanyAssessment` dataclass: `company_id`, `org_air_score: float`, `vr_score: float`, `hr_score: float`, `synergy_score: float`, `dimension_scores: Dict[Dimension, DimensionScore]`, `confidence_interval: Tuple[float, float]`, `evidence_count: int`
- [x] **P.9** `CS3Client.__init__(base_url)`
- [x] **P.10** `async get_assessment(company_id) -> CompanyAssessment` — calls `GET /v2/assessments/{company_id}`

---

## Value Creation Services (Used by MCP Tools)

### EBITDA Calculator (`services/value_creation/ebitda.py`)
**Existing:** `app/services/value_creation/ebitda.py`

- [x] **V.1** `ebitda_calculator.project(company_id, entry_score, exit_score, h_r_score)` — returns projection with `delta_air`, `scenarios` (conservative/base/optimistic), `risk_adjusted`, `requires_approval`

### Gap Analyzer (`services/value_creation/gap_analysis.py`)
**Existing:** `app/services/value_creation/gap_analysis.py`

- [x] **V.2** `gap_analyzer.analyze(company_id, current_scores, target_org_air)` — returns analysis with gap-by-dimension, priority ranking, initiatives, investment estimate

---

## Testing

**File:** `tests/test_mcp_integration.py`
**Existing:** `app/mcp/test_mcp.py`, `test_mcp_tools.py`

- [x] **T.1** `test_calculate_org_air_calls_cs3()`:
  - Patches `cs3_client.get_assessment` with `AsyncMock`
  - Calls `call_tool("calculate_org_air_score", {"company_id": "NVDA"})`
  - Asserts `mock.assert_called_once_with("NVDA")` — verifies CS3 was called
- [x] **T.2** `test_no_hardcoded_data()`:
  - Patches `cs3_client.get_assessment` with `side_effect=ConnectionError("CS3 not running")`
  - Asserts `pytest.raises(Exception)` when calling tool — proves tools don't return hardcoded data
- [x] **T.3** Run: `pytest tests/ -v --tb=short`

---

## Bonus Extensions (+20 pts)

- [x] **B.1** Mem0 Semantic Memory (+5 pts)
- [x] **B.2** Investment Tracker with ROI (+5 pts)
- [x] **B.3** IC Memo Generator (Word doc) (+5 pts)
- [ ] **B.4** LP Letter Generator (+5 pts) *(not implemented)*

---

## Submission Structure

```
cs5_agentic_portfolio/
  |-- src/
      |-- services/
          integration/portfolio_data_service.py   # CS1-CS4 integration
          tracking/assessment_history.py           # NEW: History
          analytics/fund_air.py                    # NEW: Fund-AI-R
          observability/metrics.py                 # NEW: Prometheus
      |-- mcp/server.py                            # MCP Server
      |-- agents/
          state.py, specialists.py, supervisor.py  # LangGraph
      |-- dashboard/
          app.py                                   # Streamlit
          components/evidence_display.py           # NEW: Evidence UI
  |-- exercises/agentic_due_diligence.py
  |-- tests/test_mcp_integration.py
  +-- README.md
```

---

## Critical Reminders

1. **NO MOCK DATA** — All MCP tools must call actual CS1-CS4 clients. Submissions with hardcoded data receive max 50% credit.
2. **Verification method:** Graders will stop CS3 service and run pytest — tools should ERROR, not return data.
3. **v4 FIX:** All clients initialized at module level (not inside functions).
4. **v4 FIX:** `from datetime import datetime` — explicit import, not just `import datetime`.
5. **v4 FIX:** Use `httpx.AsyncClient` for MCP tool calls in specialists (not non-existent `MCPClient`).
6. **v4 FIX:** `nest_asyncio.apply()` required for Streamlit async compatibility.
7. **Async/await:** All MCP tool calls must use `await`. Forgetting causes `RuntimeWarning: coroutine was never awaited`.
8. **HITL gates:** Check BOTH bounds — `org_air > 85 OR org_air < 40`.
