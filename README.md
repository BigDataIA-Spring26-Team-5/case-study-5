# PE Org-AI-R Platform

**Private equity portfolio scoring engine** for the CS1–CS5 case study series. Evaluates portfolio companies against a 7-dimension AI Readiness (V^R) framework and produces an **Org-AI-R** composite score to support Investment Committee decisions.

CS3 portfolio: **NVDA, JPM, WMT, GE, DG**

---

## What It Does

The platform runs a sequential evidence → scoring → reporting pipeline across four case studies:

| Phase | What runs | Key output |
|-------|-----------|------------|
| CS1 | Company registration + Groq metadata enrichment | Company record in Snowflake |
| CS2 | SEC collection → parsing → chunking → 4 signal scores | Raw filings on S3, signals in Snowflake |
| CS3 | 7-dimension V^R rubric scoring → TC / H^R / PF / Org-AI-R composite | Assessment record in Snowflake |
| CS4 | ChromaDB RAG indexing → chatbot → IC-prep package | Justifications + chatbot answers |

CS5 (MCP + LangGraph agents) is the next phase — all the infrastructure it needs is in place.

### The 7 V^R Dimensions

| Dimension | Primary evidence sources |
|-----------|--------------------------|
| `data_infrastructure` | SEC 10-K Items 1 & 7 |
| `ai_governance` | SEC 10-K Item 1A, DEF 14A |
| `technology_stack` | SEC 10-K, USPTO patents |
| `talent` | JobSpy job postings, Glassdoor |
| `leadership` | SEC MD&A, DEF 14A |
| `use_case_portfolio` | SEC Items 1 & 7 |
| `culture` | Glassdoor reviews |

---

## Architecture

Calls flow strictly in one direction:

```
Routers  →  Services  →  Pipelines / Repositories  →  Data Stores
```

**Routers** (`app/routers/`) — path matching, request validation, HTTP status. Delegates everything to services via `Depends()`.

**Services** (`app/services/`) — business logic, LLM calls, external API orchestration, scoring formulas. Never raises `HTTPException` directly; raises `PlatformError` subclasses instead.

**Pipelines** (`app/pipelines/`) — pure computation: SEC text extraction, patent scoring, board governance analysis, culture scoring. No HTTP, no FastAPI, no app.state.

**Repositories** (`app/repositories/`) — all Snowflake SQL. One class per table domain.

**Data stores:**

| Store | Role |
|-------|------|
| Snowflake | Primary relational store (companies, documents, signals, scores) |
| Redis | Response cache (5-min TTL) + background task state |
| AWS S3 | Raw/parsed/chunked SEC filings, signal data, Glassdoor cache, results JSON |
| ChromaDB | Dense vector store + BM25 sparse index for RAG |

### Dependency Injection

All shared state lives on `app.state`. `app/core/lifespan.py` creates every singleton at startup — one Snowflake connection per repository, one Redis client, one ChromaDB client. Each `Depends()` provider in `app/core/dependencies.py` is a one-liner that reads from `request.app.state`. No module-level singleton caches anywhere.

---

## Project Structure

