# PE Org-AI-R Platform — Pre-CS5 Refactor Plan

> **Purpose:** Sequential refactoring guide to stabilize the CS1–CS4 foundation before implementing CS5 (MCP + LangGraph agents).  
> **Usage:** Claude Code reads this file and executes one phase at a time. After each phase, the developer manually commits changes and marks the phase `[COMPLETE]`.  
> **Estimated Total Effort:** 7–8 working days across 5 phases, 15 sub-tasks.

---

## How to Use This File

1. Open this file in your repo root.
2. Tell Claude Code: _"Read REFACTOR_PLAN.md and execute Phase 1A."_
3. After Claude Code finishes, run the validation steps listed.
4. If validation passes, `git add . && git commit -m "refactor: Phase 1A — dependency lifecycle"`.
5. Edit this file: change `[ ]` to `[x]` for that phase.
6. Proceed to the next phase.

**Rules:**
- Execute phases in order (1A → 1B → 1C → 2A → ... → 5B).
- Do NOT skip phases. Later phases depend on earlier ones.
- Each phase must leave the app in a runnable state (`uvicorn app.main:app --reload` starts without errors).
- Each phase includes a "Files Changed" list, "Detailed Steps", and "Validation" checklist.

---

## Progress Tracker

### Phase 1: Stabilize the Runtime
- [x] **1A** — Dependency Injection Lifecycle
- [x] **1B** — BM25 Index Persistence Across Restarts
- [x] **1C** — Background Task State to Redis

### Phase 2: Standardize the Service Contract Layer
- [x] **2A** — Reconcile Data Shapes (Shared Schemas)
- [x] **2B** — Unified Error Hierarchy with HTTP Translation
- [x] **2C** — Base API Client with Retry Logic

### Phase 3: Scoring Pipeline Integrity
- [x] **3A** — Scoring Run Tracking
- [x] **3B** — CS2 Prerequisite Checks Before Scoring

### Phase 4: Configuration Consolidation
- [x] **4A** — Single Pydantic Settings Class
- [x] **4B** — Expose Configuration via API + MCP-ready Resource

### Phase 5: Observability Foundation
- [x] **5A** — Request Correlation IDs
- [x] **5B** — Structured Logging Baseline

---

## Phase 1: Stabilize the Runtime

> **Goal:** Ensure connections are managed, indexes survive restarts, and background task state persists. These are the crashes and silent failures that will derail CS5 agent workflows.

---

### Phase 1A: Dependency Injection Lifecycle `[ ]`

**Problem:**  
`app/core/dependencies.py` uses `dict.setdefault` to create singletons. Repositories open Snowflake connections at construction time. There is no shutdown cleanup — connections leak. CS5 will add MCP server + Streamlit + LangGraph processes, all creating duplicate connections.

**Files to Change:**
- `app/core/dependencies.py` — Rewrite entirely
- `app/main.py` — Update `lifespan()` to manage resources
- All files that import from `app/core/dependencies.py` — Update import paths (no logic changes, just wiring)

**Files to Create:**
- `app/core/lifespan.py` — New module for startup/shutdown lifecycle

**Detailed Steps:**

1. **Create `app/core/lifespan.py`:**

```python
"""
Application lifecycle manager.
Creates all shared resources at startup, tears them down at shutdown.
All singletons live on app.state — no module-level caches.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
import structlog

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: Initialize all shared resources and store on app.state.
    Shutdown: Close all connections cleanly.
    """
    logger.info("lifespan_startup_begin")

    # --- Repositories (Snowflake connections) ---
    from app.repositories.company_repository import CompanyRepository
    from app.repositories.industry_repository import IndustryRepository
    from app.repositories.document_repository import DocumentRepository
    from app.repositories.signal_repository import SignalRepository
    from app.repositories.scoring_repository import ScoringRepository
    from app.repositories.composite_scoring_repository import CompositeScoringRepository
    from app.repositories.dimension_score_repository import DimensionScoreRepository
    from app.repositories.assessment_repository import AssessmentRepository
    from app.repositories.health_repository import HealthRepository

    app.state.company_repo = CompanyRepository()
    app.state.industry_repo = IndustryRepository()
    app.state.document_repo = DocumentRepository()
    app.state.signal_repo = SignalRepository()
    app.state.scoring_repo = ScoringRepository()
    app.state.composite_scoring_repo = CompositeScoringRepository()
    app.state.dimension_score_repo = DimensionScoreRepository()
    app.state.assessment_repo = AssessmentRepository()
    app.state.health_repo = HealthRepository()

    # --- Services ---
    from app.services.s3_storage import S3StorageService
    from app.services.cache import create_redis_client
    from app.services.search.vector_store import VectorStore
    from app.services.retrieval.hybrid import HybridRetriever
    from app.services.retrieval.dimension_mapper import DimensionMapper
    from app.services.llm.router import ModelRouter
    from app.services.justification.generator import JustificationGenerator
    from app.services.workflows.ic_prep import ICPrepWorkflow
    from app.services.collection.analyst_notes import AnalystNotesCollector
    from app.services.integration.cs2_client import CS2Client

    app.state.s3 = S3StorageService()
    app.state.redis = await create_redis_client()
    app.state.vector_store = VectorStore()
    app.state.dimension_mapper = DimensionMapper()
    app.state.model_router = ModelRouter()

    app.state.hybrid_retriever = HybridRetriever(
        vector_store=app.state.vector_store
    )
    app.state.justification_generator = JustificationGenerator(
        retriever=app.state.hybrid_retriever,
        model_router=app.state.model_router,
    )
    app.state.cs2_client = CS2Client()
    app.state.ic_prep_workflow = ICPrepWorkflow(
        justification_generator=app.state.justification_generator,
        model_router=app.state.model_router,
    )
    app.state.analyst_notes_collector = AnalystNotesCollector(
        retriever=app.state.hybrid_retriever,
    )

    logger.info("lifespan_startup_complete")

    yield  # App runs here

    # --- Shutdown ---
    logger.info("lifespan_shutdown_begin")

    if hasattr(app.state, 'redis') and app.state.redis:
        await app.state.redis.close()
    if hasattr(app.state, 'cs2_client'):
        await app.state.cs2_client.close()

    # Close Snowflake connections on all repositories
    for attr_name in dir(app.state):
        obj = getattr(app.state, attr_name, None)
        if hasattr(obj, 'close') and callable(obj.close):
            try:
                result = obj.close()
                if hasattr(result, '__await__'):
                    await result
            except Exception as e:
                logger.warning("shutdown_close_error", resource=attr_name, error=str(e))

    logger.info("lifespan_shutdown_complete")
```

> **IMPORTANT NOTE TO CLAUDE CODE:** The above is a *template*. You must inspect the actual constructor signatures of each repository and service class in the codebase before writing the final version. Repositories may require Snowflake connection parameters. Services may require injected dependencies. Read each `__init__` method and wire accordingly. Do not blindly copy this template — adapt it to the actual code.

2. **Rewrite `app/core/dependencies.py`:**

Remove the `_get_or_create` dict pattern. Replace every `get_*` function with a `Depends()` provider that reads from `request.app.state`:

```python
"""
Dependency injection providers.
All singletons live on app.state (created in lifespan.py).
These functions extract them for FastAPI's Depends() system.
"""
from fastapi import Request

def get_company_repository(request: Request):
    return request.app.state.company_repo

def get_industry_repository(request: Request):
    return request.app.state.industry_repo

def get_document_repository(request: Request):
    return request.app.state.document_repo

def get_signal_repository(request: Request):
    return request.app.state.signal_repo

def get_scoring_repository(request: Request):
    return request.app.state.scoring_repo

def get_composite_scoring_repository(request: Request):
    return request.app.state.composite_scoring_repo

def get_dimension_score_repository(request: Request):
    return request.app.state.dimension_score_repo

def get_assessment_repository(request: Request):
    return request.app.state.assessment_repo

def get_health_repository(request: Request):
    return request.app.state.health_repo

def get_vector_store(request: Request):
    return request.app.state.vector_store

def get_hybrid_retriever(request: Request):
    return request.app.state.hybrid_retriever

def get_model_router(request: Request):
    return request.app.state.model_router

def get_cs2_client(request: Request):
    return request.app.state.cs2_client

def get_ic_prep_workflow(request: Request):
    return request.app.state.ic_prep_workflow

def get_analyst_notes_collector(request: Request):
    return request.app.state.analyst_notes_collector

def get_justification_generator(request: Request):
    return request.app.state.justification_generator

def get_s3_service(request: Request):
    return request.app.state.s3

def get_redis(request: Request):
    return request.app.state.redis

def get_dimension_mapper(request: Request):
    return request.app.state.dimension_mapper
```

