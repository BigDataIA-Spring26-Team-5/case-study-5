import signal
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

load_dotenv()

# IMPORT ROUTERS
from app.routers.companies import router as companies_router
from app.core.exceptions import validation_exception_handler
# from app.routers.industries import router as industries_router  # Not needed: CS4 doesn't use industry catalog
from app.routers.health import router as health_router
from app.routers.dimensionScores import router as dimension_scores_router
from app.routers.documents import router as documents_router
from app.routers.signals import router as signals_router
from app.routers.evidence import router as evidence_router
from app.routers.scoring import router as scoring_router
from app.routers.tc_vr_scoring import router as tc_vr_router
from app.routers.position_factor import router as pf_router
from app.routers.hr_scoring import router as hr_router
from app.routers.orgair_scoring import router as orgair_router
from app.routers.analyst_notes import router as analyst_notes_router
from fastapi.middleware.cors import CORSMiddleware

from app.shutdown import set_shutdown, is_shutting_down


# SWAGGER UI — tag display order
_OPENAPI_TAGS = [
    # ── Infrastructure ────────────────────────────────────────────
    {"name": "Root"},
    {"name": "Health"},

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
            "**CS2 — Signal Scoring (4 Evidence Signals)**  \n"
            "Score per company: `technology_hiring` (JobSpy), `digital_presence` (BuiltWith+Wappalyzer), "
            "`innovation_activity` (PatentsView/USPTO), `leadership_signals` (SEC DEF-14A)."
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

    # ── CS4 — Analyst Notes ───────────────────────────────────────
    {
        "name": "Analyst Notes",
        "description": (
            "**CS4 — Analyst Notes Collector**  \n"
            "Index post-LOI DD notes (interview transcripts, DD findings, data room summaries) "
            "into ChromaDB for RAG retrieval, Snowflake for structured queries, and S3 for raw storage.  \n"
            "Call `POST /{company_id}/load` after a server restart to restore the in-memory cache."
        ),
    },
]

# FASTAPI APPLICATION CONFIGURATION
app = FastAPI(
    title="PE Org-AI-R Platform — CS4 Data Layer",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=_OPENAPI_TAGS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REGISTER EXCEPTION HANDLERS
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# REGISTER ROUTERS
app.include_router(health_router)            # Operational health checks
# CS1 — Company metadata
app.include_router(companies_router)         # GET /companies/{ticker}
# CS2 — Evidence collection
app.include_router(documents_router)         # collect / parse / chunk / report
app.include_router(signals_router)           # job / tech / patent / leadership signals
app.include_router(evidence_router)          # aggregated evidence stats per ticker
# CS3 — Scoring & assessments
app.include_router(dimension_scores_router)  # per-dimension scores + confidence intervals
app.include_router(scoring_router)           # dimension scoring computation + rubrics

from app.routers.rag import router as rag_router
app.include_router(rag_router)

app.include_router(tc_vr_router)             # TC + V^R computation
app.include_router(pf_router)               # Position Factor computation
app.include_router(hr_router)               # Human Capital Risk computation
app.include_router(orgair_router)           # Synergy + Org-AI-R computation
app.include_router(analyst_notes_router)   # CS4 — Analyst Notes (interview, DD findings, data room)

# COMMENTED OUT — not needed:
# app.include_router(industries_router)        # static catalog, not used by CS4 clients


# ROOT ENDPOINT
@app.get("/", tags=["Root"], summary="Root endpoint")
async def root():
    return {
        "service": "PE Org-AI-R Platform Foundation API",
        "version": "1.0.0",
        "docs": {
            "swagger": "/docs",
            "redoc": "/redoc"
        },
        "status": "running"
    }


# STARTUP EVENT
@app.on_event("startup")
async def startup_event():
    print("Starting PE Org-AI-R Platform Foundation API...")
    print("Swagger UI available at: http://localhost:8000/docs")

    loop = asyncio.get_running_loop()

    def _signal_handler(sig):
        print(f"\n⚠️  Received {sig.name} — shutting down gracefully...")
        set_shutdown()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler, sig)
    except NotImplementedError:
        print("⚠️  Signal handlers not supported on Windows, using fallback...")
        _register_windows_signal_handlers()


# SHUTDOWN EVENT
@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down PE Org-AI-R Platform Foundation API...")
    set_shutdown()


def _register_windows_signal_handlers():
    original_sigint = signal.getsignal(signal.SIGINT)

    def _windows_handler(signum, frame):
        print(f"\n⚠️  Received Ctrl+C — shutting down gracefully...")
        set_shutdown()
        if callable(original_sigint):
            original_sigint(signum, frame)

    signal.signal(signal.SIGINT, _windows_handler)


# RUN WITH UVICORN
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )