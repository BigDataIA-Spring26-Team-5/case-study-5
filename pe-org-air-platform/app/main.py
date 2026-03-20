import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# ── Router Imports (grouped by case study) ────────────────────────────────

# Infrastructure
from app.routers.health import router as health_router
from app.routers.config import router as config_router

# CS1 — Company Metadata
from app.routers.companies import router as companies_router

# CS2 — Evidence Collection
from app.routers.documents import router as documents_router
from app.routers.signals import router as signals_router
from app.routers.evidence import router as evidence_router

# CS3 — Scoring Pipeline
from app.routers.dimension_scores import router as dimension_scores_router
from app.routers.scoring import router as scoring_router
from app.routers.tc_vr_scoring import router as tc_vr_router
from app.routers.position_factor import router as pf_router
from app.routers.hr_scoring import router as hr_router
from app.routers.orgair_scoring import router as orgair_router
from app.routers.orgair_scoring import assessment_router

# CS4 — RAG & Analyst Workflows
from app.routers.rag import router as rag_router
from app.routers.analyst_notes import router as analyst_notes_router

# ── Framework imports ─────────────────────────────────────────────────────

from app.core.errors import validation_exception_handler
from app.core.errors import PlatformError, ERROR_STATUS_MAP
from app.core.lifespan import lifespan
from app.middleware.correlation import CorrelationIdMiddleware, get_correlation_id

logger = logging.getLogger(__name__)


# ── Swagger UI — tag display order (mirrors the CS1→CS4 pipeline flow) ────

_OPENAPI_TAGS = [
    # ── Infrastructure ────────────────────────────────────────────
    {"name": "Root"},
    {"name": "Health"},
    {
        "name": "Configuration",
        "description": (
            "**Infrastructure — Scoring Configuration**  \n"
            "Serves scoring parameters, dimension weights, and baselines."
        ),
    },

    # ── CS1 — Company Metadata ────────────────────────────────────
    {
        "name": "Companies",
        "description": (
            "**CS1 — Company Metadata**  \n"
            "CRUD for companies. Creating a company triggers background Groq enrichment "
            "(sector, revenue, employee count, fiscal year end). "
            "`GET /{ticker}/dimension-keywords` returns Groq-expanded rubric keywords per dimension."
        ),
    },

    # ── CS2 — Evidence Collection ─────────────────────────────────
    {
        "name": "1. Collection",
        "description": (
            "**CS2 — SEC EDGAR Document Collection**  \n"
            "Download 10-K, 10-Q, 8-K, DEF 14A from SEC EDGAR → S3 raw files → Snowflake metadata."
        ),
    },
    {
        "name": "2. Parsing",
        "description": (
            "**CS2 — Document Parsing**  \n"
            "Extract text/tables from raw SEC filings, identify key sections (Risk Factors, MD&A), "
            "upload parsed JSON to S3."
        ),
    },
    {
        "name": "3. Chunking",
        "description": (
            "**CS2 — Document Chunking**  \n"
            "Split parsed documents into overlapping chunks for LLM processing → S3 + Snowflake."
        ),
    },
    {
        "name": "Signals",
        "description": (
            "**CS2 — Signal Scoring (5 Evidence Signals)**  \n"
            "Score per company: `technology_hiring` (JobSpy), `digital_presence` (BuiltWith+Wappalyzer), "
            "`innovation_activity` (PatentsView/USPTO), `leadership_signals` (SEC DEF-14A), "
            "`culture` (Glassdoor), `board_governance` (DEF 14A board analysis)."
        ),
    },
    {
        "name": "Evidence",
        "description": (
            "**CS2 — Evidence Summary**  \n"
            "Aggregate evidence stats across all companies."
        ),
    },

    # ── CS3 — Scoring & Assessments ───────────────────────────────
    {
        "name": "Dimension Scores",
        "description": (
            "**CS3 — Dimension Score CRUD**  \n"
            "Add/retrieve/update individual scores for the 7 V^R dimensions: "
            "data_infrastructure, ai_governance, technology_stack, talent, "
            "leadership, use_case_portfolio, culture."
        ),
    },
    {
        "name": "CS3 Dimensions Scoring",
        "description": (
            "**CS3 — Full Scoring Pipeline**  \n"
            "CS2 signals → rubric-score SEC sections → map evidence to 7 dimensions (Table 1 matrix) "
            "→ persist to Snowflake.  \n"
            "**Prerequisite:** run CS2 signal scoring first."
        ),
    },
    {
        "name": "CS3 TC + V^R Scoring",
        "description": (
            "**CS3 — Technology Contribution + Value Recognition**  \n"
            "Computes TC (weighted dimension scores) and V^R (TC adjusted for confidence) "
            "for all CS3 portfolio companies."
        ),
    },
    {
        "name": "CS3 Position Factor",
        "description": (
            "**CS3 — Position Factor**  \n"
            "Computes the position factor (PF) that adjusts final scores based on "
            "portfolio positioning and sector context."
        ),
    },
    {
        "name": "CS3 H^R (Human Readiness)",
        "description": (
            "**CS3 — Human Capital Risk**  \n"
            "Computes H^R score from talent concentration, leadership depth, "
            "and culture signals."
        ),
    },
    {
        "name": "CS3 Org-AI-R",
        "description": (
            "**CS3 — Final Org-AI-R Score**  \n"
            "Computes the composite Org-AI-R score: V^R + Synergy + Position Factor − H^R penalty. "
            "Generates final assessment JSON for submission."
        ),
    },
    {
        "name": "Assessments",
        "description": (
            "**CS3 — Assessment Results**  \n"
            "Read-only access to completed scoring assessments per company."
        ),
    },

    # ── CS4 — RAG & Analyst Workflows ─────────────────────────────
    {
        "name": "RAG",
        "description": (
            "**CS4 — Retrieval-Augmented Generation**  \n"
            "RAG search over SEC filings and analyst notes. "
            "Justification generation, IC prep workflow, and evidence-backed Q&A."
        ),
    },
    {
        "name": "Analyst Notes",
        "description": (
            "**CS4 — Analyst Notes Collector**  \n"
            "Index post-LOI DD notes (interview transcripts, DD findings, data room summaries) "
            "into ChromaDB for RAG retrieval, Snowflake for structured queries, and S3 for raw storage."
        ),
    },
]