> **NOTE TO CLAUDE CODE:** You must check every existing function in the current `dependencies.py` file and ensure you have a replacement for each one. Search the entire `app/routers/` directory for `Depends(get_` calls to find every dependency that needs a provider. Do not remove any provider that is actively used.

3. **Update `app/main.py`:**

- Import `lifespan` from `app.core.lifespan`
- Pass it to the FastAPI constructor: `app = FastAPI(lifespan=lifespan, ...)`
- Remove any singleton creation or connection setup currently in `main.py`

4. **Update all routers:**

Every router file that uses `Depends(get_something)` should continue to work without changes IF the function signatures in `dependencies.py` match the old ones. However, verify:
- The old functions took no parameters (they used module-level state).
- The new functions take `request: Request`.
- FastAPI's `Depends()` automatically injects `Request`, so `Depends(get_company_repository)` still works in router function signatures — no router changes needed.

**BUT:** If any router imports the singleton directly (e.g., `from app.core.dependencies import _company_repository`), those imports must be changed to use `Depends()`.

> **NOTE TO CLAUDE CODE:** Grep for any direct imports of singleton instances from `dependencies.py` across the codebase. Replace them with `Depends()` injection. Search patterns: `from app.core.dependencies import _`, `from app.core.dependencies import get_` followed by direct calls (not inside `Depends()`).

**Validation:**
- [ ] `uvicorn app.main:app --reload` starts without import errors
- [ ] `GET /healthz` returns `200`
- [ ] `GET /health` checks Snowflake/Redis/S3 successfully
- [ ] `GET /api/v1/companies/all` returns data (confirms Snowflake singleton works)
- [ ] Stop the server with Ctrl+C — no connection leak warnings in logs
- [ ] Grep for `_get_or_create` — should return zero results
- [ ] Grep for `dict.setdefault` in `dependencies.py` — should return zero results

---

### Phase 1B: BM25 Index Persistence Across Restarts `[ ]`

**Problem:**  
`HybridRetriever` stores BM25 corpus in memory (`self._corpus`, `self._doc_ids`, `self._metadata`). Restart = empty sparse index. Hybrid retrieval silently becomes dense-only. CS5's evidence agent generates weak justifications without knowing why.

**Files to Change:**
- `app/services/retrieval/hybrid.py` — Add `rebuild_sparse_index_from_chroma()` method
- `app/core/lifespan.py` — Call rebuild on startup after `HybridRetriever` is created

**Detailed Steps:**

1. **Add rebuild method to `HybridRetriever`:**

Open `app/services/retrieval/hybrid.py`. Add a method that reads all documents from ChromaDB and rebuilds the BM25 index:

```python
def rebuild_sparse_index_from_chroma(self) -> int:
    """
    Rebuild BM25 sparse index from ChromaDB persistent store.
    Call this on startup to restore hybrid retrieval capability.
    Returns the number of documents indexed.
    """
    try:
        all_docs = self.collection.get(
            include=["documents", "metadatas"]
        )

        if not all_docs or not all_docs.get("ids"):
            logger.info("bm25_rebuild_skipped", reason="no documents in ChromaDB")
            return 0

        self._doc_ids = all_docs["ids"]
        self._corpus = all_docs["documents"]
        self._metadata = all_docs.get("metadatas", [{}] * len(self._doc_ids))

        tokenized = [doc.lower().split() for doc in self._corpus]
        self._bm25 = BM25Okapi(tokenized)

        logger.info("bm25_rebuild_complete", doc_count=len(self._doc_ids))
        return len(self._doc_ids)

    except Exception as e:
        logger.error("bm25_rebuild_failed", error=str(e))
        return 0
```

