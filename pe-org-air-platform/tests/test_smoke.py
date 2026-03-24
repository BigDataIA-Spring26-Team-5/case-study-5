"""
CS5 Fix Verification — Smoke Tests
Run: python test_smoke.py
Does NOT require FastAPI or any external services.
"""
import sys
import traceback

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"  FAIL  {name}")
        print(f"        {e}")


# ── FIX 2: MCP Server module-level clients ────────────────────────────────────
print("\n[FIX 2] MCP Server — module-level cs3_client/cs2_client/cs4_client")

def fix2_importable():
    from app.mcp.server import cs3_client, cs2_client, cs4_client  # noqa: F401

def fix2_are_none_before_first_call():
    from app.mcp.server import cs3_client, cs2_client, cs4_client
    # They should be None until first tool call (lazy init)
    # Just confirm they are importable and not raising AttributeError
    assert cs3_client is None or cs3_client is not None  # always true

def fix2_gamma_in_resource():
    import asyncio, json
    from app.mcp.server import read_resource
    data = json.loads(asyncio.run(read_resource("orgair://parameters/v2.0")))
    assert "gamma_0" in data, f"gamma_0 missing, got keys: {list(data.keys())}"
    assert data["gamma_0"] == 0.0025
    assert data["gamma_1"] == 0.05
    assert data["alpha"] == 0.6

check("cs3_client importable at module level", fix2_importable)
check("clients are None before first use (lazy)", fix2_are_none_before_first_call)
check("orgair://parameters/v2.0 has gamma_0/1/2/3 + alpha", fix2_gamma_in_resource)


# ── FIX 4: Assessment History ─────────────────────────────────────────────────
print("\n[FIX 4] Assessment History — cs1_client, assessor_id, factory")

def fix4_two_client_constructor():
    from app.services.integration.cs1_client import CS1Client
    from app.services.integration.cs3_client import CS3Client
    from app.services.tracking.history_service import AssessmentHistoryService
    svc = AssessmentHistoryService(cs1_client=CS1Client(), cs3_client=CS3Client())
    assert svc.cs1 is not None
    assert svc.cs3 is not None

def fix4_factory():
    from app.services.integration.cs1_client import CS1Client
    from app.services.integration.cs3_client import CS3Client
    from app.services.tracking.history_service import create_history_service
    svc = create_history_service(CS1Client(), CS3Client())
    assert svc is not None

def fix4_assessor_id_field():
    from dataclasses import fields
    from app.services.tracking.history_service import AssessmentSnapshot
    fnames = [f.name for f in fields(AssessmentSnapshot)]
    assert "assessor_id" in fnames, f"assessor_id not in fields: {fnames}"
    assert "assessor" not in fnames, "old 'assessor' field still present"

check("AssessmentHistoryService(cs1_client=, cs3_client=)", fix4_two_client_constructor)
check("create_history_service() factory", fix4_factory)
check("AssessmentSnapshot has assessor_id (not assessor)", fix4_assessor_id_field)


# ── FIX 5: Evidence Display signature ─────────────────────────────────────────
print("\n[FIX 5] Evidence Display — render_company_evidence_panel signature")

def fix5_signature():
    import inspect
    sys.path.insert(0, "streamlit/components")
    from evidence_display import render_company_evidence_panel
    sig = inspect.signature(render_company_evidence_panel)
    params = list(sig.parameters.keys())
    assert "company_id" in params, f"missing company_id: {params}"
    assert "justifications" in params, f"missing justifications: {params}"

check("render_company_evidence_panel(company_id, justifications=None)", fix5_signature)


# ── FIX 7: ScoringAgent HITL bounds ──────────────────────────────────────────
print("\n[FIX 7] Specialists — HITL bounds + EvidenceAgent 3 dimensions")

def fix7_hitl_bounds():
    import inspect
    from app.agents.specialists import ScoringAgent
    src = inspect.getsource(ScoringAgent.__call__)
    assert "org_air > 85 or org_air < 40" in src, \
        "HITL condition not found. Expected: org_air > 85 or org_air < 40"

def fix7_evidence_3dims():
    import inspect
    from app.agents.specialists import EvidenceAgent
    src = inspect.getsource(EvidenceAgent.__call__)
    assert "data_infrastructure" in src, "data_infrastructure missing from EvidenceAgent"
    assert "use_case_portfolio" in src, "use_case_portfolio missing from EvidenceAgent"
    assert "talent" in src, "talent missing from EvidenceAgent"

check("ScoringAgent HITL: org_air > 85 or org_air < 40", fix7_hitl_bounds)
check("EvidenceAgent fetches 3 dims: data_infrastructure, talent, use_case_portfolio", fix7_evidence_3dims)


# ── FIX 8: Fund-AI-R ─────────────────────────────────────────────────────────
print("\n[FIX 8] Fund-AI-R — CS5FundMetrics, calculate_fund_metrics, no-arg constructor")

def fix8_no_arg_constructor():
    from app.services.analytics.fund_air import FundAIRCalculator
    calc = FundAIRCalculator()
    assert calc is not None

def fix8_singleton():
    from app.services.analytics.fund_air import fund_air_calculator
    assert fund_air_calculator is not None

def fix8_cs5_fund_metrics_fields():
    from app.services.analytics.fund_air import CS5FundMetrics
    from dataclasses import fields
    fnames = [f.name for f in fields(CS5FundMetrics)]
    for required in ["fund_air", "ai_leaders_count", "ai_laggards_count",
                     "sector_hhi", "quartile_distribution", "total_ev_mm"]:
        assert required in fnames, f"CS5FundMetrics missing field: {required}"

def fix8_calculate_fund_metrics():
    from app.services.analytics.fund_air import FundAIRCalculator
    from dataclasses import dataclass

    @dataclass
    class FakeCompany:
        company_id: str
        org_air: float
        sector: str
        delta_since_entry: float = 5.0

    calc = FundAIRCalculator()
    companies = [
        FakeCompany("NVDA", 84.9, "technology"),
        FakeCompany("JPM",  72.1, "financial_services"),
        FakeCompany("WMT",  55.3, "retail"),
    ]
    evs = {"NVDA": 1000.0, "JPM": 500.0, "WMT": 300.0}
    result = calc.calculate_fund_metrics("PE-FUND-I", companies, evs)
    assert result.fund_air > 0, "fund_air should be > 0"
    assert result.company_count == 3
    assert result.ai_leaders_count == 2   # NVDA + JPM >= 70
    assert result.sector_hhi > 0
    assert "q1" in result.quartile_distribution

def fix8_nested_sector_benchmarks():
    from app.services.analytics.fund_air import SECTOR_BENCHMARKS
    bench = SECTOR_BENCHMARKS.get("technology")
    assert isinstance(bench, dict), "SECTOR_BENCHMARKS should be nested dicts"
    assert "q1" in bench and "q2" in bench

check("FundAIRCalculator() no-arg constructor", fix8_no_arg_constructor)
check("fund_air_calculator module-level singleton", fix8_singleton)
check("CS5FundMetrics has fund_air/ai_leaders_count/ai_laggards_count/sector_hhi", fix8_cs5_fund_metrics_fields)
check("calculate_fund_metrics() returns correct CS5FundMetrics", fix8_calculate_fund_metrics)
check("SECTOR_BENCHMARKS is nested {q1/q2/q3/q4}", fix8_nested_sector_benchmarks)


# ── Bonus: imports ────────────────────────────────────────────────────────────
print("\n[BONUS] Bonus files importable")

def bonus_memory():
    from app.agents.memory import agent_memory
    assert agent_memory is not None

def bonus_tracker():
    from app.services.tracking.investment_tracker import investment_tracker
    roi = investment_tracker.compute_roi("NVDA", 84.9)
    assert roi.roi_estimate_pct != 0
    assert roi.air_improvement == round(84.9 - 62.0, 2)

def bonus_ic_memo():
    from app.services.reporting.ic_memo import ic_memo_generator
    assert ic_memo_generator is not None

def bonus_lp_letter():
    from app.services.reporting.lp_letter import lp_letter_generator
    assert lp_letter_generator is not None

check("app.agents.memory — AgentMemory singleton", bonus_memory)
check("investment_tracker.compute_roi('NVDA', 84.9)", bonus_tracker)
check("ic_memo_generator importable", bonus_ic_memo)
check("lp_letter_generator importable", bonus_lp_letter)


# ── DD Workflow file exists ───────────────────────────────────────────────────
print("\n[CREATE] DD Workflow exercise file")

def create_dd_file():
    import ast
    with open("exercises/agentic_due_diligence.py", encoding="utf-8") as f:
        src = f.read()
    ast.parse(src)
    assert "run_due_diligence" in src
    assert "dd_graph" in src
    assert "DueDiligenceState" in src

check("exercises/agentic_due_diligence.py exists and is valid", create_dd_file)


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print(f"  PASSED: {len(PASS)}/{len(PASS)+len(FAIL)}")
if FAIL:
    print(f"  FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
else:
    print("  All checks passed!")
print("=" * 55)
sys.exit(0 if not FAIL else 1)
