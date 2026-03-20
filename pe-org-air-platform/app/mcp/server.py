"""MCP Server — PE Org-AI-R Platform CS5 agentic layer.

Exposes 6 tools via stdio transport. Each tool lazily imports only the
dependencies it actually needs, so tools with no external requirements
(project_ebitda_impact) work immediately while heavier tools wait until
the relevant backend (FastAPI / Snowflake / ChromaDB) is reachable.

Run with:  python -m app.mcp.server
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server instance
# (No app.* imports at module level — app/services/__init__.py eagerly
#  imports redis via cache.py which breaks standalone startup.)
# ---------------------------------------------------------------------------

server = Server(
    name="pe-org-air",
    version="1.0.0",
    instructions=(
        "Portfolio intelligence tools for the PE Org-AI-R platform. "
        "Use these tools to assess AI readiness, retrieve evidence, generate "
        "justifications, and project EBITDA impact for PE portfolio companies."
    ),
)

# ---------------------------------------------------------------------------
# Per-component lazy singletons
# Each tool initialises only what it needs; expensive clients (Snowflake,
# ChromaDB) are never touched unless the corresponding tool is called.
# ---------------------------------------------------------------------------

_ebitda_calc = None
_gap_analyzer = None
_cs3_client = None
_cs2_client = None
_composite_svc = None
_cs4_client = None


def _ebitda() -> Any:
    global _ebitda_calc
    if _ebitda_calc is None:
        from app.services.value_creation.ebitda import EBITDACalculator
        _ebitda_calc = EBITDACalculator()
    return _ebitda_calc


def _gap() -> Any:
    global _gap_analyzer
    if _gap_analyzer is None:
        from app.services.value_creation.gap_analysis import GapAnalyzer
        _gap_analyzer = GapAnalyzer()
    return _gap_analyzer


def _cs3() -> Any:
    global _cs3_client
    if _cs3_client is None:
        from app.services.integration.cs3_client import CS3Client
        _cs3_client = CS3Client()
    return _cs3_client


def _cs2() -> Any:
    global _cs2_client
    if _cs2_client is None:
        from app.services.integration.cs2_client import CS2Client
        _cs2_client = CS2Client()
    return _cs2_client


def _composite() -> Any:
    global _composite_svc
    if _composite_svc is None:
        from app.services.composite_scoring_service import CompositeScoringService
        _composite_svc = CompositeScoringService()
    return _composite_svc


def _cs4() -> Any:
    global _cs4_client
    if _cs4_client is None:
        from app.services.retrieval.hybrid import HybridRetriever
        from app.services.justification.generator import JustificationGenerator
        from app.services.integration.cs4_client import CS4Client
        retriever = HybridRetriever()
        generator = JustificationGenerator(retriever=retriever)
        _cs4_client = CS4Client(
            justification_generator=generator,
            hybrid_retriever=retriever,
        )
    return _cs4_client


def _track(name: str, status: str, duration: float) -> None:
    """Best-effort Prometheus metric recording — silently skips if unavailable."""
    try:
        from app.services.observability.metrics import (
            mcp_tool_calls_total,
            mcp_tool_duration_seconds,
        )
        mcp_tool_calls_total.labels(tool_name=name, status=status).inc()
        mcp_tool_duration_seconds.labels(tool_name=name).observe(duration)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    types.Tool(
        name="calculate_org_air_score",
        description=(
            "Compute the full Org-AI-R composite score for a portfolio company via the "
            "CS3 scoring pipeline (TC → V^R → PF → H^R → Synergy → Org-AI-R). "
            "Returns org_air_score, vr_score, hr_score, synergy, and confidence interval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "Company ticker symbol (e.g. 'NVDA', 'JPM', 'WMT', 'GE', 'DG').",
                },
            },
            "required": ["company_id"],
        },
    ),
    types.Tool(
        name="get_company_evidence",
        description=(
            "Retrieve raw evidence items for a portfolio company collected in CS2. "
            "Evidence comes from SEC filings, job postings, patents, Glassdoor reviews, "
            "and tech-stack signals. Optionally filter by V^R dimension."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "Company ticker symbol.",
                },
                "dimension": {
                    "type": "string",
                    "description": (
                        "Optional V^R dimension filter. One of: data_infrastructure, "
                        "ai_governance, technology_stack, talent, leadership, "
                        "use_case_portfolio, culture."
                    ),
                    "enum": [
                        "data_infrastructure",
                        "ai_governance",
                        "technology_stack",
                        "talent",
                        "leadership",
                        "use_case_portfolio",
                        "culture",
                    ],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of evidence items to return. Defaults to 50.",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["company_id"],
        },
    ),
    types.Tool(
        name="generate_justification",
        description=(
            "Generate an evidence-backed LLM justification for a specific V^R dimension "
            "score, using the CS4 RAG pipeline. Returns a summary, evidence citations, "
            "identified gaps, and evidence strength rating."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "Company ticker symbol.",
                },
                "dimension": {
                    "type": "string",
                    "description": "V^R dimension to justify.",
                    "enum": [
                        "data_infrastructure",
                        "ai_governance",
                        "technology_stack",
                        "talent",
                        "leadership",
                        "use_case_portfolio",
                        "culture",
                    ],
                },
            },
            "required": ["company_id", "dimension"],
        },
    ),
    types.Tool(
        name="project_ebitda_impact",
        description=(
            "Project EBITDA improvement from raising a company's Org-AI-R score. "
            "Uses sector-specific EBITDA multipliers and an H^R risk adjustment. "
            "Pure local calculation — no external API calls required. "
            "Returns projected_ebitda_improvement_pct, net_benefit, implementation_cost, "
            "and hr_risk_factor."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "Company ticker symbol.",
                },
                "entry_score": {
                    "type": "number",
                    "description": "Current Org-AI-R score at time of entry (0–100).",
                    "minimum": 0,
                    "maximum": 100,
                },
                "target_score": {
                    "type": "number",
                    "description": "Target Org-AI-R score after value-creation initiatives (0–100).",
                    "minimum": 0,
                    "maximum": 100,
                },
                "h_r_score": {
                    "type": "number",
                    "description": "Human Readiness (H^R) score, used as HR risk adjustment (0–100).",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["company_id", "entry_score", "target_score", "h_r_score"],
        },
    ),
    types.Tool(
        name="run_gap_analysis",
        description=(
            "Identify dimension-level gaps between a company's current Org-AI-R score "
            "and a target score. Returns prioritised dimension gaps, each with current "
            "and target scores, improvement actions, next-level criteria, and priority rank."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "Company ticker symbol.",
                },
                "target_org_air": {
                    "type": "number",
                    "description": "Target Org-AI-R composite score to close to (0–100).",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["company_id", "target_org_air"],
        },
    ),
    types.Tool(
        name="get_portfolio_summary",
        description=(
            "Return a fund-level portfolio view aggregating all CS3 portfolio companies "
            "(NVDA, JPM, WMT, GE, DG). Includes Fund-AI-R score, AI leaders/laggards "
            "counts, average V^R and H^R, and per-company dimension scores."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "fund_id": {
                    "type": "string",
                    "description": "Fund identifier (e.g. 'PE-FUND-I').",
                    "default": "PE-FUND-I",
                },
            },
            "required": [],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch each tool to only the components it actually needs."""
    start = time.time()
    status = "success"
    try:
        if name == "calculate_org_air_score":
            result = await _calculate_org_air_score(arguments)
        elif name == "get_company_evidence":
            result = await _get_company_evidence(arguments)
        elif name == "generate_justification":
            result = await _generate_justification(arguments)
        elif name == "project_ebitda_impact":
            result = await _project_ebitda_impact(arguments)
        elif name == "run_gap_analysis":
            result = await _run_gap_analysis(arguments)
        elif name == "get_portfolio_summary":
            result = await _get_portfolio_summary(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
        return result
    except Exception:
        status = "error"
        raise
    finally:
        _track(name, status, time.time() - start)


# ---------------------------------------------------------------------------
# Tool implementations — each imports only what it needs
# ---------------------------------------------------------------------------


async def _calculate_org_air_score(args: dict) -> dict:
    """Fetches the stored Org-AI-R assessment via CS3Client (HTTP to FastAPI).
    Requires FastAPI to be running on localhost:8000.
    Scores must already have been computed via POST /api/v1/scoring/orgair/portfolio.
    """
    ticker = args["company_id"].upper()
    client = await asyncio.to_thread(_cs3)
    assessment = await asyncio.to_thread(client.get_assessment, ticker)
    if not assessment:
        return {
            "ticker": ticker,
            "status": "not_found",
            "error": (
                "No assessment found for this ticker. "
                "Run POST /api/v1/scoring/orgair/portfolio first."
            ),
        }
    return {
        "ticker": assessment.ticker,
        "status": "success",
        "org_air_score": assessment.org_air_score,
        "vr_score": assessment.valuation_risk,
        "hr_score": assessment.human_capital_risk,
        "synergy": assessment.synergy,
        "position_factor": assessment.position_factor,
        "talent_concentration": assessment.talent_concentration,
        "dimension_scores": {
            dim: {
                "score": ds.score,
                "level": ds.level,
                "level_name": ds.level_name,
            }
            for dim, ds in assessment.dimension_scores.items()
        },
    }


async def _get_company_evidence(args: dict) -> dict:
    """Delegates to CS2Client (fetches from S3 directly — needs AWS credentials)."""
    ticker = args["company_id"].upper()
    dimension = args.get("dimension")
    limit = int(args.get("limit", 50))

    dim_to_signal = {
        "data_infrastructure": ["digital_presence"],
        "ai_governance": ["governance_signals"],
        "technology_stack": ["digital_presence", "technology_hiring"],
        "talent": ["technology_hiring"],
        "leadership": ["leadership_signals"],
        "use_case_portfolio": ["innovation_activity"],
        "culture": ["culture_signals"],
    }
    signal_cats = dim_to_signal.get(dimension) if dimension else None

    try:
        client = await asyncio.to_thread(_cs2)
        evidence = await asyncio.to_thread(client.get_evidence, ticker, signal_categories=signal_cats)
    except Exception as e:
        return {
            "company_id": ticker,
            "dimension": dimension,
            "evidence": [],
            "count": 0,
            "error": str(e),
        }

    items = [
        {
            "evidence_id": e.evidence_id,
            "source_type": e.source_type,
            "signal_category": e.signal_category,
            "content": e.content[:500],
            "confidence": e.confidence,
        }
        for e in evidence[:limit]
    ]
    return {"company_id": ticker, "dimension": dimension, "evidence": items, "count": len(items)}


async def _generate_justification(args: dict) -> dict:
    """Delegates to CS4Client (needs ChromaDB + Snowflake + LLM)."""
    ticker = args["company_id"].upper()
    dimension = args["dimension"]
    client = await asyncio.to_thread(_cs4)
    result = await asyncio.to_thread(client.generate_justification, ticker, dimension)
    return result.to_dict()


async def _project_ebitda_impact(args: dict) -> dict:
    """Pure local math — no external services required."""
    from app.services.composite_scoring_service import COMPANY_SECTORS
    ticker = args["company_id"].upper()
    sector = COMPANY_SECTORS.get(ticker, "technology")
    calc = _ebitda()
    projection = await asyncio.to_thread(
        calc.project,
        ticker,
        float(args["entry_score"]),
        float(args["target_score"]),
        float(args["h_r_score"]),
        sector,
    )
    return projection.to_dict()


async def _run_gap_analysis(args: dict) -> dict:
    """Fetches current scores from CS3 (needs FastAPI running), then runs local gap math."""
    ticker = args["company_id"].upper()
    target_org_air = float(args["target_org_air"])

    client = await asyncio.to_thread(_cs3)
    assessment = await asyncio.to_thread(client.get_assessment, ticker)

    if assessment:
        dim_scores = {dim: ds.score for dim, ds in assessment.dimension_scores.items()}
        current_org_air = assessment.org_air_score
    else:
        dim_scores = {}
        current_org_air = 0.0

    analyzer = _gap()
    result = await asyncio.to_thread(
        analyzer.analyze, ticker, dim_scores, current_org_air, target_org_air
    )
    return result.to_dict()


async def _get_portfolio_summary(args: dict) -> dict:
    """Aggregates all portfolio companies from CS3 (needs FastAPI running)."""
    from app.services.composite_scoring_service import (
        COMPANY_NAMES, COMPANY_SECTORS, MARKET_CAP_PERCENTILES,
    )
    from app.config.company_mappings import CS3_PORTFOLIO

    fund_id = args.get("fund_id", "PE-FUND-I")
    client = await asyncio.to_thread(_cs3)
    companies = []

    for ticker in CS3_PORTFOLIO:
        assessment = await asyncio.to_thread(client.get_assessment, ticker)
        org_air = vr = hr = synergy = pf = 0.0
        dim_scores: dict = {}
        if assessment:
            org_air = assessment.org_air_score
            vr = assessment.valuation_risk
            hr = assessment.human_capital_risk
            synergy = assessment.synergy
            pf = assessment.position_factor
            dim_scores = {dim: ds.score for dim, ds in assessment.dimension_scores.items()}
        companies.append({
            "ticker": ticker,
            "name": COMPANY_NAMES.get(ticker, ticker),
            "sector": COMPANY_SECTORS.get(ticker, ""),
            "org_air_score": org_air,
            "vr_score": vr,
            "hr_score": hr,
            "synergy": synergy,
            "position_factor": pf,
            "dimension_scores": dim_scores,
            "market_cap_percentile": MARKET_CAP_PERCENTILES.get(ticker, 0.0),
        })

    scores = [c["org_air_score"] for c in companies if c["org_air_score"] > 0]
    vr_scores = [c["vr_score"] for c in companies if c["vr_score"] > 0]
    hr_scores = [c["hr_score"] for c in companies if c["hr_score"] > 0]
    avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else 0.0

    return {
        "fund_id": fund_id,
        "companies": companies,
        "fund_air_score": avg(scores),
        "total_companies": len(companies),
        "ai_leaders": sum(1 for c in companies if c["org_air_score"] >= 70),
        "ai_laggards": sum(1 for c in companies if 0 < c["org_air_score"] < 50),
        "avg_vr": avg(vr_scores),
        "avg_hr": avg(hr_scores),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    import sys
    import structlog

    # ── Redirect ALL output to stderr ────────────────────────────────────
    # MCP stdio transport owns stdout exclusively for JSON-RPC messages.
    # Any log line written to stdout corrupts the protocol stream.

    # 1. structlog — force PrintLoggerFactory to write to stderr
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # 2. stdlib logging — remove any existing handlers (they default to stdout)
    #    and replace with a single stderr handler
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(_stderr_handler)
    root.setLevel(logging.INFO)
    # ─────────────────────────────────────────────────────────────────────

    logger.info("Starting PE Org-AI-R MCP server (stdio transport)...")
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


if __name__ == "__main__":
    asyncio.run(main())
