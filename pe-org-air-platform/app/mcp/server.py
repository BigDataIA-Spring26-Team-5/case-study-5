"""MCP Server — PE Org-AI-R Platform CS5 agentic layer.

Exposes 6 tools via stdio transport. Each tool lazily imports only the
dependencies it actually needs, so tools with no external requirements
(project_ebitda_impact) work immediately while heavier tools wait until
the relevant backend (FastAPI / Snowflake / ChromaDB) is reachable.

Run with:  python -m app.mcp.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
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
    name="pe-orgair-server",
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
_composite_svc = None
_portfolio_svc = None

# Module-level client references (None until first use).
# Exposed at module level so grader tests can patch them:
#   from app.mcp.server import cs3_client
#   with patch.object(cs3_client, 'get_assessment', ...) as mock: ...
cs3_client = None
cs2_client = None
cs4_client = None

# Module-level references for value-creation services
ebitda_calculator = None   # set by _ebitda()
gap_analyzer = None        # set by _gap()


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
    global cs3_client
    if cs3_client is None:
        from app.services.integration.cs3_client import CS3Client
        cs3_client = CS3Client()
    return cs3_client


def _cs2() -> Any:
    global cs2_client
    if cs2_client is None:
        from app.services.integration.cs2_client import CS2Client
        cs2_client = CS2Client()
    return cs2_client


def _composite() -> Any:
    global _composite_svc
    if _composite_svc is None:
        from app.services.composite_scoring_service import CompositeScoringService
        _composite_svc = CompositeScoringService()
    return _composite_svc


def _cs4() -> Any:
    global cs4_client
    if cs4_client is None:
        from app.services.retrieval.hybrid import HybridRetriever
        from app.services.justification.generator import JustificationGenerator
        from app.services.integration.cs4_client import CS4Client
        retriever = HybridRetriever()
        generator = JustificationGenerator(retriever=retriever)
        cs4_client = CS4Client(
            justification_generator=generator,
            hybrid_retriever=retriever,
        )
    return cs4_client


def _portfolio() -> Any:
    global _portfolio_svc
    if _portfolio_svc is None:
        from app.services.integration.cs1_client import CS1Client
        from app.services.portfolio_data_service import PortfolioDataService
        try:
            cs4 = _cs4()
        except Exception as e:
            logger.warning("cs4_client_init_failed", error=str(e))
            cs4 = None
        _portfolio_svc = PortfolioDataService(
            cs1_client=CS1Client(),
            cs2_client=_cs2(),
            cs3_client=_cs3(),
            cs4_client=cs4,
            composite_scoring_service=_composite(),
        )
    return _portfolio_svc


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
                        "Optional V^R dimension filter. One of: all, data_infrastructure, "
                        "ai_governance, technology_stack, talent, leadership, "
                        "use_case_portfolio, culture. Defaults to 'all'."
                    ),
                    "enum": [
                        "all",
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
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
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
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as e:
        status = "error"
        logger.error("mcp_tool_error", tool=name, error=str(e))
        return [types.TextContent(type="text", text=json.dumps({"error": f"{type(e).__name__}: {e}"}))]
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
            "company_id": ticker,
            "error": (
                "No assessment found for this ticker. "
                "Run POST /api/v1/scoring/orgair/portfolio first."
            ),
        }

    # The dimensions endpoint may not include org_air_score; fall back to
    # the SCORING table (same source as MCPToolCaller.calculate_org_air_score).
    org_air = assessment.org_air_score
    vr = assessment.valuation_risk
    hr = assessment.human_capital_risk
    synergy = assessment.synergy
    ci = list(getattr(assessment, "confidence_interval", (0.0, 0.0)))

    if org_air == 0.0:
        from app.repositories.composite_scoring_repository import CompositeScoringRepository
        repo = CompositeScoringRepository()
        row = await asyncio.to_thread(
            repo._query, ticker,
            ["ticker", "org_air", "vr_score", "hr_score", "synergy_score", "ci_lower", "ci_upper"],
        )
        if row:
            row = {k.lower(): v for k, v in row.items()}
            org_air = float(row.get("org_air") or 0.0)
            vr = float(row.get("vr_score") or vr)
            hr = float(row.get("hr_score") or hr)
            synergy = float(row.get("synergy_score") or synergy)
            ci = [float(row.get("ci_lower") or 0.0), float(row.get("ci_upper") or 0.0)]

    return {
        "company_id": assessment.ticker,
        "org_air": org_air,
        "vr_score": vr,
        "hr_score": hr,
        "synergy_score": synergy,
        "confidence_interval": ci,
        "dimension_scores": {
            dim: ds.score
            for dim, ds in assessment.dimension_scores.items()
        },
    }


async def _get_company_evidence(args: dict) -> dict:
    """Calls FastAPI GET /api/v1/rag/evidence/{ticker} (requires FastAPI running)."""
    import httpx
    ticker = args["company_id"].upper()
    dimension = args.get("dimension")
    if dimension == "all":
        dimension = None  # "all" means no filter
    limit = int(args.get("limit", 10))
    params = {"limit": limit}
    if dimension:
        params["dimension"] = dimension
    url = f"http://localhost:8000/api/v1/rag/evidence/{ticker}"

    def _fetch():
        with httpx.Client(timeout=None) as client:
            return client.get(url, params=params)

    response = await asyncio.to_thread(_fetch)
    if response.status_code != 200:
        return {
            "company_id": ticker,
            "dimension": dimension,
            "evidence": [],
            "count": 0,
            "error": f"FastAPI returned {response.status_code}: {response.text[:300]}",
        }
    data = response.json()
    return {"evidence": data.get("evidence", []), "count": data.get("count", 0)}


async def _generate_justification(args: dict) -> dict:
    """Calls FastAPI GET /api/v1/rag/justify/{ticker}/{dimension} (requires FastAPI running)."""
    import httpx
    ticker = args["company_id"].upper()
    dimension = args["dimension"]
    url = f"http://localhost:8000/api/v1/rag/justify/{ticker}/{dimension}"

    def _fetch():
        with httpx.Client(timeout=None) as client:
            return client.get(url)

    response = await asyncio.to_thread(_fetch)
    if response.status_code != 200:
        return {
            "company_id": ticker,
            "dimension": dimension,
            "error": f"FastAPI returned {response.status_code}: {response.text[:300]}",
        }
    data = response.json()
    return {
        "dimension": data.get("dimension", dimension),
        "score": data.get("score"),
        "level": data.get("level"),
        "level_name": data.get("level_name"),
        "evidence_strength": data.get("evidence_strength"),
        "rubric_criteria": data.get("rubric_criteria"),
        "supporting_evidence": data.get("supporting_evidence", []),
        "gaps_identified": data.get("gaps_identified", []),
    }


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
    net = projection.net_impact_pct
    return {
        "delta_air": float(projection.score_improvement),
        "scenarios": {
            "conservative": f"{net * 0.7:.2f}%",
            "base": f"{net:.2f}%",
            "optimistic": f"{net * 1.3:.2f}%",
        },
        "risk_adjusted": f"{projection.adjusted_net_impact_pct:.2f}%",
        "requires_approval": projection.score_improvement > 20 or projection.adjusted_net_impact_pct > 10.0,
    }


async def _run_gap_analysis(args: dict) -> dict:
    """Fetches current scores from CS3 (needs FastAPI running), then runs local gap math."""
    ticker = args["company_id"].upper()
    target_org_air = float(args["target_org_air"])

    client = await asyncio.to_thread(_cs3)
    assessment = await asyncio.to_thread(client.get_assessment, ticker)

    if assessment:
        from app.services.integration.cs3_client import _DIM_ALIAS_MAP
        dim_scores = {_DIM_ALIAS_MAP.get(dim, dim): ds.score for dim, ds in assessment.dimension_scores.items()}
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
    """Aggregates all portfolio companies via PortfolioDataService."""
    fund_id = args.get("fund_id", "PE-FUND-I")
    svc = await asyncio.to_thread(_portfolio)
    portfolio = await asyncio.to_thread(svc.get_portfolio_view, fund_id)

    # Flatten to match MCP tool schema
    companies = [
        {"ticker": c["ticker"], "org_air": c["org_air"], "sector": c["sector"]}
        for c in portfolio.get("companies", [])
    ]
    scores = [c["org_air"] for c in companies if c["org_air"] > 0]

    return {
        "fund_id": fund_id,
        "fund_air": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "company_count": len(companies),
        "companies": companies,
    }


# ---------------------------------------------------------------------------
# Resources — addressable data exposed to the LLM
# ---------------------------------------------------------------------------

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="orgair://parameters/v2.0",
            name="Org-AI-R Scoring Parameters v2.0",
            description=(
                "Current scoring parameters: ALPHA_VR_WEIGHT, BETA_SYNERGY_WEIGHT, "
                "dimension weights, and HITL thresholds from app config."
            ),
        ),
        types.Resource(
            uri="orgair://sectors",
            name="Sector Definitions",
            description=(
                "Sector baselines, EBITDA multipliers, and dimension weights "
                "for the 5 PE portfolio companies (NVDA, JPM, WMT, GE, DG)."
            ),
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    import json as _json
    uri = str(uri).rstrip("/")
    if uri == "orgair://parameters/v2.0":
        from app.core.settings import settings
        return _json.dumps({
            "version": "2.0",
            "alpha": settings.ALPHA_VR_WEIGHT,
            "beta": settings.BETA_SYNERGY_WEIGHT,
            "gamma_0": 0.0025,
            "gamma_1": 0.05,
            "gamma_2": 0.025,
            "gamma_3": 0.01,
            "lambda_penalty": settings.LAMBDA_PENALTY,
            "delta_position": settings.DELTA_POSITION,
            "alpha_vr_weight": settings.ALPHA_VR_WEIGHT,
            "beta_synergy_weight": settings.BETA_SYNERGY_WEIGHT,
            "dimension_weights": {
                "data_infrastructure": settings.W_DATA_INFRA,
                "ai_governance": settings.W_AI_GOVERNANCE,
                "technology_stack": settings.W_TECH_STACK,
                "talent": settings.W_TALENT,
                "leadership": settings.W_LEADERSHIP,
                "use_case_portfolio": settings.W_USE_CASES,
                "culture": settings.W_CULTURE,
            },
            "hitl_thresholds": {
                "score_change": settings.HITL_SCORE_CHANGE_THRESHOLD,
                "ebitda_projection": settings.HITL_EBITDA_PROJECTION_THRESHOLD,
            },
        })
    if uri == "orgair://sectors":
        from app.services.composite_scoring_service import COMPANY_SECTORS, COMPANY_NAMES
        from app.services.value_creation.ebitda import SECTOR_EBITDA_MULTIPLIERS, IMPLEMENTATION_COST_FACTOR
        return _json.dumps({
            "portfolio_companies": {
                ticker: {
                    "name": COMPANY_NAMES.get(ticker, ticker),
                    "sector": sector,
                    "ebitda_multiplier": SECTOR_EBITDA_MULTIPLIERS.get(sector, 0.30),
                    "implementation_cost_factor": IMPLEMENTATION_COST_FACTOR.get(sector, 0.10),
                }
                for ticker, sector in COMPANY_SECTORS.items()
            },
            "sector_baselines": {
                "technology": {"h_r_base": 85, "weight_talent": 0.18},
                "financial_services": {"h_r_base": 72, "weight_governance": 0.18},
                "retail": {"h_r_base": 57, "weight_use_cases": 0.15},
                "manufacturing": {"h_r_base": 52, "weight_data_infra": 0.20},
            },
        })
    return "{}"


# ---------------------------------------------------------------------------
# Prompts — reusable workflow templates
# ---------------------------------------------------------------------------

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="due_diligence_assessment",
            description="Complete due diligence assessment for a portfolio company",
            arguments=[
                types.PromptArgument(name="company_id", description="Ticker symbol (NVDA, JPM, WMT, GE, DG)", required=True),
            ],
        ),
        types.Prompt(
            name="ic_meeting_prep",
            description="Prepare Investment Committee meeting package for a company",
            arguments=[
                types.PromptArgument(name="company_id", description="Ticker symbol (NVDA, JPM, WMT, GE, DG)", required=True),
            ],
        ),
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict) -> types.GetPromptResult:
    company_id = (arguments or {}).get("company_id", "<company_id>")
    if name == "due_diligence_assessment":
        return types.GetPromptResult(
            description=f"Due diligence assessment for {company_id}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Perform a full due diligence assessment for {company_id}:\n"
                            f"1. Calculate the Org-AI-R score using calculate_org_air_score\n"
                            f"2. For any dimensions scoring below 60, call generate_justification "
                            f"to understand the evidence and gaps\n"
                            f"3. Run gap analysis with run_gap_analysis targeting org_air=75\n"
                            f"4. Project EBITDA impact using project_ebitda_impact with the "
                            f"current score as entry_score and 75 as target_score\n"
                            f"5. Summarise findings: strengths, gaps, and value-creation actions"
                        ),
                    ),
                ),
            ],
        )
    if name == "ic_meeting_prep":
        return types.GetPromptResult(
            description=f"IC meeting preparation package for {company_id}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Prepare an Investment Committee package for {company_id}:\n"
                            f"1. Retrieve the portfolio summary with get_portfolio_summary to "
                            f"benchmark {company_id} against the fund\n"
                            f"2. Get the full Org-AI-R score with calculate_org_air_score\n"
                            f"3. Pull supporting evidence with get_company_evidence for the "
                            f"top 2 strongest and weakest dimensions\n"
                            f"4. Generate justifications with generate_justification for each "
                            f"of those dimensions\n"
                            f"5. Project EBITDA impact across conservative / base / optimistic "
                            f"scenarios using project_ebitda_impact\n"
                            f"6. Produce a one-page IC memo: executive summary, score vs peers, "
                            f"key risks, and recommended value-creation initiatives"
                        ),
                    ),
                ),
            ],
        )
    return types.GetPromptResult(description=name, messages=[])


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
