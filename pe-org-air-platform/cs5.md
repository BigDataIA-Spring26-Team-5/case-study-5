Plan to implement                                                                                                                  │
│                                                                                                                                    │
│ CS5: Agentic Portfolio Intelligence — Implementation Plan                                                                          │
│                                                                                                                                    │
│ Context                                                                                                                            │
│                                                                                                                                    │
│ CS5 is the capstone case study (due March 27, 2026). It transforms the existing CS1-CS4 PE Org-AI-R Platform into an intelligent   │
│ agent system by:                                                                                                                   │
│ - Lab 9 (50 pts): Building an MCP Server that wraps CS1-CS4 APIs as tools, plus integration services and a Streamlit dashboard     │
│ - Lab 10 (50 pts): Building LangGraph agents (supervisor + 4 specialists) with HITL approval gates                                 │
│                                                                                                                                    │
│ Key constraint: All agent tools must call real CS1-CS4 services — no mock data.                                                    │
│                                                                                                                                    │
│ Architecture decision: Our platform is a single FastAPI monolith (not microservices). The PortfolioDataService will call existing  │
│ services in-process rather than over HTTP where possible, adapting the PDF's multi-service import paths to our actual codebase.    │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 0: Dependencies (prerequisite)                                                                                               │
│                                                                                                                                    │
│ Modify: requirements.txt — add:                                                                                                    │
│ mcp>=1.0.0                                                                                                                         │
│ langgraph>=0.2.0                                                                                                                   │
│ langchain-core>=0.3.0                                                                                                              │
│ langchain-openai>=0.2.0                                                                                                            │
│ langchain-anthropic>=0.2.0                                                                                                         │
│ prometheus-client>=0.20.0                                                                                                          │
│ nest_asyncio>=1.6.0                                                                                                                │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 1: CS4 Client + Portfolio Data Service + Value Creation — Task 9.1 (8 pts)                                                   │
│                                                                                                                                    │
│ We already have all three CS clients:                                                                                              │
│ - app/services/integration/cs1_client.py — CS1Client, Company, Sector, Portfolio                                                   │
│ - app/services/integration/cs2_client.py — CS2Client (evidence)                                                                    │
│ - app/services/integration/cs3_client.py — CS3Client, CompanyAssessment, DimensionScore, ScoreLevel, rubric data, Groq helpers     │
│                                                                                                                                    │
│ We need to create a CS4 client for justification/RAG, then build the unified PortfolioDataService and value-creation modules.      │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌─────────────────────────────────────────────┬──────────────────────────────────────────────────────────────┐                     │
│ │                    File                     │                           Purpose                            │                     │
│ ├─────────────────────────────────────────────┼──────────────────────────────────────────────────────────────┤                     │
│ │ app/services/integration/cs4_client.py      │ CS4 client wrapping JustificationGenerator + HybridRetriever │                     │
│ ├─────────────────────────────────────────────┼──────────────────────────────────────────────────────────────┤                     │
│ │ app/services/portfolio_data_service.py      │ Unified facade — the ONLY data source for MCP tools + agents │                     │
│ ├─────────────────────────────────────────────┼──────────────────────────────────────────────────────────────┤                     │
│ │ app/services/value_creation/__init__.py     │ Package init                                                 │                     │
│ ├─────────────────────────────────────────────┼──────────────────────────────────────────────────────────────┤                     │
│ │ app/services/value_creation/ebitda.py       │ EBITDA impact projector (new logic from PDF)                 │                     │
│ ├─────────────────────────────────────────────┼──────────────────────────────────────────────────────────────┤                     │
│ │ app/services/value_creation/gap_analysis.py │ Gap analyzer (uses CS3 scores + CS4 justifications)          │                     │
│ └─────────────────────────────────────────────┴──────────────────────────────────────────────────────────────┘                     │
│                                                                                                                                    │
│ Key reuse                                                                                                                          │
│                                                                                                                                    │
│ - app/services/integration/cs1_client.py — existing CS1Client, Company, Sector                                                     │
│ - app/services/integration/cs2_client.py — existing CS2Client                                                                      │
│ - app/services/integration/cs3_client.py — existing CS3Client, CompanyAssessment, DimensionScore, score_to_level(), SCORE_LEVELS,  │
│ _RUBRIC_TEXT                                                                                                                       │
│ - app/services/composite_scoring_service.py — compute_orgair() returns full score breakdown                                        │
│ - app/services/justification/generator.py — JustificationGenerator.generate()                                                      │
│ - app/repositories/company_repository.py — CompanyRepository.get_by_ticker()                                                       │
│                                                                                                                                    │
│ Modifications                                                                                                                      │
│                                                                                                                                    │
│ - app/core/lifespan.py — instantiate PortfolioDataService with existing singletons in _create_singletons()                         │
│ - app/core/dependencies.py — add get_portfolio_data_service() provider                                                             │
│                                                                                                                                    │
│ Adaptation from PDF                                                                                                                │
│                                                                                                                                    │
│ The PDF's PortfolioDataService.__init__ takes separate URLs for CS1/CS2/CS3. Ours will take the service objects directly           │
│ (in-process DI):                                                                                                                   │
│ class PortfolioDataService:                                                                                                        │
│     def __init__(self, cs1_client, cs2_client, cs3_client, cs4_client):                                                            │
│         self.cs1 = cs1_client  # existing CS1Client                                                                                │
│         self.cs2 = cs2_client  # existing CS2Client                                                                                │
│         self.cs3 = cs3_client  # existing CS3Client (has CompanyAssessment, DimensionScore)                                        │
│         self.cs4 = cs4_client  # new CS4Client (wraps justification + RAG)                                                         │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 2: MCP Server Core — Task 9.2 (12 pts)                                                                                       │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌─────────────────────┬───────────────────────────────────────────────────────────────┐                                            │
│ │        File         │                            Purpose                            │                                            │
│ ├─────────────────────┼───────────────────────────────────────────────────────────────┤                                            │
│ │ app/mcp/__init__.py │ Package init                                                  │                                            │
│ ├─────────────────────┼───────────────────────────────────────────────────────────────┤                                            │
│ │ app/mcp/server.py   │ MCP Server with 6 tools, list_tools(), call_tool() dispatcher │                                            │
│ └─────────────────────┴───────────────────────────────────────────────────────────────┘                                            │
│                                                                                                                                    │
│ 6 MCP Tools                                                                                                                        │
│                                                                                                                                    │
│ 1. calculate_org_air_score(company_id) → cs3_client.get_assessment()                                                               │
│ 2. get_company_evidence(company_id, dimension?, limit?) → cs2_client.get_evidence()                                                │
│ 3. generate_justification(company_id, dimension) → cs4_client.generate_justification()                                             │
│ 4. project_ebitda_impact(company_id, entry_score, target_score, h_r_score) → ebitda_calculator.project()                           │
│ 5. run_gap_analysis(company_id, target_org_air) → gap_analyzer.analyze()                                                           │
│ 6. get_portfolio_summary(fund_id) → portfolio_data_service.get_portfolio_view()                                                    │
│                                                                                                                                    │
│ Entry point                                                                                                                        │
│                                                                                                                                    │
│ python -m app.mcp.server — stdio transport for Claude Desktop integration                                                          │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 3: MCP Resources & Prompts — Task 9.3 (8 pts)                                                                                │
│                                                                                                                                    │
│ Added to app/mcp/server.py                                                                                                         │
│                                                                                                                                    │
│ Resources:                                                                                                                         │
│ - orgair://parameters/v2.0 — returns alpha, beta, gamma values from app/core/settings.py                                           │
│ - orgair://sectors — returns sector baselines + dimension weight overrides                                                         │
│                                                                                                                                    │
│ Prompts:                                                                                                                           │
│ - due_diligence_assessment(company_id) — instructs agent to run full DD workflow                                                   │
│ - ic_meeting_prep(company_id) — instructs agent to prepare IC meeting package                                                      │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 4: Assessment History Tracking — Task 9.4 (6 pts)                                                                            │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌──────────────────────────────────────────┬─────────────────────────────────────────────────────┐                                 │
│ │                   File                   │                       Purpose                       │                                 │
│ ├──────────────────────────────────────────┼─────────────────────────────────────────────────────┤                                 │
│ │ app/services/tracking/__init__.py        │ Package init                                        │                                 │
│ ├──────────────────────────────────────────┼─────────────────────────────────────────────────────┤                                 │
│ │ app/services/tracking/history_service.py │ AssessmentHistoryService with record/retrieve/trend │                                 │
│ └──────────────────────────────────────────┴─────────────────────────────────────────────────────┘                                 │
│                                                                                                                                    │
│ Dataclasses                                                                                                                        │
│                                                                                                                                    │
│ - AssessmentSnapshot — point-in-time score capture (org_air, vr, hr, synergy, dimensions, confidence, timestamp, assessor, type)   │
│ - AssessmentTrend — computed trend (current, entry, delta_30d, delta_90d, direction)                                               │
│                                                                                                                                    │
│ Methods                                                                                                                            │
│                                                                                                                                    │
│ - record_assessment(company_id, assessor_id, type) → calls CS3 for current scores, stores snapshot                                 │
│ - get_history(company_id, days) → retrieves from in-memory cache (+ Snowflake in production)                                       │
│ - calculate_trend(company_id) → computes deltas and direction from history                                                         │
│                                                                                                                                    │
│ Modifications                                                                                                                      │
│                                                                                                                                    │
│ - app/core/lifespan.py — instantiate with CS1/CS3 clients                                                                          │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 5: Evidence Display Component — Task 9.5 (6 pts)                                                                             │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌──────────────────────────────────────────┬──────────────────────────────────┐                                                    │
│ │                   File                   │             Purpose              │                                                    │
│ ├──────────────────────────────────────────┼──────────────────────────────────┤                                                    │
│ │ streamlit/components/__init__.py         │ Package init (if missing)        │                                                    │
│ ├──────────────────────────────────────────┼──────────────────────────────────┤                                                    │
│ │ streamlit/components/evidence_display.py │ Reusable evidence card component │                                                    │
│ └──────────────────────────────────────────┴──────────────────────────────────┘                                                    │
│                                                                                                                                    │
│ Functions                                                                                                                          │
│                                                                                                                                    │
│ - render_evidence_card(justification) — single dimension card with score badge (L1-L5 color), evidence list, gaps                  │
│ - render_company_evidence_panel(company_id, justifications) — full 7-dimension panel with tabs                                     │
│ - render_evidence_summary_table(justifications) — compact table with pandas styling                                                │
│                                                                                                                                    │
│ Reuse                                                                                                                              │
│                                                                                                                                    │
│ - Level colors: L1=red, L2=orange, L3=yellow, L4=green, L5=teal                                                                    │
│ - Evidence strength indicators: strong/moderate/weak                                                                               │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 6: Portfolio Dashboard — Task 9.6 (10 pts)                                                                                   │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌──────────────────────┬────────────────────────────────┐                                                                          │
│ │         File         │            Purpose             │                                                                          │
│ ├──────────────────────┼────────────────────────────────┤                                                                          │
│ │ streamlit/cs5_app.py │ Main CS5 dashboard entry point │                                                                          │
│ └──────────────────────┴────────────────────────────────┘                                                                          │
│                                                                                                                                    │
│ Features                                                                                                                           │
│                                                                                                                                    │
│ - Fund-AI-R metric — average Org-AI-R across portfolio                                                                             │
│ - VR vs HR scatter — Plotly scatter (x=vr, y=hr, size=org_air, color=sector) with threshold lines at 60                            │
│ - Company table — sorted dataframe with conditional gradient on org_air                                                            │
│ - Sidebar — fund_id input, connection status                                                                                       │
│                                                                                                                                    │
│ Integration                                                                                                                        │
│                                                                                                                                    │
│ - Uses nest_asyncio for Streamlit async compatibility                                                                              │
│ - Loads data via portfolio_data_service.get_portfolio_view(fund_id)                                                                │
│ - @st.cache_data(ttl=300) for caching                                                                                              │
│ - Imports evidence_display component from Phase 5                                                                                  │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 7: LangGraph State — Task 10.1 (8 pts)                                                                                       │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌────────────────────────┬──────────────────────────────────────────────────────┐                                                  │
│ │          File          │                       Purpose                        │                                                  │
│ ├────────────────────────┼──────────────────────────────────────────────────────┤                                                  │
│ │ app/agents/__init__.py │ Package init                                         │                                                  │
│ ├────────────────────────┼──────────────────────────────────────────────────────┤                                                  │
│ │ app/agents/state.py    │ AgentMessage TypedDict + DueDiligenceState TypedDict │                                                  │
│ └────────────────────────┴──────────────────────────────────────────────────────┘                                                  │
│                                                                                                                                    │
│ State fields                                                                                                                       │
│                                                                                                                                    │
│ - Input: company_id, assessment_type (screening/limited/full), requested_by                                                        │
│ - Messages: Annotated[List[AgentMessage], operator.add] (append-only reducer)                                                      │
│ - Agent outputs: sec_analysis, talent_analysis, scoring_result, evidence_justifications, value_creation_plan (all Optional[Dict])  │
│ - Control: next_agent, requires_approval, approval_reason, approval_status, approved_by                                            │
│ - Metadata: started_at, completed_at, total_tokens, error                                                                          │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 8: Specialist Agents — Task 10.2 (12 pts)                                                                                    │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌───────────────────────────┬───────────────────────────────────────────────────┐                                                  │
│ │           File            │                      Purpose                      │                                                  │
│ ├───────────────────────────┼───────────────────────────────────────────────────┤                                                  │
│ │ app/agents/specialists.py │ MCPToolCaller + 4 tool wrappers + 4 agent classes │                                                  │
│ └───────────────────────────┴───────────────────────────────────────────────────┘                                                  │
│                                                                                                                                    │
│ Components                                                                                                                         │
│                                                                                                                                    │
│ - MCPToolCaller — httpx async client wrapping MCP server calls                                                                     │
│ - Tool wrappers: get_org_air_score, get_evidence, get_justification, get_gap_analysis (decorated with @tool)                       │
│ - SECAnalysisAgent.analyze(state) — uses get_evidence for SEC data                                                                 │
│ - ScoringAgent.calculate(state) — uses get_org_air_score, checks HITL thresholds [40,85]                                           │
│ - EvidenceAgent.justify(state) — generates justifications for key dimensions                                                       │
│ - ValueCreationAgent.plan(state) — runs gap analysis, checks EBITDA >5% for HITL                                                   │
│                                                                                                                                    │
│ LLM providers                                                                                                                      │
│                                                                                                                                    │
│ - SECAnalysisAgent: ChatOpenAI(gpt-4o)                                                                                             │
│ - ScoringAgent: ChatAnthropic(claude-sonnet)                                                                                       │
│ - EvidenceAgent: ChatOpenAI(gpt-4o)                                                                                                │
│ - ValueCreationAgent: ChatOpenAI(gpt-4o)                                                                                           │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 9: Supervisor with HITL — Task 10.3 (10 pts)                                                                                 │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌──────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────┐                 │
│ │           File           │                                       Purpose                                       │                 │
│ ├──────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤                 │
│ │ app/agents/supervisor.py │ StateGraph definition + routing + hitl_approval_node + create_due_diligence_graph() │                 │
│ └──────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────┘                 │
│                                                                                                                                    │
│ Graph structure                                                                                                                    │
│                                                                                                                                    │
│ supervisor → sec_analyst → supervisor → scorer → supervisor → evidence_agent → supervisor → value_creator → supervisor →           │
│ [hitl_approval?] → complete → END                                                                                                  │
│                                                                                                                                    │
│ Routing logic (supervisor_node)                                                                                                    │
│                                                                                                                                    │
│ 1. If requires_approval and approval_status == "pending" → hitl_approval                                                           │
│ 2. If sec_analysis missing → sec_analyst                                                                                           │
│ 3. If scoring_result missing → scorer                                                                                              │
│ 4. If evidence_justifications missing → evidence_agent                                                                             │
│ 5. If value_creation_plan missing and type != "screening" → value_creator                                                          │
│ 6. Else → complete                                                                                                                 │
│                                                                                                                                    │
│ HITL triggers                                                                                                                      │
│                                                                                                                                    │
│ - Org-AI-R score outside [40, 85]                                                                                                  │
│ - EBITDA projection > 5%                                                                                                           │
│                                                                                                                                    │
│ Compile                                                                                                                            │
│                                                                                                                                    │
│ workflow.compile(checkpointer=MemorySaver())                                                                                       │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 10: Due Diligence Workflow — Task 10.4 (10 pts)                                                                              │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌────────────────────────────────────┬────────────────────────────┐                                                                │
│ │                File                │          Purpose           │                                                                │
│ ├────────────────────────────────────┼────────────────────────────┤                                                                │
│ │ exercises/agentic_due_diligence.py │ End-to-end exercise script │                                                                │
│ └────────────────────────────────────┴────────────────────────────┘                                                                │
│                                                                                                                                    │
│ Flow                                                                                                                               │
│                                                                                                                                    │
│ 1. Initialize DueDiligenceState for a ticker (e.g., "NVDA")                                                                        │
│ 2. dd_graph.ainvoke(initial_state, config) with unique thread_id                                                                   │
│ 3. Print Org-AI-R score, HITL status, approval status                                                                              │
│ 4. Confirm all data came from CS1-CS4 via MCP tools                                                                                │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 11: Fund-AI-R Calculator — Task 10.5 (5 pts)                                                                                 │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌────────────────────────────────────┬───────────────────────────────────────────┐                                                 │
│ │                File                │                  Purpose                  │                                                 │
│ ├────────────────────────────────────┼───────────────────────────────────────────┤                                                 │
│ │ app/services/analytics/__init__.py │ Package init                              │                                                 │
│ ├────────────────────────────────────┼───────────────────────────────────────────┤                                                 │
│ │ app/services/analytics/fund_air.py │ FundAIRCalculator + FundMetrics dataclass │                                                 │
│ └────────────────────────────────────┴───────────────────────────────────────────┘                                                 │
│                                                                                                                                    │
│ Logic                                                                                                                              │
│                                                                                                                                    │
│ - EV-weighted Fund-AI-R = Σ(ev_weight × org_air) across portfolio                                                                  │
│ - Sector quartile distribution using SECTOR_BENCHMARKS                                                                             │
│ - Sector HHI concentration index                                                                                                   │
│ - AI leaders (>=70) and laggards (<50) counts                                                                                      │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Phase 12: Prometheus Metrics — Task 10.6 (5 pts)                                                                                   │
│                                                                                                                                    │
│ New files                                                                                                                          │
│                                                                                                                                    │
│ ┌────────────────────────────────────────┬──────────────────────────────────┐                                                      │
│ │                  File                  │             Purpose              │                                                      │
│ ├────────────────────────────────────────┼──────────────────────────────────┤                                                      │
│ │ app/services/observability/__init__.py │ Package init                     │                                                      │
│ ├────────────────────────────────────────┼──────────────────────────────────┤                                                      │
│ │ app/services/observability/metrics.py  │ Counters, Histograms, decorators │                                                      │
│ └────────────────────────────────────────┴──────────────────────────────────┘                                                      │
│                                                                                                                                    │
│ Metrics                                                                                                                            │
│                                                                                                                                    │
│ - mcp_tool_calls_total (Counter: tool_name, status)                                                                                │
│ - mcp_tool_duration_seconds (Histogram: tool_name)                                                                                 │
│ - agent_invocations_total (Counter: agent_name, status)                                                                            │
│ - agent_duration_seconds (Histogram: agent_name)                                                                                   │
│ - hitl_approvals_total (Counter: reason, decision)                                                                                 │
│ - cs_client_calls_total (Counter: service, endpoint, status)                                                                       │
│                                                                                                                                    │
│ Decorators                                                                                                                         │
│                                                                                                                                    │
│ - @track_mcp_tool(tool_name) — wraps async MCP tool functions                                                                      │
│ - @track_agent(agent_name) — wraps agent node functions                                                                            │
│ - @track_cs_client(service, endpoint) — wraps CS client calls                                                                      │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Execution Order                                                                                                                    │
│                                                                                                                                    │
│ ┌──────┬───────┬─────────────────────────┬────────┬────────────────────────────┐                                                   │
│ │ Step │ Phase │          Task           │ Points │        Dependencies        │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 1    │ 0     │ Install dependencies    │ —      │ None                       │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 2    │ 1     │ Portfolio Data Service  │ 8      │ Phase 0                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 3    │ 4     │ History Tracking        │ 6      │ Phase 1 (CS3 client)       │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 4    │ 11    │ Fund-AI-R Calculator    │ 5      │ Phase 1                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 5    │ 12    │ Prometheus Metrics      │ 5      │ None                       │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 6    │ 2     │ MCP Server Core         │ 12     │ Phase 1                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 7    │ 3     │ MCP Resources & Prompts │ 8      │ Phase 2                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 8    │ 7     │ LangGraph State         │ 8      │ Phase 0                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 9    │ 8     │ Specialist Agents       │ 12     │ Phase 7, Phase 2           │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 10   │ 9     │ Supervisor + HITL       │ 10     │ Phase 8                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 11   │ 5     │ Evidence Display        │ 6      │ Phase 1                    │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 12   │ 6     │ Portfolio Dashboard     │ 10     │ Phase 1, Phase 5, Phase 11 │                                                   │
│ ├──────┼───────┼─────────────────────────┼────────┼────────────────────────────┤                                                   │
│ │ 13   │ 10    │ DD Workflow Exercise    │ 10     │ Phase 9                    │                                                   │
│ └──────┴───────┴─────────────────────────┴────────┴────────────────────────────┘                                                   │
│                                                                                                                                    │
│ Total: 100 base points                                                                                                             │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ File Summary                                                                                                                       │
│                                                                                                                                    │
│ New files (~20)                                                                                                                    │
│                                                                                                                                    │
│ app/services/integration/cs4_client.py                                                                                             │
│ app/services/portfolio_data_service.py                                                                                             │
│ app/services/value_creation/__init__.py                                                                                            │
│ app/services/value_creation/ebitda.py                                                                                              │
│ app/services/value_creation/gap_analysis.py                                                                                        │
│ app/services/tracking/__init__.py                                                                                                  │
│ app/services/tracking/history_service.py                                                                                           │
│ app/services/analytics/__init__.py                                                                                                 │
│ app/services/analytics/fund_air.py                                                                                                 │
│ app/services/observability/__init__.py                                                                                             │
│ app/services/observability/metrics.py                                                                                              │
│ app/mcp/__init__.py                                                                                                                │
│ app/mcp/server.py                                                                                                                  │
│ app/agents/__init__.py                                                                                                             │
│ app/agents/state.py                                                                                                                │
│ app/agents/specialists.py                                                                                                          │
│ app/agents/supervisor.py                                                                                                           │
│ streamlit/components/evidence_display.py                                                                                           │
│ streamlit/cs5_app.py                                                                                                               │
│ exercises/agentic_due_diligence.py                                                                                                 │
│ tests/test_mcp_integration.py                                                                                                      │
│                                                                                                                                    │
│ Modified files (~4)                                                                                                                │
│                                                                                                                                    │
│ requirements.txt              — add CS5 dependencies                                                                               │
│ app/core/lifespan.py          — instantiate CS5 singletons                                                                         │
│ app/core/dependencies.py      — add CS5 DI providers                                                                               │
│ app/main.py                   — register any new routers (optional)                                                                │
│                                                                                                                                    │
│ ---                                                                                                                                │
│ Verification                                                                                                                       │
│                                                                                                                                    │
│ 1. MCP Server: python -m app.mcp.server starts without error; Claude Desktop can list and call all 6 tools                         │
│ 2. No mock data: Stop FastAPI → MCP tools should error, not return hardcoded data                                                  │
│ 3. Agents: python exercises/agentic_due_diligence.py runs full DD workflow for NVDA                                                │
│ 4. HITL: NVDA (score ~90) triggers HITL approval gate                                                                              │
│ 5. Dashboard: streamlit run streamlit/cs5_app.py shows portfolio with real scores                                                  │
│ 6. Tests: pytest tests/test_mcp_integration.py -v passes                                                                           │
│ 7. Metrics: /metrics endpoint returns Prometheus counters after tool calls   