> **NOTE TO CLAUDE CODE:** Check the actual attribute names in the existing `HybridRetriever.__init__`. The CLAUDE.md references `self._bm25`, `self._corpus`, `self._doc_ids`, `self._metadata` — verify these match the actual code. Also verify that `self.collection` is the ChromaDB collection object and that `.get()` is the correct method (it is for ChromaDB's `Collection`).

2. **Call rebuild in `app/core/lifespan.py`:**

After creating the `HybridRetriever`, add:

```python
# Rebuild BM25 from persistent ChromaDB data
doc_count = app.state.hybrid_retriever.rebuild_sparse_index_from_chroma()
logger.info("startup_bm25_ready", doc_count=doc_count)
```

3. **Add a health indicator for BM25 status:**

In `HybridRetriever`, add a property:

```python
@property
def sparse_index_size(self) -> int:
    """Number of documents in the BM25 sparse index."""
    return len(self._doc_ids) if self._doc_ids else 0
```

Update `GET /api/v1/rag/diagnostics` in `app/routers/rag.py` to include `sparse_index_size` in the response so you can verify BM25 is populated.

**Validation:**
- [ ] Start the server, check logs for `bm25_rebuild_complete` with a nonzero `doc_count` (if ChromaDB has data)
- [ ] `GET /api/v1/rag/diagnostics` includes `sparse_index_size` field
- [ ] If you have indexed data: restart the server, immediately call `POST /api/v1/rag/search` with a query — results should include `retrieval_method: "hybrid"` (not just `"dense"`)
- [ ] If ChromaDB is empty: logs should show `bm25_rebuild_skipped` — no crash

---

### Phase 1C: Background Task State to Redis `[ ]`

**Problem:**  
`_task_store` is an in-memory dict. Server restart loses all task state. CS5's agents trigger signal collection and need to check completion status.

**Files to Change:**
- `app/routers/signals.py` — Replace `_task_store` dict with Redis-backed storage
- `app/services/cache.py` — Add task-specific Redis helpers (if not placing them in a new file)

**Files to Create (optional):**
- `app/services/task_store.py` — Dedicated task state manager using Redis

**Detailed Steps:**

1. **Create `app/services/task_store.py`:**

```python
"""
Redis-backed task state store.
Replaces the in-memory _task_store dict in signals.py.
"""
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()

TASK_KEY_PREFIX = "task:"
TASK_TTL_SECONDS = 86400  # 24 hours


class TaskStore:
    """Persistent task state backed by Redis."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def create_task(self, task_id: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create a new task record with status 'queued'."""
        task = {
            "task_id": task_id,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
            "error": None,
            "progress": {},
        }
        key = f"{TASK_KEY_PREFIX}{task_id}"
        await self.redis.set(key, json.dumps(task), ex=TASK_TTL_SECONDS)
        return task

    async def update_status(
        self,
        task_id: str,
        status: str,
        progress: Dict[str, Any] = None,
        error: str = None,
    ) -> Optional[Dict[str, Any]]:
        """Update task status. Returns updated task or None if not found."""
        key = f"{TASK_KEY_PREFIX}{task_id}"
        raw = await self.redis.get(key)
        if raw is None:
            logger.warning("task_not_found", task_id=task_id)
            return None

        task = json.loads(raw)
        task["status"] = status
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        if progress:
            task["progress"] = progress
        if error:
            task["error"] = error

        await self.redis.set(key, json.dumps(task), ex=TASK_TTL_SECONDS)
        return task

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve task state. Returns None if expired or not found."""
        key = f"{TASK_KEY_PREFIX}{task_id}"
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
```

2. **Wire `TaskStore` into `app/core/lifespan.py`:**

After creating the Redis client:

```python
from app.services.task_store import TaskStore
app.state.task_store = TaskStore(redis_client=app.state.redis)
```

3. **Add dependency provider in `app/core/dependencies.py`:**

```python
def get_task_store(request: Request):
    return request.app.state.task_store
```

4. **Refactor `app/routers/signals.py`:**

- Remove the module-level `_task_store = {}` dict
- Inject `TaskStore` via `Depends(get_task_store)`
- Replace all `_task_store[task_id] = {...}` writes with `await task_store.create_task(task_id, ...)`
- Replace all `_task_store[task_id]["status"] = "completed"` updates with `await task_store.update_status(task_id, "completed", ...)`
- Replace all `_task_store.get(task_id)` reads with `await task_store.get_task(task_id)`
- If there is a `GET /api/v1/signals/task/{task_id}` endpoint (or equivalent), update it to use `TaskStore.get_task()`

> **NOTE TO CLAUDE CODE:** Search `app/routers/signals.py` for ALL references to `_task_store`. There may be a task status endpoint. Map every access pattern before making changes.

**Validation:**
- [ ] `POST /api/v1/signals/collect` returns a `task_id`
- [ ] Restart the server
- [ ] The task status is still queryable (check Redis with `redis-cli GET task:<task_id>`)
- [ ] No references to `_task_store` remain in the codebase (grep for it)
- [ ] `uvicorn app.main:app --reload` starts without errors

---

## Phase 2: Standardize the Service Contract Layer

> **Goal:** Ensure all APIs return consistent shapes, errors are typed and actionable, and HTTP client calls have retry logic. CS5's MCP tools and LangGraph agents depend on predictable inputs and outputs.

---

### Phase 2A: Reconcile Data Shapes (Shared Schemas) `[ ]`

**Problem:**  
CS5 client interfaces expect different field names than the actual API returns (e.g., `revenue_mm` vs `revenue_millions`, `company_id` vs `id`). Every mismatch becomes a runtime `KeyError` in MCP tool handlers.

**Files to Create:**
- `app/schemas/__init__.py`
- `app/schemas/company.py` — Canonical company schema
- `app/schemas/evidence.py` — Canonical evidence schema
- `app/schemas/scoring.py` — Canonical scoring/assessment schema
- `app/schemas/dimensions.py` — Canonical dimension definitions

**Files to Change:**
- `app/routers/companies.py` — Use canonical schemas for response models (or add aliases)
- `app/routers/scoring.py` — Ensure response includes fields CS5 expects
- `app/routers/orgair_scoring.py` — Ensure assessment response matches CS5's `CompanyAssessment` shape
- Any other router that returns data consumed by CS5

**Detailed Steps:**

1. **Create `app/schemas/dimensions.py`:**

This is the single source of truth for dimension names. Currently dimensions appear as strings in various places with inconsistent formatting.

```python
"""Canonical dimension definitions used across CS1-CS5."""
from enum import Enum

class Dimension(str, Enum):
    DATA_INFRASTRUCTURE = "data_infrastructure"
    AI_GOVERNANCE = "ai_governance"
    TECHNOLOGY_STACK = "technology_stack"
    TALENT = "talent"
    LEADERSHIP = "leadership"
    USE_CASE_PORTFOLIO = "use_case_portfolio"
    CULTURE = "culture"
```

> **NOTE TO CLAUDE CODE:** Check `app/models/enumerations.py` for the existing `DIMENSION_ALIAS_MAP` and any existing dimension enums. The new canonical enum should be consistent with these. If an enum already exists, refactor usages to import from the new canonical location, and have the old location re-export from the new one for backward compatibility.

2. **Create `app/schemas/company.py`:**

```python
"""Canonical company schema — single source of truth for field names."""
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime

class CompanyBase(BaseModel):
    """Fields shared between create, read, and CS5 client views."""
    name: str
    ticker: Optional[str] = None
    sector: Optional[str] = None
    sub_sector: Optional[str] = None

class CompanyRead(CompanyBase):
    """Full company read model — returned by API."""
    id: UUID
    industry_id: UUID
    position_factor: float = 0.0
    market_cap_percentile: Optional[float] = Field(None, alias="market_cap_percentile")
    revenue_millions: Optional[float] = None
    employee_count: Optional[int] = None
    fiscal_year_end: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    # CS5 compatibility properties
    @property
    def company_id(self) -> str:
        """CS5 expects 'company_id' as string."""
        return str(self.id)

    @property
    def revenue_mm(self) -> Optional[float]:
        """CS5 expects 'revenue_mm'."""
        return self.revenue_millions
```

> **NOTE TO CLAUDE CODE:** Inspect the actual `CompanyResponse` model currently used in `app/routers/companies.py`. The new canonical schema should be a superset — it must include every field the existing response returns PLUS the aliases CS5 needs. Do not remove existing fields. If the existing response model uses `id` (UUID), keep that as the primary field and add `company_id` as a computed property or serialization alias.

3. **Create `app/schemas/scoring.py`:**

```python
"""Canonical scoring schemas — assessment, dimension scores."""
from pydantic import BaseModel
from typing import Dict, Tuple, Optional

class DimensionScoreRead(BaseModel):
    dimension: str
    score: float
    level: int
    evidence_count: int = 0
    confidence_interval: Optional[Tuple[float, float]] = None

class CompanyAssessmentRead(BaseModel):
    """
    Full assessment — matches what CS5's CompanyAssessment dataclass expects.
    """
    company_id: str
    org_air_score: float
    vr_score: float
    hr_score: float
    synergy_score: float
    dimension_scores: Dict[str, DimensionScoreRead]
    confidence_interval: Tuple[float, float]
    talent_concentration: float = 0.0
    position_factor: float = 0.0
    evidence_count: int = 0
    assessment_date: Optional[str] = None
```

> **NOTE TO CLAUDE CODE:** Inspect the actual response from `POST /api/v1/scoring/orgair/portfolio`. Map every field it returns to the canonical schema above. Add any missing fields. The goal is that CS5's `cs3_client.get_assessment()` can deserialize the API response into this schema without any field remapping.

4. **Add a `/api/v1/assessments/{ticker}` convenience endpoint:**

CS5's MCP tool mapping table references `GET /v2/assessments/{id}`. Instead of creating a v2, add a thin endpoint that aggregates the current scoring data into the `CompanyAssessmentRead` shape:

> **NOTE TO CLAUDE CODE:** Check if this endpoint already exists in any router. If not, add it to `app/routers/orgair_scoring.py` (or a new `app/routers/assessments.py`). It should read from the same Snowflake tables that the existing scoring endpoints use and return the `CompanyAssessmentRead` schema. This is a READ-only endpoint — no new computation.

5. **Create `app/schemas/evidence.py`:**

```python
"""Canonical evidence schemas."""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class EvidenceRead(BaseModel):
    evidence_id: str
    company_id: str
    source_type: str
    signal_category: str
    content: str
    confidence: float
    fiscal_year: Optional[int] = None
    source_url: Optional[str] = None
    extracted_at: Optional[datetime] = None
    indexed_in_cs4: bool = False
```

**Validation:**
- [ ] `app/schemas/` directory exists with `__init__.py`, `company.py`, `evidence.py`, `scoring.py`, `dimensions.py`
- [ ] Existing API responses are unchanged (backward compatible)
- [ ] New `/api/v1/assessments/{ticker}` endpoint returns `CompanyAssessmentRead` shape
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] Existing tests (if any) still pass

---

### Phase 2B: Unified Error Hierarchy with HTTP Translation `[ ]`

**Problem:**  
Services mix domain exceptions with direct `HTTPException` raises. CS5's MCP `call_tool()` handler catches bare `Exception` and returns `f"Error: {str(e)}"` — agents can't distinguish "not found" from "timeout" from "pipeline incomplete."

**Files to Create:**
- `app/core/errors.py` — New unified error hierarchy (keep old `exceptions.py` temporarily for backward compat)

**Files to Change:**
- `app/core/exceptions.py` — Deprecate `raise_error()`, keep validation handler
- `app/main.py` — Register new exception handlers for `PlatformError` subclasses
- `app/services/` — Gradually replace `raise_error()` calls with domain exceptions (focus on services used by CS5 MCP tools)
- `app/routers/` — Remove try/except blocks that catch domain errors and re-raise as HTTP (the middleware handles it now)