```
app/
├── main.py                   # FastAPI app, router registration, exception handlers
│
├── core/
│   ├── lifespan.py           # Creates all singletons on app.state at startup; cleans up at shutdown
│   ├── dependencies.py       # FastAPI Depends() providers — thin reads from request.app.state
│   ├── settings.py           # Pydantic BaseSettings — env vars, scoring params, dimension weights
│   ├── errors.py             # PlatformError hierarchy + ERROR_STATUS_MAP + validation handler
│   ├── exceptions.py         # RepositoryException hierarchy (pending merge into errors.py)
│   └── logging_config.py     # structlog setup with correlation ID injected into every log entry
│
├── routers/                  # One file per domain (companies, documents, signals, scoring, rag …)
│
├── services/                 # Business logic layer
│   ├── cache.py              # Redis singleton + TTL constants + cached_query() helper
│   ├── redis_cache.py        # RedisCache class (get/set/delete/delete_pattern)
│   ├── task_store.py         # Redis-backed background task state (create/update/get)
│   ├── s3_storage.py         # S3 client wrapper
│   ├── scoring_service.py    # Dimension scoring pipeline, prerequisite guards, run tracking
│   ├── composite_scoring_service.py  # TC, V^R, H^R, Position Factor, Org-AI-R formulas
│   ├── base_signal_service.py        # Abstract orchestrator: delete → collect → persist → summary
│   ├── job_signal_service.py         # technology_hiring  (JobSpy)
│   ├── patent_signal_service.py      # innovation_activity  (PatentsView / USPTO)
│   ├── tech_signal_service.py        # digital_presence  (BuiltWith + Wappalyzer + LLM subdomains)
│   ├── leadership_service.py         # leadership_signals  (DEF 14A)
│   ├── board_composition_service.py  # board_composition  (proxy statement parsing)
│   ├── culture_signal_service.py     # culture  (Glassdoor S3 cache)
│   ├── evidence_service.py           # Document aggregation helper
│   ├── llm/router.py         # ModelRouter: task-based routing (Groq / DeepSeek / Claude Haiku)
│   ├── retrieval/            # hybrid.py (BM25+dense), dimension_mapper.py, hyde.py
│   ├── justification/        # generator.py — LLM dimension score justification
│   ├── workflows/            # ic_prep.py — IC meeting package workflow
│   ├── integration/          # cs2_client.py (BaseAPIClient), cs1_client.py
│   └── collection/           # analyst_notes.py — post-LOI DD notes collector
│
├── pipelines/                # Pure business logic — no HTTP knowledge
│   ├── board_analyzer.py     # DEF 14A proxy parsing + board governance scoring
│   ├── glassdoor_collector.py
│   ├── job_signals.py
│   ├── patent_signals.py
│   ├── tech_signals.py
│   ├── leadership_analyzer.py
│   ├── sec_edgar.py
│   ├── document_parser.py
│   ├── chunking.py
│   └── signal_pipeline_state.py
│
├── repositories/             # All Snowflake SQL — one class per table domain
│   ├── base.py               # BaseRepository + Snowflake connection factory
│   └── …                     # company, document, signal, scoring, assessment, health …
│
├── schemas/                  # Canonical API response shapes (separate from domain models)
│   ├── scoring.py            # DimensionScoreRead, CompanyAssessmentRead
│   └── evidence.py           # CompanyEvidenceResponse, Backfill*, EvidenceStats*
│
├── models/                   # Pydantic domain types for internal use
│   ├── evidence.py           # DocumentSummary, SignalEvidence, GlassdoorReview,
│   │                         # CultureSignal, BoardMember, GovernanceSignal
│   ├── signal.py             # CompanySignalSummary
│   ├── dimension.py          # DIMENSION_WEIGHTS (sourced from Settings)
│   └── enumerations.py       # DIMENSION_ALIAS_MAP
│
├── config/
│   ├── company_mappings.py   # COMPANY_NAME_MAPPINGS, CS3_PORTFOLIO, CompanyRegistry
│   └── retrieval_settings.py # RAG tunables (also exposed via Settings)
│
├── clients/
│   └── base.py               # BaseAPIClient — async HTTP + exponential backoff retry
│
├── middleware/
│   └── correlation.py        # CorrelationIdMiddleware + get_correlation_id()
│
├── scoring/                  # Pure scoring calculators (no I/O)
│   ├── orgair_calculator.py
│   ├── hr_calculator.py
│   ├── vr_calculator.py
│   ├── talent_concentration.py
│   ├── position_factor.py
│   ├── confidence_calculator.py
│   ├── evidence_mapper.py
│   └── rubric_scorer.py
│
├── guardrails/               # RAG input/output validation
│   ├── input_guards.py       # validate_ticker, validate_question, validate_dimension
│   └── output_guards.py      # check_answer_length, check_answer_grounded, check_no_refusal
│
├── prompts/                  # LLM prompt templates
│   └── rag_prompts.py        # Chatbot + dimension detection system/user prompts
│
└── utils/
    ├── company_resolver.py
    ├── id_utils.py
    └── serialization.py      # serialize_row() — Snowflake rows to JSON-safe types
```

---

## Setup

**Requirements:** Python ≥ 3.11, Poetry, Docker (for Redis + ChromaDB)

```bash
# 1. Install dependencies
poetry install

# 2. Start infrastructure
docker compose up -d          # Redis on :6379, ChromaDB on :8001

# 3. Copy and fill env file
cp .env.example .env
# Required: SNOWFLAKE_*, AWS_*, REDIS_URL, GROQ_API_KEY, ANTHROPIC_API_KEY

# 4. Run the API
poetry run uvicorn app.main:app --reload
```

API is at `http://localhost:8000`. Swagger UI at `/docs`.

### Key environment variables

| Variable | Used by |
|----------|---------|
| `SNOWFLAKE_ACCOUNT/USER/PASSWORD/DATABASE/SCHEMA/WAREHOUSE/ROLE` | All repositories |
| `REDIS_URL` | Cache, task store |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME` | S3 storage |
| `GROQ_API_KEY` | Groq enrichment, ModelRouter, keyword expansion |
| `ANTHROPIC_API_KEY` | Claude Haiku (chatbot endpoint) |
| `CHROMA_PERSIST_DIR` | ChromaDB local store (default: `./chroma_data`) |

### Run order for a fresh company

```
POST /api/v1/companies                        # CS1: register company
POST /api/v1/documents/collect                # CS2: download SEC filings → S3
POST /api/v1/documents/parse/{ticker}         # CS2: extract text → S3
POST /api/v1/documents/chunk/{ticker}         # CS2: chunk → S3 + Snowflake
POST /api/v1/signals/collect                  # CS2: run all 4 signal collectors
POST /api/v1/scoring/{ticker}                 # CS3: dimension scoring
POST /api/v1/scoring/tc-vr/portfolio          # CS3: TC + V^R
POST /api/v1/scoring/pf/portfolio             # CS3: Position Factor
POST /api/v1/scoring/hr/portfolio             # CS3: H^R
POST /api/v1/scoring/orgair/portfolio         # CS3: final Org-AI-R
POST /api/v1/rag/index/{ticker}               # CS4: index evidence into ChromaDB
GET  /api/v1/rag/chatbot/{ticker}?q=...       # CS4: chatbot
GET  /api/v1/rag/ic-prep/{ticker}             # CS4: IC package
```

---

## Key Design Decisions

**Lifespan-managed singletons over module-level caches.** All Snowflake connections, the Redis client, ChromaDB, and every service instance are created once in `app/core/lifespan.py` and attached to `app.state`. `Depends()` providers in `dependencies.py` are single-line getters. This means clean shutdown (connections are explicitly closed), no duplicate connections from import-time side effects, and a clear place to look when something doesn't start.

**Config fully consolidated in `Settings`.** All scoring parameters (alpha, beta, dimension weights, sector baselines), infrastructure URLs, LLM model names, and cache TTLs live in `app/core/settings.py` as a `pydantic_settings.BaseSettings` class. The API exposes them at `GET /api/v1/config/*` so CS5's MCP resources can read live values instead of hardcoding them.

**`PlatformError` hierarchy replaces ad-hoc HTTPException raises.** Services raise typed domain errors (`NotFoundError`, `PipelineIncompleteError`, `ScoringInProgressError`, etc.); a single exception handler in `main.py` translates them to structured JSON with `error_code`, `message`, `details`, `correlation_id`, and timestamp. This makes error responses machine-readable for LangGraph agents and keeps service code free of HTTP concerns.

**BM25 index rebuilt from ChromaDB on startup.** `HybridRetriever.rebuild_sparse_index_from_chroma()` is called during lifespan startup. Without this, a server restart silently degraded hybrid retrieval to dense-only. The startup log emits `bm25_index_rebuilt` with a doc count so you can see if it's populated.

---

## Health & Observability

```
GET /healthz                  # Liveness — always 200 if the process is up
GET /health                   # Dependency check: Snowflake, Redis, S3
GET /api/v1/rag/diagnostics   # ChromaDB doc count, sparse_index_size, retrieval config
```

Every request gets an `X-Correlation-ID` header (generated or echoed from the client). All `structlog` entries include it automatically. Error responses include `correlation_id` in the JSON body.

---

## What's In Progress

See `TODO.md` for the remaining structural cleanup tasks on the `refactor` branch:
- Merging `core/exceptions.py` into `core/errors.py`
- Moving signal services into `services/signals/` subdirectory
- Extracting I/O helpers out of `pipelines/board_analyzer.py`
- Migrating API response types from `models/evidence.py` into `schemas/evidence.py` (schema file already created)