# ── FastAPI Application ───────────────────────────────────────────────────

app = FastAPI(
    title="PE Org-AI-R Platform",
    version="4.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationIdMiddleware)


# ── Exception Handlers ────────────────────────────────────────────────────

app.add_exception_handler(RequestValidationError, validation_exception_handler)


@app.exception_handler(PlatformError)
async def platform_error_handler(request: Request, exc: PlatformError):
    """Translate PlatformError subclasses to structured JSON responses."""
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler for unhandled exceptions — returns structured 500."""
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "details": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": get_correlation_id(),
        },
    )


# ── Register Routers (CS1 → CS2 → CS3 → CS4 pipeline flow) ──────────────

# Infrastructure
app.include_router(health_router)
app.include_router(config_router)

# CS1 — Company Metadata
app.include_router(companies_router)

# CS2 — Evidence Collection
app.include_router(documents_router)
app.include_router(signals_router)
app.include_router(evidence_router)

# CS3 — Scoring Pipeline (executed in order)
app.include_router(dimension_scores_router)     # Step 1: dimension score CRUD
app.include_router(scoring_router)              # Step 2: compute 7 dimension scores
app.include_router(tc_vr_router)                # Step 3: TC + V^R
app.include_router(pf_router)                   # Step 4: Position Factor
app.include_router(hr_router)                   # Step 5: Human Capital Risk
app.include_router(orgair_router)               # Step 6: Final Org-AI-R composite
app.include_router(assessment_router)           # Read-only assessment results

# CS4 — RAG & Analyst Workflows
app.include_router(rag_router)
app.include_router(analyst_notes_router)


# ── Root Endpoint ─────────────────────────────────────────────────────────

@app.get("/", tags=["Root"], summary="Root endpoint")
async def root():
    return {
        "service": "PE Org-AI-R Platform",
        "version": "4.0.0",
        "docs": {"swagger": "/docs", "redoc": "/redoc"},
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