**Detailed Steps:**

1. **Create `app/core/errors.py`:**

```python
"""
Unified platform error hierarchy.
Services raise these. The FastAPI exception handler translates to HTTP responses.
CS5 MCP tools can inspect error_code to make routing decisions.
"""
from typing import Optional, Dict, Any


class PlatformError(Exception):
    """Base error for all domain/business logic errors."""

    def __init__(
        self,
        message: str,
        error_code: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class NotFoundError(PlatformError):
    """Resource not found. HTTP 404."""

    def __init__(self, resource: str, identifier: str, **kwargs):
        super().__init__(
            message=f"{resource} not found: {identifier}",
            error_code=f"{resource.upper()}_NOT_FOUND",
            details={"resource": resource, "identifier": identifier, **kwargs},
        )


class ConflictError(PlatformError):
    """Duplicate or conflicting resource. HTTP 409."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message=message, error_code="CONFLICT", details=kwargs)


class ValidationError(PlatformError):
    """Business rule validation failure. HTTP 422."""

    def __init__(self, message: str, field: str = None, **kwargs):
        details = kwargs
        if field:
            details["field"] = field
        super().__init__(message=message, error_code="VALIDATION_ERROR", details=details)


class ExternalServiceError(PlatformError):
    """External service (Snowflake, Groq, SEC EDGAR, etc.) failure. HTTP 502."""

    def __init__(self, service: str, message: str = None, **kwargs):
        super().__init__(
            message=message or f"External service failed: {service}",
            error_code="EXTERNAL_SERVICE_ERROR",
            details={"service": service, **kwargs},
        )


class PipelineIncompleteError(PlatformError):
    """Required upstream pipeline step has not been run. HTTP 424."""

    def __init__(self, ticker: str, missing_steps: list, **kwargs):
        super().__init__(
            message=f"Pipeline incomplete for {ticker}: missing {', '.join(missing_steps)}",
            error_code="PIPELINE_INCOMPLETE",
            details={"ticker": ticker, "missing_steps": missing_steps, **kwargs},
        )


class ScoringInProgressError(PlatformError):
    """Scoring is currently running for this company. HTTP 409."""

    def __init__(self, ticker: str, run_id: str = None, **kwargs):
        super().__init__(
            message=f"Scoring already in progress for {ticker}",
            error_code="SCORING_IN_PROGRESS",
            details={"ticker": ticker, "run_id": run_id, **kwargs},
        )
```

2. **Register exception handlers in `app/main.py`:**

```python
from app.core.errors import (
    PlatformError, NotFoundError, ConflictError,
    ValidationError, ExternalServiceError,
    PipelineIncompleteError, ScoringInProgressError,
)
from fastapi.responses import JSONResponse
from datetime import datetime, timezone

# Map error types to HTTP status codes
ERROR_STATUS_MAP = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    ExternalServiceError: 502,
    PipelineIncompleteError: 424,
    ScoringInProgressError: 409,
}

@app.exception_handler(PlatformError)
async def platform_error_handler(request, exc: PlatformError):
    status_code = ERROR_STATUS_MAP.get(type(exc), 500)
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
```

> **NOTE TO CLAUDE CODE:** Keep the existing `RequestValidationError` handler and the global `Exception` 500 handler. The new `PlatformError` handler sits alongside them — FastAPI routes to the most specific handler.

3. **Migrate services used by CS5's MCP tools:**

Focus on the services that CS5's 6 MCP tools call:
- `calculate_org_air_score` → `CS3Client.get_assessment()` → scoring repos → replace any `raise_error(404, ...)` with `raise NotFoundError("company", ticker)`
- `get_company_evidence` → CS2Client → evidence repos
- `generate_justification` → JustificationGenerator → retriever + scoring
- `run_gap_analysis` → composite scoring
- `get_portfolio_summary` → PortfolioDataService → CS1 repos

> **NOTE TO CLAUDE CODE:** Do a codebase-wide search for `raise_error(` calls. For each one, determine if it's in a service (should use domain errors) or a router (can remain as-is for now but ideally should let the middleware handle it). Migrate service-layer `raise_error` calls first. Router-layer can be migrated incrementally.

4. **Backward compatibility:**

Keep `app/core/exceptions.py` with `raise_error()` intact but add a deprecation comment. This avoids breaking any code paths not yet migrated. Remove it fully after all services are migrated.

**Validation:**
- [ ] `app/core/errors.py` exists with all error classes
- [ ] `PlatformError` handler registered in `main.py`
- [ ] A service that previously used `raise_error(404, "COMPANY_NOT_FOUND", ...)` now raises `NotFoundError("company", ticker)` and the API still returns a 404 JSON response
- [ ] Existing tests still pass
- [ ] `uvicorn app.main:app --reload` starts without errors

---

### Phase 2C: Base API Client with Retry Logic `[ ]`

**Problem:**  
CS1–CS4 client classes each implement their own `httpx.AsyncClient` setup with no retry logic, no structured error handling, and duplicated `close()` methods. CS5 adds `MCPToolCaller` as yet another HTTP client wrapper. A transient Snowflake timeout at the bottom of the call chain becomes a permanent agent failure.

**Files to Create:**
- `app/clients/__init__.py`
- `app/clients/base.py` — Base HTTP client with retry, error parsing, lifecycle management

**Files to Change:**
- `app/services/integration/cs2_client.py` — Inherit from `BaseAPIClient`
- Any other internal HTTP client (check if `groq_enrichment.py` or LLM router uses raw httpx)

**Detailed Steps:**

1. **Create `app/clients/base.py`:**

```python
"""
Base API client with retry logic, structured error handling, and connection lifecycle.
All internal HTTP clients (CS1-CS4, MCP) should inherit from this.
"""
import asyncio
from typing import Optional, Dict, Any
import httpx
import structlog
from app.core.errors import ExternalServiceError, NotFoundError

logger = structlog.get_logger()


class BaseAPIClient:
    """
    Async HTTP client with exponential backoff retry and structured errors.
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """
        Make an HTTP request with retry logic.
        
        - 404 → NotFoundError (no retry)
        - 4xx → PlatformError (no retry)
        - 5xx / connection errors → retry with exponential backoff
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                response = await self.client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    **kwargs,
                )

                if response.status_code == 404:
                    raise NotFoundError(
                        resource=self.service_name,
                        identifier=path,
                    )

                if 400 <= response.status_code < 500:
                    # Client errors — do not retry
                    body = response.json() if response.content else {}
                    raise ExternalServiceError(
                        service=self.service_name,
                        message=body.get("message", f"HTTP {response.status_code}"),
                        status_code=response.status_code,
                        response_body=body,
                    )

                if response.status_code >= 500:
                    # Server errors — retry
                    last_exception = ExternalServiceError(
                        service=self.service_name,
                        message=f"HTTP {response.status_code} from {self.service_name}",
                        status_code=response.status_code,
                    )
                    await self._backoff(attempt)
                    continue

                return response.json()

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_exception = ExternalServiceError(
                    service=self.service_name,
                    message=f"Connection error: {str(e)}",
                )
                logger.warning(
                    "client_retry",
                    service=self.service_name,
                    path=path,
                    attempt=attempt + 1,
                    error=str(e),
                )
                await self._backoff(attempt)

        # All retries exhausted
        raise last_exception

    async def _backoff(self, attempt: int):
        delay = self.retry_backoff_base * (2 ** attempt)
        await asyncio.sleep(delay)

    async def get(self, path: str, params: Dict[str, Any] = None, **kwargs) -> Any:
        return await self._request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json_body: Dict[str, Any] = None, **kwargs) -> Any:
        return await self._request("POST", path, json_body=json_body, **kwargs)

    async def close(self):
        await self.client.aclose()
```

2. **Migrate `app/services/integration/cs2_client.py` to use `BaseAPIClient`:**

