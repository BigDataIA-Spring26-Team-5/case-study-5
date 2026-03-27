# PE Org-AI-R Platform : Multi-Agent Due Diligence & Portfolio Intelligence

> **Case Study 5: From RAG Answers to Agentic Investment Decisions**
> Big Data and Intelligent Analytics — Spring 2026 | Team 5

Multi-agent due diligence platform that extends CS1–CS4's scoring and RAG pipeline with a **LangGraph orchestrated workflow**, **MCP tool server**, **HITL approval gates**, value creation analytics (EBITDA projections, gap analysis), portfolio-level reporting (IC memos, LP letters), and a 10-page Streamlit portfolio intelligence dashboard. Agents autonomously gather evidence, score companies, project value creation, and pause for human approval when thresholds are breached.

**Investment Committee decisions are now backed by autonomous agent analysis with human-in-the-loop governance.**

---

## Links

| Resource | URL |
|----------|-----|
| **GitHub Repository** | [PE_OrgAIR_Platform_AgenticDD](https://github.com/BigDataIA-Spring26-Team-5/PE_OrgAIR_Platform_AgenticDD.git) |
| **Project Codelabs** | [CS5 Walkthrough](https://codelabs-preview.appspot.com/?file_id=16_uZuNqImUhPztCSOeEnVWznIwW3TT5R477rsmvLrSA#2) |
| **Demo Video** | [SharePoint Video](https://northeastern-my.sharepoint.com/) |
| **Streamlit Deployed UI** | [pe-org-air-platform.streamlit.app](https://pe-org-air-platform.streamlit.app/) |

---

## Table of Contents

1. [Project Overview & Context](#1-project-overview--context)
2. [Objective & Business Problem](#2-objective--business-problem)
3. [Architecture](#3-architecture)
4. [Case Study Progression](#4-case-study-progression)
5. [Pipeline Flows](#5-pipeline-flows)
6. [API Endpoints & Streamlit Dashboard](#6-api-endpoints--streamlit-dashboard)
7. [Setup & Installation](#7-setup--installation)
8. [Project Structure](#8-project-structure)
9. [Summary & Key Takeaways](#9-summary--key-takeaways)
10. [Design Decisions & Tradeoffs](#10-design-decisions--tradeoffs)
11. [Known Limitations](#11-known-limitations)
12. [Team Member Contributions & AI Usage](#12-team-member-contributions--ai-usage)

---

## 1. Project Overview & Context

The **PE Org-AI-R Platform** simulates a Private Equity due-diligence tool that measures how ready a company is to adopt and benefit from AI. CS1–CS3 built the data collection and scoring pipeline. CS4 added RAG-powered citation-backed justifications and an analyst chatbot. However, the platform still required an analyst to manually orchestrate each step — running scoring, reviewing evidence, projecting value creation, and preparing IC materials — in sequence.

**CS5 solves this** by introducing a LangGraph multi-agent workflow where specialist agents autonomously execute the full due diligence process. A supervisor agent dispatches work to four specialists (SEC Analyst, Scorer, Evidence Analyst, Value Creator), each backed by an MCP tool server. When scores or EBITDA projections cross risk thresholds, the workflow pauses at a Human-in-the-Loop gate for analyst approval before proceeding. The result is an end-to-end automated DD workflow — from evidence gathering to IC memo generation — with governance checkpoints that keep humans in control of investment decisions.

CS5 was built in five phases:

**Phase 1 — Integration Clients:** Four client classes (`cs1_client.py`, `cs2_client.py`, `cs3_client.py`, `cs4_client.py`) unify all CS1–CS4 data access into a single facade. `PortfolioDataService` provides a unified view of company metadata, scores, evidence counts, and assessment history for the dashboard and agents.

**Phase 2 — MCP Tool Server:** A Model Context Protocol stdio server exposes 6 tools (`calculate_org_air_score`, `get_company_evidence`, `generate_justification`, `project_ebitda_impact`, `run_gap_analysis`, `get_portfolio_summary`), 2 resources, and 2 prompts. Agents interact with platform data exclusively through these tools, ensuring a single source of truth for business logic.

**Phase 3 — LangGraph Agent Workflow:** A `StateGraph` with a supervisor router dispatches work to four specialist agents in sequence. Assessment types (`screening`, `limited`, `full`) control which agents run. HITL `interrupt()` gates pause the graph when Org-AI-R scores exceed 85 or fall below 40, or when risk-adjusted EBITDA projections exceed 50%. `MemorySaver` provides thread-based checkpointing for pause/resume.

**Phase 4 — Value Creation & Reporting:** `EBITDACalculator` projects sector-adjusted EBITDA impact with conservative/base/optimistic scenarios. `GapAnalyzer` identifies per-dimension improvement priorities. `ICMemoGenerator` produces Investment Committee memos (.docx/.pdf/.txt) and `LPLetterGenerator` creates quarterly LP updates (.pdf/.txt).

**Phase 5 — Portfolio Dashboard & Observability:** A 10-page Streamlit dashboard provides portfolio overview, evidence analysis, assessment history, agentic workflow control, Fund-AI-R analytics, MCP server inspection, Prometheus metrics, IC memo/LP letter generation, investment tracking, and Mem0 memory viewing. Prometheus counters and histograms track all MCP tool and agent invocations.

---

## 2. Objective & Business Problem

CS1 through CS4 built a complete AI readiness assessment platform: collecting signals, scoring across seven dimensions, indexing evidence into ChromaDB, and generating citation-backed justifications via hybrid RAG. However, the platform had two key constraints limiting real-world PE workflow adoption:

**First**, the due diligence process was manual. An analyst had to run scoring, request justifications, project EBITDA impact, perform gap analysis, and compile IC materials step by step. There was no automated workflow that could execute these stages intelligently and produce a complete DD package.

**Second**, there was no governance layer. Automated scoring could produce recommendations without any human review checkpoint. In PE, investment decisions above certain thresholds require Investment Committee approval before proceeding.

### Objectives

- **Automate Due Diligence:** Replace manual step-by-step analyst workflow with autonomous multi-agent orchestration that handles evidence gathering, scoring, value creation analysis, and report generation.
- **Enforce HITL Governance:** Pause the workflow for human approval when Org-AI-R scores or EBITDA projections cross risk thresholds — keeping humans in control of investment decisions.
- **Project Value Creation:** Produce sector-adjusted EBITDA projections and per-dimension improvement plans that quantify the investment thesis.
- **Generate IC-Ready Materials:** Automatically produce Investment Committee memos and quarterly LP letters with scoring data, gap analysis, and EBITDA scenarios.
- **Track Portfolio Performance:** Provide fund-level analytics (Fund-AI-R, sector concentration, leader/laggard identification) and per-company assessment history with trend tracking.

### Scope

| Phase Activities | Detail |
|:---|:---|
| Integration Layer | CS1–CS4 client wrappers, unified portfolio data service |
| MCP Tool Server | 6 tools, 2 resources, 2 prompts — single source of truth for agent data access |
| Agent Orchestration | LangGraph StateGraph, 4 specialist agents, supervisor router, HITL interrupt gates |
| Value Creation | Sector-adjusted EBITDA projections, dimension gap analysis, improvement roadmaps |
| Reporting | IC memo (.docx/.pdf), LP letter (.pdf), automated generation with download |
| Portfolio Analytics | Fund-AI-R (EV-weighted), sector HHI, assessment history, investment ROI tracking |
| Observability | Prometheus metrics, structlog, X-Correlation-ID, health endpoints |
| Dashboard | 10-page Streamlit app with portfolio overview, agentic workflow, and all CS5 features |

### Target Companies

**CS3 Scoring Portfolio** (5 companies with calibrated scoring parameters):

| Sector | Tickers |
|:---|:---|
| Technology | NVDA |
| Manufacturing | GE |
| Retail | WMT, DG |
| Financial Services | JPM |

**CS5 Dashboard Portfolio** (7 companies displayed in Streamlit):

| Sector | Tickers |
|:---|:---|
| Technology | NVDA, CRM, GOOGL |
| Retail | WMT |
| Financial Services | JPM |
| HR/Payroll | ADP |
| Healthcare | UNH |

The platform is preconfigured with 20+ companies in `COMPANY_NAME_MAPPINGS` (including META, TSLA, AAPL, MSFT, AMZN, IBM, ORCL, NFLX, and others) for signal collection, but composite scoring is calibrated only for the CS3 portfolio.

---

## 3. Architecture

### Architecture Diagram

[CS5 Architecture Diagram](https://mermaid.ai/d/f7139255-cc68-4b7c-988d-a5ecbfafba1a)

The platform runs as a single FastAPI server with 18 routers. CS1–CS3 routers handle data collection and scoring. CS4 adds the `/rag` router for indexing, retrieval, justification, and IC prep. CS5 adds the `/dd` router for the LangGraph agent workflow with HITL approval.

The platform follows a strict four-layer architecture:

**Layer 1 — API Layer (FastAPI Routers):** Thin HTTP wrappers. Routers receive requests, validate inputs via Pydantic models, delegate to a single service, and return structured responses. 18 routers total: 2 infrastructure (health, config), 2 CS1 (companies, portfolios), 3 CS2 (documents, signals, evidence), 7 CS3 (dimension_scores, scoring, tc_vr, position_factor, hr, orgair + assessment), 4 CS4 (rag, analyst_notes, bonus, history), 1 CS5 (due_diligence — conditionally loaded).

**Layer 2 — Service Layer (Business Logic):** All business logic lives here. Services orchestrate multi-step workflows, call external APIs, make LLM calls, and coordinate between repositories. CS5 adds `PortfolioDataService` (unified CS1–CS4 facade), `EBITDACalculator`, `GapAnalyzer`, `ICMemoGenerator`, `LPLetterGenerator`, `AssessmentHistoryService`, `FundAIRCalculator`, and `InvestmentTracker`.

**Layer 3 — Agent Layer (LangGraph + MCP):** CS5-specific. The `StateGraph` supervisor dispatches to four specialist agents that access platform data exclusively through `MCPToolCaller` (HTTP wrapper around MCP tools). `MemorySaver` provides thread-based checkpointing. Optional `Mem0` semantic memory enables cross-session agent recall.

**Layer 4 — Storage Layer:** Snowflake (primary relational store), S3 (object store for raw/parsed SEC filings), Redis (caching and task state), ChromaDB (vector database for RAG embeddings), Mem0 (optional semantic memory).

### Tech Stack

| Category | Technology | Version | Purpose |
|:---|:---|:---|:---|
| API Framework | FastAPI | 0.128.0 | REST API, async routing |
| Runtime | Python | >=3.11 | Application runtime |
| Data Warehouse | Snowflake | 4.2.0 | Primary relational storage |
| Object Storage | AWS S3 (boto3) | 1.42.35 | Document and signal storage |
| Vector DB | ChromaDB | 0.5.23 | RAG embedding storage |
| Cache | Redis | 7.1.0 | Task state, response caching |
| Agent Framework | LangGraph | 1.1.3 | Multi-agent orchestration, HITL interrupt/resume |
| Tool Protocol | MCP | 1.26.0 | Stdio tool server (6 tools, 2 resources, 2 prompts) |
| LLM Router | LiteLLM | 1.82.1 | Multi-provider LLM abstraction ($5/day budget) |
| LLM — Free | Groq (llama-3.1-8b, llama-3.3-70b) | — | Keyword expansion, HyDE, dimension detection |
| LLM — Quality | Claude Haiku 4.5 (Anthropic) | — | Chatbot, justifications, IC summaries |
| LLM — Fallback | DeepSeek (deepseek-chat) | — | Fallback for all tasks |
| Embeddings | sentence-transformers | 2.2.0 | all-MiniLM-L6-v2 (local, no API cost) |
| Semantic Memory | mem0ai | 1.0.7 | Cross-session agent recall (optional) |
| BM25 Search | rank_bm25 | 0.2.2 | Keyword retrieval (hybrid RAG) |
| Job Scraping | python-jobspy | 1.1.82 | LinkedIn, Indeed job postings |
| Patent API | PatentsView (httpx) | — | USPTO patent data |
| Tech Detection | BuiltWith API + python-Wappalyzer | — | Website tech stack fingerprinting |
| SEC Filings | sec-edgar-downloader | 5.1.0 | 10-K, 10-Q, 8-K, DEF 14A |
| PDF Parsing | pdfplumber + PyMuPDF | 0.11.9 / 1.26.7 | Document text extraction |
| HTML Parsing | BeautifulSoup4 + lxml | 4.14.3 | SEC HTML filing parsing |
| IC Memo Reports | python-docx | 1.1.2 | .docx generation (primary), WeasyPrint PDF fallback |
| LP Letter Reports | WeasyPrint | 60.0 | .pdf generation (primary), .txt fallback |
| Observability | Prometheus + structlog | 0.24.1 | Metrics + structured JSON logging |
| Orchestration | Apache Airflow | 2.9.3 | Nightly evidence refresh DAGs |
| Dashboard | Streamlit + Plotly | 1.55.0 | 10-page portfolio intelligence UI |
| Data Validation | Pydantic | 2.12.5 | Request/response schemas |
| Containerization | Docker + Docker Compose | — | API + Redis + Airflow |
| Testing | pytest + Hypothesis | — | Unit, integration, property-based testing |

---

## 4. Case Study Progression

### CS1 — Platform Foundation (Weeks 1–2)
Built the API layer, data models, Snowflake schema, S3 storage, and Redis caching. Established the entity framework (Companies, Industries, Assessments) that all subsequent case studies build on.

### CS2 — Evidence Collection (Weeks 3–4)
Built two parallel pipelines. The **Document Pipeline** downloads SEC filings (10-K, DEF 14A) from EDGAR, parses them with pdfplumber/BeautifulSoup, extracts key sections (Item 1, 1A, 7), and chunks them into ~500-token segments stored in Snowflake + S3. The **Signal Pipeline** scrapes 6 external signal categories — technology hiring (JobSpy), innovation activity (USPTO patents), digital presence (BuiltWith/Wappalyzer), leadership signals (DEF 14A proxy analysis), board composition (DEF 14A board bios), and culture (Glassdoor reviews) — normalizing each to 0–100 scores.

### CS3 — Scoring Engine (Weeks 5–6)
Transforms CS2's raw evidence into validated Org-AI-R scores through a multi-step pipeline: evidence mapping (9x7 weight matrix), rubric-based scoring (5-level rubrics for 7 dimensions), talent concentration analysis, V^R calculation with balance penalties, position factor computation, sector-adjusted H^R, synergy bonus, confidence intervals, and full Org-AI-R composite scoring.

### CS4 — RAG Search & IC Preparation (Weeks 7–8)
Introduces a Retrieval Augmented Generation layer that connects raw SEC filing evidence directly to the scoring rubric. All CS2 evidence is indexed into ChromaDB, retrieved via hybrid dense + sparse search with HyDE query enhancement and Reciprocal Rank Fusion, and passed through LLM-generated justifications that cite specific evidence, state score levels, and identify gaps. Nightly Airflow DAGs keep job signal data fresh.

### CS5 — Multi-Agent Due Diligence & Portfolio Intelligence (Weeks 9–10) — **This Submission**
CS5 introduces autonomous agent-driven due diligence with human governance. A LangGraph `StateGraph` supervisor dispatches work to four specialist agents (SEC Analyst, Scorer, Evidence Analyst, Value Creator), each accessing platform data through an MCP tool server. HITL interrupt gates pause the workflow for analyst approval when scores or projections cross thresholds. Value creation analytics project EBITDA impact and identify improvement gaps. Report generators produce IC memos (.docx/.pdf) and LP letters (.pdf). A 10-page Streamlit dashboard provides portfolio-level intelligence, agentic workflow control, and all CS5 features. Prometheus metrics and structlog provide full observability.

---

## 5. Pipeline Flows

### Pipeline 1: Integration Clients — Unified CS1–CS4 Data Access

CS5's first task was to unify all prior case study data into a single access layer for agents and the dashboard.

**`cs1_client.py` — Company Metadata:** Fetches enriched company metadata including `sector`, `sub_sector`, `revenue_millions`, `employee_count`, `fiscal_year_end`. Provides a `Sector` enum and `Company` dataclass.

**`cs2_client.py` — Signal Evidence:** Fetches CS2 signal outputs from S3 with cleaning (TOC chunk filtering, proxy boilerplate removal). Calls Groq to expand rubric keyword lists and summarize raw signals.

**`cs3_client.py` — Scoring & Rubric:** Fetches dimension scores, rubric criteria, and `CompanyAssessment` data. Provides Groq-based score estimation for zero-data dimensions.

**`cs4_client.py` — RAG & Justification:** Wraps RAG search and justification endpoints. Returns `JustificationResult` with cited evidence, score levels, and identified gaps.

**`PortfolioDataService` — Unified Facade:** Aggregates CS1–CS4 clients into `PortfolioCompanyView` (per-company) and `PortfolioView` (fund-level). Falls back to `CS3_PORTFOLIO` if CS1 portfolio tables are unavailable.

---

### Pipeline 2: MCP Tool Server

The MCP stdio server (`app/mcp/server.py`) exposes platform capabilities as tools that agents invoke via `MCPToolCaller` (an HTTP wrapper in `specialists.py`). Expensive clients (Snowflake, ChromaDB) are lazy-initialized on first tool call.

| Tool | Source | Purpose |
|:---|:---|:---|
| `calculate_org_air_score` | SCORING table (Snowflake) | Org-AI-R + V^R + H^R + dimension scores + confidence interval |
| `get_company_evidence` | `/rag/evidence/{ticker}` (FastAPI) | Evidence items with optional dimension filter |
| `generate_justification` | `/rag/justify/{ticker}/{dim}` (FastAPI) | Score justification with citations and gap analysis |
| `project_ebitda_impact` | `EBITDACalculator` (local) | Sector-adjusted EBITDA delta + H^R risk factor + scenarios |
| `run_gap_analysis` | `GapAnalyzer` via `CS3Client` (local) | Per-dimension gaps, priorities, improvement actions |
| `get_portfolio_summary` | CS3_PORTFOLIO iteration | Fund-AI-R, per-company {ticker, org_air, sector} |

**Resources:** `orgair://parameters/v2.0` (scoring parameters), `orgair://sectors` (sector baselines and timing factors)

**Prompts:** `due_diligence_assessment` (full DD template), `ic_meeting_prep` (IC preparation template)

---

### Pipeline 3: LangGraph Agent Workflow

The DD workflow (`POST /api/v1/dd/run/{ticker}`) creates a `DueDiligenceState` and dispatches it through a `StateGraph`:

```
Supervisor (conditional router)
  |
  +--> 1. SEC Analyst Node          [always]
  |       MCPToolCaller.get_company_evidence(limit=10)
  |       LLM summarize -> state["sec_analysis"]
  |       Optional: Mem0 cross-session recall
  |
  +--> 2. Scorer Node               [always]
  |       MCPToolCaller.calculate_org_air_score()
  |       HITL gate if org_air > 85 or < 40
  |       -> state["scoring_result"]
  |
  +--> 3. Evidence Node              [limited | full]
  |       MCPToolCaller.get_company_evidence(3 dims)
  |       LLM summarize -> state["talent_analysis"]
  |
  +--> 4. Value Creator Node         [full only]
  |       MCPToolCaller.run_gap_analysis()
  |       MCPToolCaller.project_ebitda_impact()
  |       HITL gate if risk-adjusted EBITDA >= 50%
  |       LLM narrative -> state["value_creation_plan"]
  |
  +--> 5. HITL Approval Node         [if requires_approval]
  |       interrupt(payload) -> graph pauses
  |       POST /api/v1/dd/approve/{thread_id} resumes
  |       Command(resume={decision, approved_by, comments})
  |
  +--> 6. Complete Node
          set completed_at -> END
          Record AssessmentSnapshot
          Store Mem0 semantic memory
```

**Assessment types** control scope: `screening` runs SEC + Scorer only, `limited` adds Evidence, `full` adds Value Creation.

**State management:** `DueDiligenceState` (TypedDict) uses `Annotated[List[AgentMessage], operator.add]` for append-only message history. `MemorySaver` provides thread-based checkpointing for HITL resume.

---

### Pipeline 4: Value Creation Analytics

**EBITDACalculator** (`ebitda.py`): Projects sector-adjusted EBITDA impact from Org-AI-R score improvement.
- Sector multipliers (bps per point): Tech=0.45, FinServ=0.38, Healthcare=0.35, Mfg=0.30, Retail=0.28
- H^R risk adjustment: `hr_risk_factor = min(1.0, max(0.5, h_r_score / 80))`
- Output: conservative/base/optimistic scenarios, time-to-value (12–36 months), confidence level

**GapAnalyzer** (`gap_analysis.py`): Identifies per-dimension improvement priorities.
- Ranks dimensions by gap size (target - current score)
- Maps to next-level rubric criteria and specific improvement actions
- Output: sorted dimension gaps, top 3 priorities

---

### Pipeline 5: Report Generation

**ICMemoGenerator** (`ic_memo.py`): Produces Investment Committee memos.
- Format chain: .docx (python-docx, primary) -> .pdf (WeasyPrint, fallback) -> .txt (final fallback)
- Content: executive summary, Org-AI-R/V^R/H^R scores, 7 dimension breakdown, gap analysis with 100-day plan, EBITDA scenario projections, recommendation (PROCEED / MONITOR / CONDITIONAL)

**LPLetterGenerator** (`lp_letter.py`): Produces quarterly Limited Partner updates.
- Format chain: .pdf (WeasyPrint HTML-to-PDF, primary) -> .txt (fallback)
- Content: Fund-AI-R score, portfolio composition, AI leaders/laggards, highlights, outlook

---

### Pipeline 6: Portfolio Analytics & Tracking

**FundAIRCalculator** (`fund_air.py`): Computes EV-weighted Fund-AI-R score across the portfolio, sector concentration (HHI), quartile distribution, and identifies AI leaders (>=70) and laggards (<50).

**AssessmentHistoryService** (`history_service.py`): Captures point-in-time snapshots (org_air, vr, hr, synergy, dimension scores). Tracks trends: delta since entry, 30-day delta, 90-day delta, trend direction.

**InvestmentTracker** (`investment_tracker.py`): Computes portfolio ROI from revenue lift, EBITDA lift, and multiple expansion.

---

### Pipeline 7: Airflow Nightly Evidence Refresh

Three Airflow DAGs maintain data freshness:

| DAG | Schedule | Purpose |
|:---|:---|:---|
| `pe_evidence_indexing` | `0 2 * * *` (2 AM) | Index unindexed evidence into ChromaDB |
| `pe_signal_collection_parent` | `0 3 * * *` (3 AM) | Orchestrate all signal collection |
| `pe_job_signals_collection` | (child DAG) | JobSpy AI/ML job posting collection |

---

## 6. API Endpoints & Streamlit Dashboard

### CS1 — Company Metadata

| Method | Endpoint | Description |
|:---|:---|:---|
| POST | `/api/v1/companies` | Register any ticker; Groq auto-populates sector, revenue, employee count |
| GET | `/api/v1/companies/{ticker}` | Retrieve enriched metadata |
| GET | `/api/v1/companies/all` | All companies with metadata |
| POST | `/api/v1/portfolios` | Create/manage portfolio entries |

### CS2 — Evidence Collection

| Method | Endpoint | Description |
|:---|:---|:---|
| POST | `/api/v1/documents/collect` | Download SEC filings from EDGAR -> S3 -> Snowflake |
| POST | `/api/v1/documents/parse/{ticker}` | Extract text, identify sections -> parsed JSON to S3 |
| POST | `/api/v1/documents/chunk/{ticker}` | Split into overlapping chunks -> S3 + Snowflake |
| POST | `/api/v1/signals/collect` | All 6 signal categories in one background task |
| GET | `/api/v1/evidence` | Aggregated evidence stats |

### CS3 — Scoring

| Method | Endpoint | Description |
|:---|:---|:---|
| POST | `/api/v1/scoring/{ticker}` | CS2 signals -> rubric-score SEC sections -> 7 dimension scores |
| POST | `/api/v1/scoring/tc-vr/portfolio` | Talent Concentration + V^R for all companies |
| POST | `/api/v1/scoring/pf/portfolio` | Position Factor for all companies |
| POST | `/api/v1/scoring/hr/portfolio` | Human Readiness H^R for all companies |
| POST | `/api/v1/scoring/orgair/portfolio` | Final Org-AI-R score for all companies |
| POST | `/api/v1/scoring/orgair/results` | Write results JSON to S3 |

### CS4 — RAG & Analyst Notes

| Method | Endpoint | Description |
|:---|:---|:---|
| POST | `/api/v1/rag/index/{ticker}` | Index CS2 evidence into ChromaDB + seed BM25 |
| POST | `/api/v1/rag/search` | Hybrid dense + sparse search with optional HyDE |
| GET | `/api/v1/rag/justify/{ticker}/{dimension}` | Citation-backed score justification with gap analysis |
| GET | `/api/v1/rag/ic-prep/{ticker}` | All 7 justifications + executive summary + recommendation |
| GET | `/api/v1/rag/chatbot/{ticker}?q=...` | Natural language Q&A with cited evidence |
| POST | `/api/v1/analyst-notes/{ticker}/interview` | Submit interview transcript |
| POST | `/api/v1/analyst-notes/{ticker}/dd-finding` | Submit due diligence finding |
| POST | `/api/v1/analyst-notes/{ticker}/data-room` | Submit data room document summary |

### CS5 — Due Diligence Workflow

| Method | Endpoint | Description |
|:---|:---|:---|
| POST | `/api/v1/dd/run/{ticker}` | Launch LangGraph DD workflow (screening / limited / full) |
| GET | `/api/v1/dd/status/{thread_id}` | Get workflow state, agent messages, completion status |
| POST | `/api/v1/dd/approve/{thread_id}` | Resume paused workflow with approval decision |

### CS5 — Configuration & Health

| Method | Endpoint | Description |
|:---|:---|:---|
| GET | `/healthz` | Liveness check |
| GET | `/health` | Deep dependency check (Snowflake, Redis, S3) |
| GET | `/api/v1/config/scoring-parameters` | Current alpha, beta, dimension weights |
| GET | `/api/v1/config/sector-baselines` | Sector H^R baselines and timing factors |
| GET | `/api/v1/config/portfolio` | CS3 portfolio tickers |
| GET | `/api/v1/rag/diagnostics` | ChromaDB doc count, sparse index size, retrieval config |
| GET | `/metrics` | Prometheus metrics scrape endpoint |

### Streamlit Dashboard

Entry point: `streamlit run streamlit/cs5_app.py` -> `http://localhost:8501`

| Page | What It Shows |
|:---|:---|
| Portfolio Overview | Fund-AI-R metrics, org_air vs sector scatter, quartile bar chart, company metrics table |
| Evidence Analysis | 7 justification panels per company, evidence cards, dimension score table |
| Assessment History | Historical snapshot trends, delta tracking (30d, 90d, since entry) |
| Agentic Workflow | Assessment type selector, DD run/status/approve, HITL approval dialog |
| Fund-AI-R Analytics | Fund-level metrics, sector concentration (HHI), leaders/laggards |
| MCP Server | Tool documentation, live tool testing interface |
| Prometheus Metrics | Counter/histogram visualization from `/metrics` endpoint |
| IC Memo / LP Letter | Document generation with download buttons (docx/pdf/txt) |
| Investment Tracker | ROI projections (revenue lift, EBITDA lift, multiple expansion) |
| Mem0 Memory | Semantic memory viewer for cross-session agent recall |

---

## 7. Setup & Installation

### Prerequisites

- Python 3.11 or higher installed
- Docker and Docker Compose installed
- Snowflake account (`PE_ORGAIR_DB` database, `PLATFORM` schema)
- AWS S3 bucket with access keys
- Redis instance (provided via Docker Compose)
- API credentials: `GROQ_API_KEY`, `ANTHROPIC_API_KEY`

### Step 1: Clone the Repository

```bash
git clone https://github.com/BigDataIA-Spring26-Team-5/PE_OrgAIR_Platform_AgenticDD.git
cd PE_OrgAIR_Platform_AgenticDD/pe-org-air-platform
```

### Step 2: Configure Environment Variables

```bash
cp .env.example .env
```

Configure the following variable groups inside `.env`:

| Variable Group | Keys |
|:---|:---|
| Snowflake | `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_ROLE` |
| AWS S3 | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME` |
| Redis | `REDIS_URL` (default: `redis://localhost:6379/0`) |
| LLMs | `GROQ_API_KEY`, `ANTHROPIC_API_KEY` |
| ChromaDB | `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE`, `CHROMA_HOST` |
| SEC EDGAR | `SEC_COMPANY_NAME`, `SEC_EMAIL` (composed into User-Agent header) |
| Mem0 (optional) | `MEM0_API_KEY` — enables cross-session agent memory |

### Step 3: Install Dependencies

```bash
poetry install
# OR
pip install -r requirements.txt
```

### Step 4: Set Up the Database

Run the schema SQL files against your Snowflake account:

```bash
app/database/schema.sql                  # Core CS1 tables
app/database/document_schema.sql          # Documents table
app/database/signals_schema.sql           # Signals tables
app/database/final_scoring_schema.sql     # SCORING output table (CS3)
```

### Step 5: Run with Docker

```bash
docker compose up -d
docker ps
# Starts: API (:8000), Redis (:6379), Airflow (:8080)
```

### Step 6: Run the FastAPI Backend Locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs: `http://localhost:8000/docs`

### Step 7: Launch the Streamlit Dashboard

```bash
streamlit run streamlit/cs5_app.py
# Dashboard: http://localhost:8501
```

### Running Tests

```bash
poetry run pytest                                    # All tests
poetry run pytest --cov=app --cov-report=term-missing # With coverage
```

Test results output to `test_results/` (JUnit XML + coverage HTML).

---

## 8. Project Structure

```
pe-org-air-platform/
├── app/
│   ├── main.py                          # FastAPI entry point (18 routers)
│   ├── shutdown.py                      # Graceful shutdown event
│   ├── core/
│   │   ├── dependencies.py              # Singleton DI providers
│   │   ├── errors.py                    # PlatformError hierarchy
│   │   ├── lifespan.py                  # Startup init + shutdown hooks
│   │   └── logging_config.py            # structlog configuration
│   ├── middleware/
│   │   └── correlation.py               # X-Correlation-ID middleware
│   ├── config/
│   │   ├── __init__.py                  # App settings (Snowflake, Redis, S3, LLM keys)
│   │   ├── company_mappings.py          # CS3_PORTFOLIO, COMPANY_NAME_MAPPINGS, CompanyRegistry
│   │   └── retrieval_settings.py        # RAG tunables
│   ├── routers/                         # 18 HTTP endpoint routers
│   │   ├── health.py                    # /healthz, /health
│   │   ├── config.py                    # Scoring params, weights, baselines
│   │   ├── companies.py                 # CS1: Company CRUD + Groq enrichment
│   │   ├── portfolios.py                # CS1: Portfolio management
│   │   ├── documents.py                 # CS2: SEC collection / parsing / chunking
│   │   ├── signals.py                   # CS2: Signal collection (6 categories)
│   │   ├── evidence.py                  # CS2: Evidence summary
│   │   ├── dimension_scores.py          # CS3: Dimension weight config
│   │   ├── scoring.py                   # CS3: Dimension scoring pipeline
│   │   ├── tc_vr_scoring.py             # CS3: Talent Concentration + V^R
│   │   ├── position_factor.py           # CS3: Position Factor
│   │   ├── hr_scoring.py                # CS3: Human Readiness H^R
│   │   ├── orgair_scoring.py            # CS3: Org-AI-R (+ assessment_router)
│   │   ├── rag.py                       # CS4: RAG indexing + search + chatbot
│   │   ├── analyst_notes.py             # CS4: Analyst notes
│   │   ├── bonus.py                     # CS4: Bonus analysis
│   │   ├── history.py                   # CS4: Assessment history
│   │   └── due_diligence.py             # CS5: LangGraph DD workflow + HITL
│   ├── services/
│   │   ├── signals/                     # 6 signal collection services
│   │   │   ├── job_signal_service.py    # JobSpy -> technology_hiring
│   │   │   ├── patent_signal_service.py # PatentsView -> innovation_activity
│   │   │   ├── tech_signal_service.py   # BuiltWith -> digital_presence
│   │   │   ├── leadership_service.py    # DEF-14A -> leadership_signals
│   │   │   ├── board_composition_service.py # Board -> board_composition
│   │   │   ├── culture_signal_service.py # Glassdoor -> culture
│   │   │   └── evidence_service.py      # Evidence aggregation
│   │   ├── search/
│   │   │   └── vector_store.py          # ChromaDB client + EMBEDDING_MODEL
│   │   ├── retrieval/
│   │   │   ├── hybrid.py                # BM25 + dense hybrid retrieval + RRF
│   │   │   ├── hyde.py                  # HyDE query enhancement
│   │   │   └── dimension_mapper.py      # Source -> dimension routing
│   │   ├── justification/
│   │   │   └── generator.py             # LLM justification generator
│   │   ├── llm/
│   │   │   └── router.py                # ModelRouter (Groq/Claude/DeepSeek via LiteLLM)
│   │   ├── integration/                 # CS5 integration clients
│   │   │   ├── cs1_client.py            # Company metadata
│   │   │   ├── cs2_client.py            # Signal evidence
│   │   │   ├── cs3_client.py            # Scoring & rubric
│   │   │   └── cs4_client.py            # RAG & justification
│   │   ├── value_creation/
│   │   │   ├── ebitda.py                # EBITDA projections
│   │   │   └── gap_analysis.py          # Dimension gap analysis
│   │   ├── reporting/
│   │   │   ├── ic_memo.py               # IC memo (.docx/.pdf/.txt)
│   │   │   └── lp_letter.py             # LP letter (.pdf/.txt)
│   │   ├── tracking/
│   │   │   ├── history_service.py       # Assessment snapshots + trends
│   │   │   └── investment_tracker.py    # ROI computation
│   │   ├── analytics/
│   │   │   └── fund_air.py              # Fund-AI-R calculator
│   │   ├── observability/
│   │   │   └── metrics.py               # Prometheus counters/histograms
│   │   ├── workflows/
│   │   │   └── ic_prep.py               # IC meeting package workflow
│   │   ├── collection/
│   │   │   └── analyst_notes.py         # AnalystNotesCollector
│   │   ├── composite_scoring_service.py # TC, V^R, PF, H^R, Org-AI-R orchestrator
│   │   ├── portfolio_data_service.py    # Unified CS1-CS4 facade
│   │   ├── cache.py                     # Redis TTL cache helpers
│   │   ├── s3_storage.py                # AWS S3 client wrapper
│   │   └── task_store.py                # Redis-backed background task state
│   ├── agents/                          # CS5 LangGraph multi-agent
│   │   ├── state.py                     # DueDiligenceState TypedDict + AgentMessage
│   │   ├── specialists.py               # MCPToolCaller + 4 specialist agents
│   │   ├── supervisor.py                # StateGraph factory + HITL interrupt node
│   │   └── memory.py                    # Mem0 semantic memory (optional)
│   ├── mcp/
│   │   └── server.py                    # MCP stdio server (6 tools, 2 resources, 2 prompts)
│   ├── pipelines/                       # CS2 data collection logic
│   │   ├── sec_edgar.py                 # SEC EDGAR filing collector
│   │   ├── job_signals.py               # Job posting signal processing
│   │   ├── patent_signals.py            # Patent signal processing
│   │   ├── tech_signals.py              # Technology signal analysis
│   │   ├── leadership_analyzer.py       # Leadership analysis
│   │   ├── board_analyzer.py            # Board composition analysis
│   │   └── glassdoor_collector.py       # Glassdoor culture data
│   ├── repositories/                    # 11 Snowflake data access modules
│   ├── scoring/                         # 10 calculation modules (evidence_mapper, rubric, TC, VR, PF, HR, synergy, orgair, confidence)
│   ├── models/                          # Pydantic domain models
│   ├── schemas/                         # API response models
│   ├── guardrails/                      # RAG input/output validation
│   ├── prompts/                         # LLM prompt templates
│   ├── scripts/                         # Utility scripts (seed_history, fill_sectors, etc.)
│   └── database/                        # SQL schemas + seed data
├── streamlit/
│   ├── cs5_app.py                       # CS5 dashboard entry point (10 pages)
│   ├── views/                           # 8 view modules
│   └── components/                      # Charts + evidence display
├── dags/                                # 3 Airflow DAGs
├── tests/                               # 25+ pytest test files
├── exercises/                           # Learning materials
├── data/                                # Local data cache
├── results/                             # Pipeline output JSON + reports
├── docker-compose.yml                   # API + Redis + Airflow
├── Dockerfile
└── pyproject.toml                       # Poetry config (Python >=3.11, 50+ deps)
```

---

## 9. Summary & Key Takeaways

CS5 takes everything built in CS1 through CS4 and automates the full due diligence workflow. The scoring pipeline and RAG layer were solid, but analysts still had to manually orchestrate each step — run scoring, request justifications, project EBITDA impact, perform gap analysis, and compile IC materials. That is what CS5 solves.

The LangGraph supervisor dispatches work to four specialist agents, each accessing platform data exclusively through MCP tools. This ensures a single source of truth — agents cannot bypass the scoring engine or RAG pipeline. When the Scorer agent detects an Org-AI-R score above 85 or below 40, or when the Value Creator projects risk-adjusted EBITDA above 50%, the workflow pauses at a HITL gate for analyst approval. This keeps humans in control of investment decisions while automating the research legwork.

The value creation pipeline quantifies the investment thesis: the EBITDACalculator projects sector-adjusted returns from score improvement, and the GapAnalyzer identifies the specific dimensions and rubric criteria that need attention. Report generators compile everything into IC memos and LP letters that analysts can download and present directly.

### LLM Provider Strategy

| Provider | Tasks | Cost |
|:---|:---|:---|
| Groq (FREE) | Keyword expansion, HyDE, dimension detection, evidence extraction | ~$0.00/1K tokens |
| Claude Haiku 4.5 | Chatbot, justifications, IC summaries, agent reasoning | $0.00125/1K tokens |
| DeepSeek | Fallback for all tasks | $0.00014/1K tokens |

All LLM calls route through LiteLLM with a **$5/day budget** enforced by `ModelRouter`.

### Observability

- **Prometheus:** `@track_mcp_tool` and `@track_agent` decorators on all tool and agent invocations. Counters and histograms exposed at `GET /metrics`.
- **structlog:** JSON-formatted structured logging with `X-Correlation-ID` on every request/response.
- **Health endpoints:** `GET /healthz` (liveness), `GET /health` (Snowflake + Redis + S3 dependency check).

---

## 10. Design Decisions & Tradeoffs

- **MCP tools over direct service calls:** Agents interact with the platform exclusively through the MCP tool server rather than calling services directly. This adds HTTP latency per tool call but enforces a single source of truth — agents cannot bypass scoring logic or RAG pipeline. The MCPToolCaller HTTP wrapper mirrors tool dispatch, so adding a new tool to the MCP server automatically makes it available to all agents.

- **HITL interrupt over post-hoc review:** The LangGraph `interrupt()` mechanism pauses the graph mid-execution rather than completing and flagging for review. This prevents value creation analysis from running on scores that haven't been approved, saving compute cost and ensuring the IC memo reflects the approved score. The trade-off is that paused workflows consume MemorySaver checkpoint state until resumed.

- **MemorySaver over Redis checkpointing:** Thread-based in-memory checkpointing was chosen for simplicity. For production with multiple workers, this would need to be replaced with a persistent checkpointer (e.g., Redis or PostgreSQL).

- **python-docx for IC memos, WeasyPrint for LP letters:** IC memos use python-docx because analysts typically edit them in Word before the IC meeting. LP letters use WeasyPrint HTML-to-PDF because they are final-form documents sent to investors. Both have .txt fallbacks for environments without the rendering libraries.

- **Separate CS3 and CS5 portfolios:** The 5-company CS3 portfolio has calibrated scoring parameters (expected ranges, market cap percentiles, sector mappings). The 7-company CS5 dashboard portfolio includes additional companies for demonstration. Scoring is calibrated only for CS3 companies.

- **Graceful degradation throughout:** Redis cache, Mem0 memory, and LangGraph DD router all degrade gracefully — the platform runs without them. Redis falling out returns cache misses; Mem0 being unavailable means agents have no cross-session recall; DD router import failure disables only the `/dd` endpoints.

---

## 11. Known Limitations

1. **Composite scoring limited to 5 companies.** The scoring calibration dicts (`EXPECTED_TC_VR_RANGES`, `MARKET_CAP_PERCENTILES`, `COMPANY_SECTORS`) are hardcoded for NVDA, JPM, WMT, GE, DG. Scoring arbitrary tickers produces uncalibrated results.

2. **MemorySaver is in-memory only.** Workflow checkpoints are lost on service restart. Paused HITL workflows cannot be resumed after a restart. Production deployment would need a persistent checkpointer.

3. **BM25 resets on service restart.** The in-memory sparse index must be rebuilt via the Airflow DAG or manual re-indexing. Between restart and rebuild, RAG falls back to dense-only retrieval.

4. **LLM non-determinism in agent narratives.** Two identical DD runs can produce slightly different agent summaries. Structured prompts and rubric anchoring reduce but do not eliminate this.

5. **Mem0 API instability across versions.** The `AgentMemory` class tries 10+ calling conventions to maintain compatibility across mem0ai versions. If the library changes its API again, memory storage may silently fail (with graceful degradation).

6. **EBITDA projections are illustrative.** Sector multipliers and implementation costs are estimates for demonstration purposes, not validated against actual PE deal outcomes.

---

## 12. Team Member Contributions & AI Usage

### Bhavya

Bhavya designed the end-to-end pipeline architecture and built the Streamlit application, covering the 10-page portfolio intelligence dashboard with portfolio overview, evidence analysis, assessment history, agentic workflow control, Fund-AI-R analytics, MCP server inspection, Prometheus metrics visualization, IC memo/LP letter generation, investment tracking, and Mem0 memory viewing. She also worked alongside Aqeel to integrate the Airflow scheduling layer.

### Deepika

Deepika built the CS1, CS2, CS3, and CS4 client wrapper files that form the bridge between the agent layer and the prior case study pipelines. She extended the integration layer with the `PortfolioDataService` unified facade and handled project documentation including the architecture diagrams and Codelabs walkthrough.

### Aqeel

Aqeel designed and built the CS5 agent framework — the LangGraph `StateGraph` with supervisor routing, four specialist agents (SEC Analyst, Scorer, Evidence, Value Creator), the MCP tool server (6 tools, 2 resources, 2 prompts), HITL interrupt/resume gates, and the `MCPToolCaller` HTTP wrapper. He also built the value creation analytics (EBITDACalculator, GapAnalyzer), report generators (ICMemoGenerator, LPLetterGenerator), portfolio analytics (FundAIRCalculator, AssessmentHistoryService, InvestmentTracker), Prometheus observability layer, and the Mem0 semantic memory integration.

### AI Tools Usage Disclosure

We used Claude Code (architecture, scaffolding, and documentation) and ChatGPT (formula verification). All AI-generated code was reviewed and tested against expected score ranges. AI served as a productivity aid, not a substitute for understanding the scoring methodology and agent orchestration patterns.

### Resources

| Resource | Link |
|:---|:---|
| GitHub Repository | https://github.com/BigDataIA-Spring26-Team-5/PE_OrgAIR_Platform_AgenticDD.git |
| FastAPI Documentation | https://fastapi.tiangolo.com/ |
| LangGraph Documentation | https://langchain-ai.github.io/langgraph/ |
| MCP Specification | https://modelcontextprotocol.io/ |
| Snowflake Documentation | https://docs.snowflake.com/ |
| SEC EDGAR | https://www.sec.gov/edgar |
| Streamlit Documentation | https://docs.streamlit.io/ |
| Prometheus Documentation | https://prometheus.io/docs/ |

---

*Big Data and Intelligent Analytics — Spring 2026*
*Case Study 5: Multi-Agent Due Diligence — "From RAG Answers to Agentic Investment Decisions"*