> **NOTE TO CLAUDE CODE:** Inspect the actual `CS2Client` class. It currently creates its own `httpx.AsyncClient`. Refactor it to inherit from `BaseAPIClient` and replace raw `self.client.get(...)` calls with `self.get(path, params=...)`. Keep the response parsing logic (converting JSON to dataclass instances) in the subclass. The base class handles HTTP transport; the subclass handles domain serialization.

3. **Migrate any other internal HTTP clients:**

> **NOTE TO CLAUDE CODE:** Search for `httpx.AsyncClient` across the codebase. Any service that creates its own client should be evaluated for migration. Priority: clients that CS5's MCP tools will call. Lower priority: one-off clients like SEC EDGAR downloader (those can be migrated later).

**Validation:**
- [ ] `app/clients/base.py` exists
- [ ] `CS2Client` inherits from `BaseAPIClient`
- [ ] Simulate a transient failure: temporarily misconfigure a service URL, verify the client retries 3 times before raising `ExternalServiceError`
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] Existing API calls through CS2Client still work

---

## Phase 3: Scoring Pipeline Integrity

> **Goal:** Ensure scores are either fully computed or clearly marked as incomplete. CS5's `AssessmentHistoryService` captures snapshots — partial scores must never be snapshotted.

---

### Phase 3A: Scoring Run Tracking `[ ]`

**Problem:**  
The scoring pipeline writes dimension scores sequentially with no transaction boundary. A failure mid-pipeline leaves partial scores in Snowflake. CS5's history service would snapshot these as valid.

**Files to Create:**
- `app/models/scoring_run.py` — `ScoringRun` model (or add to existing models)

**Files to Change:**
- `app/repositories/scoring_repository.py` — Add `create_scoring_run()`, `update_scoring_run()`, `get_latest_scoring_run()` methods
- `app/services/scoring_service.py` — Wrap scoring pipeline in a run lifecycle (create → update → complete/fail)
- `app/routers/scoring.py` — `GET /scoring/{ticker}/dimensions` should check run status

**Detailed Steps:**

1. **Add `scoring_runs` table via Snowflake migration:**

> **NOTE TO CLAUDE CODE:** Check how the project handles Snowflake schema changes. There may be a migrations directory or SQL files. Create the table:

```sql
CREATE TABLE IF NOT EXISTS scoring_runs (
    run_id VARCHAR(36) PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',  -- running, completed, failed
    started_at TIMESTAMP_NTZ NOT NULL,
    completed_at TIMESTAMP_NTZ,
    dimensions_written INT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
```

If there's no formal migration system, add this SQL to a `migrations/` or `sql/` directory and document that it needs to be run manually.

2. **Add scoring run methods to `ScoringRepository`:**

```python
async def create_scoring_run(self, run_id: str, ticker: str) -> None:
    """Insert a new scoring run with status 'running'."""
    ...

async def complete_scoring_run(self, run_id: str, dimensions_written: int) -> None:
    """Mark run as completed."""
    ...

async def fail_scoring_run(self, run_id: str, error_message: str) -> None:
    """Mark run as failed."""
    ...

async def get_latest_scoring_run(self, ticker: str) -> Optional[Dict]:
    """Get the most recent scoring run for a ticker."""
    ...
```

> **NOTE TO CLAUDE CODE:** Inspect the actual `ScoringRepository` class. Match the SQL execution pattern used by other methods (parameterized queries, cursor handling, etc.).

3. **Wrap the scoring pipeline in `ScoringService`:**

In `app/services/scoring_service.py`, find the method that runs the full scoring pipeline for a single ticker (called by `POST /api/v1/scoring/{ticker}`). Wrap it:

```python
import uuid

async def score_company(self, ticker: str) -> ScoringResponse:
    run_id = str(uuid.uuid4())
    await self.scoring_repo.create_scoring_run(run_id, ticker)

    try:
        # ... existing scoring logic ...
        dimensions_written = len(dimension_scores)  # however many were written

        await self.scoring_repo.complete_scoring_run(run_id, dimensions_written)
        return ScoringResponse(status="success", ...)

    except Exception as e:
        await self.scoring_repo.fail_scoring_run(run_id, str(e))
        raise
```

4. **Guard dimension reads:**

In `GET /scoring/{ticker}/dimensions`, before returning scores, check:

```python
latest_run = await scoring_repo.get_latest_scoring_run(ticker)
if latest_run and latest_run["status"] == "failed":
    raise PipelineIncompleteError(
        ticker=ticker,
        missing_steps=["scoring (last run failed)"],
    )
if latest_run and latest_run["status"] == "running":
    raise ScoringInProgressError(ticker=ticker, run_id=latest_run["run_id"])
```

**Validation:**
- [ ] `scoring_runs` table exists in Snowflake
- [ ] `POST /api/v1/scoring/{ticker}` creates a scoring run record
- [ ] On success: run status = `completed`, `dimensions_written` = 7
- [ ] On failure (simulate by temporarily breaking a dependency): run status = `failed`, error message populated
- [ ] `GET /scoring/{ticker}/dimensions` after a failed run returns 424 (not stale scores)
- [ ] `uvicorn app.main:app --reload` starts without errors

---

### Phase 3B: CS2 Prerequisite Checks Before Scoring `[ ]`

**Problem:**  
`POST /api/v1/scoring/{ticker}` fails with a generic error if signal data is missing. CS5's agents need to know exactly what's missing to route to signal collection first.

**Files to Change:**
- `app/services/scoring_service.py` — Add prerequisite validation at the start of the scoring pipeline
- `app/repositories/signal_repository.py` — Add `get_signal_categories_for_ticker()` method (if not exists)

**Detailed Steps:**

1. **Add a prerequisite check method to `ScoringService`:**

```python
REQUIRED_SIGNAL_CATEGORIES = [
    "technology_hiring",
    "innovation_activity",
    "digital_presence",
    "leadership_signals",
]

async def check_scoring_prerequisites(self, ticker: str) -> Dict[str, Any]:
    """
    Check that all required CS2 data exists for scoring.
    Returns: {"ready": bool, "missing": [...], "available": [...]}
    """
    # Check signal categories
    available_categories = await self.signal_repo.get_signal_categories_for_ticker(ticker)
    missing_categories = [
        c for c in REQUIRED_SIGNAL_CATEGORIES
        if c not in available_categories
    ]

    # Check document chunks
    chunk_count = await self.document_repo.get_chunk_count_for_ticker(ticker)

    missing = []
    if missing_categories:
        missing.extend([f"signal:{c}" for c in missing_categories])
    if chunk_count == 0:
        missing.append("document_chunks")

    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "available_signals": list(available_categories),
        "chunk_count": chunk_count,
    }
```

2. **Call it at the start of the scoring pipeline:**

```python
async def score_company(self, ticker: str) -> ScoringResponse:
    prereqs = await self.check_scoring_prerequisites(ticker)
    if not prereqs["ready"]:
        raise PipelineIncompleteError(
            ticker=ticker,
            missing_steps=prereqs["missing"],
        )
    # ... proceed with scoring ...
```

3. **Optionally expose as an API endpoint:**

```
GET /api/v1/scoring/{ticker}/prerequisites
```

This is useful for CS5's supervisor agent to check before routing to the scoring agent.

> **NOTE TO CLAUDE CODE:** Check if `SignalRepository` already has a method that returns the distinct signal categories for a company. If not, add one. Similarly for document chunk counts — check `DocumentRepository`.

**Validation:**
- [ ] Scoring a company with no signal data returns 424 with `{"error_code": "PIPELINE_INCOMPLETE", "details": {"missing_steps": ["signal:technology_hiring", ...]}}`
- [ ] Scoring a company with complete data works as before
- [ ] `GET /api/v1/scoring/{ticker}/prerequisites` returns readiness status
- [ ] `uvicorn app.main:app --reload` starts without errors

---

## Phase 4: Configuration Consolidation

> **Goal:** One source of truth for all configuration. CS5's MCP resources serve parameters that must match what the scoring engine actually uses.

---

### Phase 4A: Single Pydantic Settings Class `[ ]`

**Problem:**  
Configuration is scattered: dimension weights in `app/models/dimension.py`, portfolio tickers in `app/config/company_mappings.py`, retrieval settings in `app/config/retrieval_settings.py`, scoring parameters hardcoded in multiple places, env vars for credentials.

**Files to Create:**
- `app/core/settings.py` — Pydantic `BaseSettings` class

**Files to Change:**
- `app/models/dimension.py` — Import weights from settings instead of hardcoding
- `app/config/company_mappings.py` — Import from settings
- `app/config/retrieval_settings.py` — Import from settings
- `app/core/lifespan.py` — Validate settings on startup

**Detailed Steps:**

1. **Create `app/core/settings.py`:**

```python
"""
Centralized application settings.
All configuration — credentials, scoring parameters, business rules — lives here.
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Dict, List, Optional
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and .env file.
    """

    # --- Infrastructure ---
    redis_url: str = "redis://localhost:6379"

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = ""
    snowflake_warehouse: str = ""
    snowflake_role: str = ""

    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket_name: str = ""
    aws_region: str = "us-east-1"

    groq_api_key: str = ""
    anthropic_api_key: str = ""

    chroma_persist_dir: str = "./chroma_data"
    chroma_collection_name: str = "pe_evidence"

    # --- Scoring Parameters (CS3 v2.0) ---
    scoring_alpha: float = 0.60
    scoring_beta: float = 0.12
    scoring_gamma_0: float = 0.0025
    scoring_gamma_1: float = 0.05
    scoring_gamma_2: float = 0.025
    scoring_gamma_3: float = 0.01

    # Org-AI-R weights
    orgair_vr_weight: float = 0.5
    orgair_hr_weight: float = 0.3
    orgair_pf_weight: float = 0.2

    # Position Factor weights
    pf_vr_component_weight: float = 0.6
    pf_mcap_component_weight: float = 0.4

    # H^R formula
    hr_adjustment_factor: float = 0.15

    # --- Dimension Weights (must sum to 1.0) ---
    dimension_weights: Dict[str, float] = {
        "data_infrastructure": 0.15,
        "ai_governance": 0.12,
        "technology_stack": 0.18,
        "talent": 0.18,
        "leadership": 0.12,
        "use_case_portfolio": 0.15,
        "culture": 0.10,
    }

    @field_validator("dimension_weights")
    @classmethod
    def validate_dimension_weights(cls, v):
        total = sum(v.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"Dimension weights must sum to 1.0, got {total}")
        return v

    # --- Portfolio ---
    cs3_portfolio_tickers: List[str] = ["NVDA", "JPM", "WMT", "GE", "DG"]

    # --- Retrieval ---
    max_context_chars: int = 4000
    default_top_k: int = 10
    dense_weight: float = 0.6
    sparse_weight: float = 0.4
    rrf_k: int = 60

    # --- Cache TTLs (seconds) ---
    ttl_company: int = 300  # 5 minutes
    ttl_dimension_weights: int = 3600  # 1 hour
    ttl_scoring: int = 600  # 10 minutes

    # --- LLM ---
    llm_daily_budget_usd: float = 50.0
    chat_model: str = "claude-haiku-4-5-20251001"
    justification_model: str = "claude-sonnet-4-20250514"

    # --- Sector Baselines ---
    sector_hr_baselines: Dict[str, float] = {
        "technology": 85.0,
        "financial_services": 78.0,
        "healthcare": 75.0,
        "manufacturing": 70.0,
        "retail": 65.0,
        "energy": 60.0,
        "business_services": 72.0,
        "consumer": 68.0,
    }

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore extra env vars
    }


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
```

> **NOTE TO CLAUDE CODE:** Inspect the following files to extract the actual current values before writing `settings.py`:
> - `app/models/dimension.py` → `DIMENSION_WEIGHTS` dict
> - `app/config/company_mappings.py` → `CS3_PORTFOLIO` list
> - `app/config/retrieval_settings.py` → `RETRIEVAL_SETTINGS` object and its fields
> - `app/services/llm/router.py` → `MODEL_ROUTING` dict, `DailyBudget.limit_usd`
> - `app/services/composite_scoring_service.py` → scoring formula constants (alpha, beta, gamma, weights)
> - `app/services/cache.py` → `TTL_COMPANY` and other TTL constants
> - `.env.example` → all environment variable names
>
> Use the actual values you find. Do not use the placeholder values in the template above.

2. **Update `app/models/dimension.py`:**

Replace the hardcoded `DIMENSION_WEIGHTS` dict:

```python
from app.core.settings import get_settings

def get_dimension_weights() -> dict:
    return get_settings().dimension_weights

# Keep backward-compatible module-level access
DIMENSION_WEIGHTS = get_settings().dimension_weights
```

3. **Update `app/config/company_mappings.py`:**

```python
from app.core.settings import get_settings

CS3_PORTFOLIO = get_settings().cs3_portfolio_tickers
```

4. **Validate settings on startup in `app/core/lifespan.py`:**

```python
from app.core.settings import get_settings

settings = get_settings()
logger.info("settings_loaded",
    portfolio_tickers=settings.cs3_portfolio_tickers,
    dimension_weight_sum=sum(settings.dimension_weights.values()),
    redis_url=settings.redis_url[:20] + "...",  # redact
)
```

**Validation:**
- [ ] `app/core/settings.py` exists
- [ ] `from app.core.settings import get_settings; s = get_settings()` works in a Python shell
- [ ] `s.dimension_weights` sums to 1.0
- [ ] `s.cs3_portfolio_tickers` matches the expected list
- [ ] Environment variables from `.env` override defaults
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] Startup logs show `settings_loaded` with correct values

---

### Phase 4B: Expose Configuration via API `[ ]`

**Problem:**  
CS5's MCP resource `orgair://parameters/v2.0` hardcodes scoring parameters. It needs to serve the actual parameters from the settings.

**Files to Create:**
- `app/routers/config.py` — Configuration API endpoints

**Files to Change:**
- `app/main.py` — Register the new config router

**Detailed Steps:**

1. **Create `app/routers/config.py`:**

```python
"""Configuration endpoints — serves scoring parameters for MCP resources."""
from fastapi import APIRouter
from app.core.settings import get_settings

router = APIRouter(prefix="/api/v1/config", tags=["Configuration"])


@router.get("/scoring-parameters")
async def get_scoring_parameters():
    """
    Returns current scoring parameters.
    CS5 MCP resource 'orgair://parameters/v2.0' should read from this.
    """
    s = get_settings()
    return {
        "version": "2.0",
        "alpha": s.scoring_alpha,
        "beta": s.scoring_beta,
        "gamma_0": s.scoring_gamma_0,
        "gamma_1": s.scoring_gamma_1,
        "gamma_2": s.scoring_gamma_2,
        "gamma_3": s.scoring_gamma_3,
        "orgair_weights": {
            "vr": s.orgair_vr_weight,
            "hr": s.orgair_hr_weight,
            "pf": s.orgair_pf_weight,
        },
        "position_factor_weights": {
            "vr_component": s.pf_vr_component_weight,
            "mcap_component": s.pf_mcap_component_weight,
        },
        "hr_adjustment_factor": s.hr_adjustment_factor,
    }


@router.get("/dimension-weights")
async def get_dimension_weights():
    """Returns current dimension weights."""
    s = get_settings()
    weights = s.dimension_weights
    return {
        "weights": weights,
        "total": sum(weights.values()),
        "is_valid": 0.999 <= sum(weights.values()) <= 1.001,
    }


@router.get("/sector-baselines")
async def get_sector_baselines():
    """Returns sector H^R baselines."""
    s = get_settings()
    return {
        "baselines": s.sector_hr_baselines,
    }


@router.get("/portfolio")
async def get_portfolio_config():
    """Returns portfolio tickers and retrieval settings."""
    s = get_settings()
    return {
        "cs3_portfolio_tickers": s.cs3_portfolio_tickers,
        "retrieval": {
            "max_context_chars": s.max_context_chars,
            "default_top_k": s.default_top_k,
            "dense_weight": s.dense_weight,
            "sparse_weight": s.sparse_weight,
            "rrf_k": s.rrf_k,
        },
    }
```

2. **Register in `app/main.py`:**

```python
from app.routers.config import router as config_router
app.include_router(config_router)
```

**Validation:**
- [ ] `GET /api/v1/config/scoring-parameters` returns the correct parameter values
- [ ] `GET /api/v1/config/dimension-weights` returns weights summing to 1.0
- [ ] `GET /api/v1/config/sector-baselines` returns baselines matching what the scoring engine uses
- [ ] `GET /api/v1/config/portfolio` returns the 5 CS3 tickers
- [ ] `uvicorn app.main:app --reload` starts without errors

---

## Phase 5: Observability Foundation

> **Goal:** Make multi-service debugging possible. CS5's agentic workflows make 10-15 sequential calls — without correlation IDs and structured logging, failures are untraceable.

---

### Phase 5A: Request Correlation IDs `[ ]`

**Problem:**  
No way to trace a request across services, background tasks, and LLM calls. CS5's due diligence workflow chains many calls — a failure deep in the stack is impossible to correlate.

**Files to Create:**
- `app/middleware/__init__.py`
- `app/middleware/correlation.py` — Correlation ID middleware

**Files to Change:**
- `app/main.py` — Register the middleware
- `app/core/errors.py` — Include correlation ID in error responses

**Detailed Steps:**

1. **Create `app/middleware/correlation.py`:**

```python
"""
Correlation ID middleware.
Generates a unique ID for each request and makes it available throughout the request lifecycle.
"""
import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable — accessible from any async code within the request
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

HEADER_NAME = "X-Correlation-ID"


def get_correlation_id() -> str:
    """Get the current request's correlation ID. Call from anywhere."""
    return correlation_id_var.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Assigns a correlation ID to each request.
    - If the client sends X-Correlation-ID, use it (for cross-service tracing).
    - Otherwise, generate a new UUID.
    - Sets the ID on the response header.
    - Stores it in a ContextVar for access in services/repos/logging.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use client-provided ID or generate new
        cid = request.headers.get(HEADER_NAME, str(uuid.uuid4()))
        correlation_id_var.set(cid)

        # Make it available on request state for Depends() access
        request.state.correlation_id = cid

        response = await call_next(request)
        response.headers[HEADER_NAME] = cid
        return response
```

2. **Register in `app/main.py`:**

```python
from app.middleware.correlation import CorrelationIdMiddleware

# Add BEFORE CORSMiddleware (middleware runs in reverse registration order in Starlette)
app.add_middleware(CorrelationIdMiddleware)
```

> **NOTE TO CLAUDE CODE:** Starlette processes middleware in LIFO order (last added = first to run). Correlation ID middleware should run FIRST (before CORS), so add it AFTER CORSMiddleware in the code. Verify this is correct by checking the existing middleware registration order in `main.py`.

3. **Include correlation ID in error responses:**

Update the `platform_error_handler` in `app/main.py`:

```python
from app.middleware.correlation import get_correlation_id

@app.exception_handler(PlatformError)
async def platform_error_handler(request, exc: PlatformError):
    status_code = ERROR_STATUS_MAP.get(type(exc), 500)
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": get_correlation_id(),
        },
    )
```

Also update the global 500 handler to include `correlation_id`.

4. **Add a dependency provider for router access:**

In `app/core/dependencies.py`:

```python
from app.middleware.correlation import get_correlation_id

def get_correlation_id_dep(request: Request) -> str:
    return getattr(request.state, "correlation_id", get_correlation_id())
```

**Validation:**
- [ ] Any API call returns `X-Correlation-ID` in response headers
- [ ] Sending `X-Correlation-ID: test-123` as a request header → response echoes `test-123`
- [ ] Error responses include `correlation_id` field
- [ ] `uvicorn app.main:app --reload` starts without errors

---

### Phase 5B: Structured Logging Baseline `[ ]`

**Problem:**  
Logging is inconsistent. Some modules use `structlog`, others use plain `print` or `logging`. No request context (correlation ID, ticker, duration) in log entries. CS5's Prometheus metrics (Task 10.6) need clean entry/exit points to instrument.

**Files to Create:**
- `app/core/logging_config.py` — Structlog configuration

**Files to Change:**
- `app/core/lifespan.py` — Initialize logging on startup
- `app/services/scoring_service.py` — Add structured logging to scoring pipeline (example migration)
- `app/services/llm/router.py` — Add structured logging to LLM calls
- `app/routers/rag.py` — Add structured logging to RAG endpoints

**Detailed Steps:**

1. **Create `app/core/logging_config.py`:**

```python
"""
Structured logging configuration.
All log entries include correlation_id (if in a request context) and timestamps.
"""
import logging
import structlog
from app.middleware.correlation import get_correlation_id


def add_correlation_id(logger, method_name, event_dict):
    """Processor that adds correlation_id to every log entry."""
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(log_level: str = "INFO"):
    """
    Configure structlog for the application.
    Call once at startup.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            add_correlation_id,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer()  # Switch to JSONRenderer in production
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Redirect stdlib logging through structlog
    logging.basicConfig(
        format="%(message)s",
        level=logging.getLevelName(log_level),
    )
```

2. **Initialize in `app/core/lifespan.py`:**

At the very beginning of the lifespan startup:

```python
from app.core.logging_config import configure_logging
configure_logging()
```

3. **Add structured logging to the scoring pipeline:**

> **NOTE TO CLAUDE CODE:** Find the main scoring method in `app/services/scoring_service.py`. Add logging at key points:

```python
import time
import structlog

logger = structlog.get_logger()

async def score_company(self, ticker: str) -> ScoringResponse:
    start = time.perf_counter()
    logger.info("scoring_started", ticker=ticker)

    # ... prerequisite check ...
    logger.info("scoring_prerequisites_checked", ticker=ticker, ready=prereqs["ready"])

    # ... dimension scoring ...
    for dim_name, score in dimension_scores.items():
        logger.info("dimension_scored", ticker=ticker, dimension=dim_name, score=score)

    duration = time.perf_counter() - start
    logger.info("scoring_completed", ticker=ticker, duration_seconds=round(duration, 2))
```

4. **Add structured logging to LLM calls:**

> **NOTE TO CLAUDE CODE:** In `app/services/llm/router.py`, the `ModelRouter.complete()` method already has some logging. Ensure it logs: model used, task type, duration, token count (if available), and correlation_id (automatic via the processor).

5. **Add structured logging to RAG chatbot:**

> **NOTE TO CLAUDE CODE:** In the chatbot endpoint (`GET /api/v1/rag/chatbot/{ticker}`), add logging for: question received, dimension detected, docs retrieved, answer generated, guardrails checked. This creates the trace that CS5's `EvidenceAgent` calls will benefit from.

**Validation:**
- [ ] `app/core/logging_config.py` exists
- [ ] Startup logs show structured format with timestamps
- [ ] Making an API call produces log entries with `correlation_id`
- [ ] Scoring a company produces log entries for each step: `scoring_started`, `scoring_prerequisites_checked`, dimension scores, `scoring_completed`
- [ ] LLM calls log model name, task type, and duration
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] No remaining `print()` statements used for logging in service files (grep for `print(` in `app/services/`)

---

## Post-Refactor Checklist

Before starting CS5, verify:

- [ ] All 15 phases marked `[x]` above
- [ ] `uvicorn app.main:app --reload` starts cleanly
- [ ] `GET /healthz` → 200
- [ ] `GET /health` → 200 (all dependencies healthy)
- [ ] `GET /api/v1/companies/all` returns data
- [ ] `GET /api/v1/scoring/{ticker}/dimensions` returns scores (for a scored company)
- [ ] `GET /api/v1/rag/diagnostics` shows `sparse_index_size > 0` (if data indexed)
- [ ] `GET /api/v1/config/scoring-parameters` returns parameters
- [ ] `GET /api/v1/assessments/{ticker}` returns `CompanyAssessmentRead` shape
- [ ] Error responses include `error_code`, `correlation_id`, and `details`
- [ ] No remaining `_get_or_create` or `dict.setdefault` in dependencies
- [ ] No remaining `_task_store` in-memory dict in signals
- [ ] Structured logs with correlation IDs visible in terminal

**You are now ready for CS5.**

---

## Changes Made

Summary of actual file changes per refactor commit.

---

### Refactor 1 — Phase 1A: Dependency Injection Lifecycle
**Created:**
- `app/core/lifespan.py` — startup/shutdown lifecycle manager; all singletons created on `app.state`

**Modified:**
- `app/core/dependencies.py` — replaced `dict.setdefault` singleton cache with `Depends()` providers
- `app/main.py` — integrated `lifespan` context manager; stripped inline startup code

---

### Refactor 2 — Phase 1B + 1C: BM25 Persistence & Redis Task State
**Created:**
- `app/services/task_store.py` — `TaskStore`: Redis-backed background task state (`create_task`, `update_status`, `get_task`)

**Modified:**
- `app/core/dependencies.py` — added `get_task_store` provider
- `app/core/lifespan.py` — added BM25 rebuild from ChromaDB on startup
- `app/routers/rag.py` — wired sparse index rebuild diagnostic
- `app/routers/signals.py` — replaced in-memory `_task_store` dict with injected `TaskStore`
- `app/services/retrieval/hybrid.py` — added `rebuild_sparse_index_from_chroma()` and `sparse_index_size` property
- `app/services/search/vector_store.py` — added helpers for BM25 index sourcing

---

### Refactor 3 — Phase 2A: Canonical Schemas & Assessment Endpoint
**Created:**
- `app/schemas/__init__.py`
- `app/schemas/company.py` — `CompanyRead` with CS5 compatibility properties
- `app/schemas/dimensions.py` — canonical `Dimension` enum
- `app/schemas/evidence.py` — `EvidenceRead` schema
- `app/schemas/scoring.py` — `CompanyAssessmentRead`, `DimensionScoreRead`

**Modified:**
- `app/core/dependencies.py` — added `get_assessment_repository` provider
- `app/main.py` — registered assessment router
- `app/routers/companies.py` — updated response models to `CompanyRead`
- `app/routers/orgair_scoring.py` — added `GET /api/v1/assessments/{ticker}` endpoint

---

### Refactor 4 — Phase 2B: Unified Error Hierarchy
**Created:**
- `app/core/errors.py` — `PlatformError` base + subclasses: `NotFoundError` (404), `ConflictError` (409), `ValidationError` (422), `ExternalServiceError` (502), `PipelineIncompleteError` (424), `ScoringInProgressError` (409); `ERROR_STATUS_MAP`
- `REFACTOR_PLAN.md` — initial plan file committed
- `API_DOCS.md` — API documentation

**Modified:**
- `app/main.py` — registered `platform_error_handler` and `global_exception_handler` using `ERROR_STATUS_MAP`
- `app/prompts/rag_prompts.py`, `app/services/base_signal_service.py`, `app/services/document_collector.py`, `app/services/document_parsing_service.py`, `app/services/job_data_service.py`, `app/services/leadership_service.py`, `app/services/scoring_service.py` — updated to raise `PlatformError` subclasses

---

### Refactor 5 — Phase 2C: Base API Client with Retry Logic
**Created:**
- `app/clients/__init__.py`
- `app/clients/base.py` — `BaseAPIClient`: async HTTP with exponential backoff, raises `ExternalServiceError`/`NotFoundError`

**Modified:**
- `app/core/__init__.py` — re-exported error classes for convenience
- `app/core/exceptions.py` — stripped legacy helpers, kept `raise_error()` shim
- `app/routers/analyst_notes.py`, `app/routers/common.py`, `app/routers/companies.py`, `app/routers/documents.py`, `app/routers/orgair_scoring.py`, `app/routers/rag.py`, `app/routers/scoring.py`, `app/routers/signals.py` — updated error imports to `app/core/errors`
- `app/services/composite_scoring_service.py`, `app/services/document_chunking_service.py`, `app/services/document_parsing_service.py`, `app/services/groq_enrichment.py`, `app/services/integration/cs1_client.py`, `app/services/job_signal_service.py`, `app/services/llm/router.py` — updated error imports

---

### Refactor 6 — Phase 3A + 3B: Scoring Run Tracking & Prerequisites
**Created:**
- `app/database/scoring_runs_schema.sql` — Snowflake table DDL for `scoring_runs`

**Modified:**
- `app/core/exceptions.py` — added `ScoringInProgressError`, `PipelineIncompleteError`
- `app/repositories/document_repository.py` — added `get_chunk_count()` method
- `app/repositories/scoring_repository.py` — added `create_scoring_run()`, `complete_scoring_run()`, `fail_scoring_run()`, `get_latest_scoring_run()`
- `app/repositories/signal_repository.py` — added `get_signal_categories()` method
- `app/routers/scoring.py` — wrapped scoring endpoint with prerequisite guard and run lifecycle
- `app/services/scoring_service.py` — added `check_scoring_prerequisites()`, wrapped pipeline with run tracking

---

### Refactor 7 — Phase 4A: Single Pydantic Settings Class
**Created:**
- `app/core/settings.py` — `Settings(BaseSettings)` with all env-driven config; dimension weights validated to sum to 1.0

**Modified:**
- `.env.example` — added `DIMENSION_WEIGHTS_*` entries
- `app/core/lifespan.py` — instantiates `Settings` at startup
- `app/models/dimension.py` — `DIMENSION_WEIGHTS` now imported from `settings` instead of hardcoded

---

### Refactor 8 — Phase 5A: Request Correlation IDs
**Created:**
- `app/middleware/__init__.py`
- `app/middleware/correlation.py` — `CorrelationIdMiddleware`: assigns `X-Correlation-ID` to every request/response; `get_correlation_id()` for log injection

**Modified:**
- `app/core/dependencies.py` — exported `get_correlation_id`
- `app/main.py` — registered `CorrelationIdMiddleware`

---

### Refactor 9 — Phase 5B: Structured Logging
**Created:**
- `app/core/logging_config.py` — `configure_logging()`: structlog processors, JSON formatter, correlation ID injection

**Modified:**
- `app/core/lifespan.py` — calls `configure_logging()` at startup
- `app/routers/rag.py` — structured logging for chatbot question, retrieval, guardrail steps
- `app/services/llm/router.py` — logs model, task type, duration, token count
- `app/services/scoring_service.py` — logs `scoring_started`, prerequisites, per-dimension scores, `scoring_completed`

---

### Refactor 10 — Phase 4B: Config API + Final Wiring
**Created:**
- `app/routers/config.py` — endpoints: `GET /api/v1/config/scoring-parameters`, `/dimension-weights`, `/sector-baselines`, `/portfolio`

**Modified:**
- `app/config.py` — added sector baseline defaults and portfolio constants
- `app/core/lifespan.py` — additional startup wiring
- `app/main.py` — registered config router
- `app/routers/rag.py`, `app/routers/signals.py` — minor wiring fixes
- `app/schemas/company.py`, `app/schemas/dimensions.py`, `app/schemas/scoring.py` — minor schema additions
- `app/services/integration/cs2_client.py` — refactored to inherit from `BaseAPIClient`
- `app/services/retrieval/hybrid.py`, `app/services/search/vector_store.py` — additional properties exposed
- `app/services/task_store.py` — minor fixes

---

### Cleanup Commit — Code Reduction Pass
Removed dead code, simplified implementations. Net result: **−602 lines**.

**Modified:**
- `app/pipelines/board_analyzer.py` — −43 lines (removed unused methods)
- `app/pipelines/glassdoor_collector.py` — −35 lines
- `app/pipelines/job_signals.py` — −6 lines
- `app/pipelines/tech_signals.py` — −29 lines
- `app/services/collection/analyst_notes.py` — major simplification (942 → ~600 lines)
- `app/services/integration/cs1_client.py` — simplified (255 → ~150 lines)
- `app/services/retrieval/dimension_mapper.py` — simplified (327 → ~200 lines)
- `app/services/tech_signal_service.py` — −7 lines
- `app/services/workflows/ic_prep.py` — significant reduction (816 → ~500 lines)

---

*Generated 2026-03-18 — PE Org-AI-R Platform Refactoring Plan*