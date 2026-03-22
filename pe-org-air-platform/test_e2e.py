"""
CS5 End-to-End Tests — All Deliverables
Run: python test_e2e.py
Requires: uvicorn app.main:app --reload  (Terminal 1)
"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.abspath("."))
# Ensure UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError for ✓ → ✗ chars)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL, SKIP = [], [], []

def check(name, fn):
    try:
        asyncio.run(fn()) if asyncio.iscoroutinefunction(fn) else fn()
        PASS.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"  FAIL  {name}")
        print(f"        {e}")

def skip(name, reason):
    SKIP.append(name)
    print(f"  SKIP  {name}  ({reason})")


# ===========================================================================
# 1. MCP Tools — all 6
# ===========================================================================
print("\n" + "="*60)
print("[1] MCP TOOLS")
print("="*60)

async def t_tool1():
    from app.mcp.server import call_tool
    r = await call_tool("calculate_org_air_score", {"company_id": "NVDA"})
    assert "org_air" in r, f"missing org_air: {r}"
    print(f"        NVDA org_air={r['org_air']:.2f}  vr={r.get('vr_score',0):.2f}  hr={r.get('hr_score',0):.2f}")

async def t_tool2():
    from app.mcp.server import call_tool
    r = await call_tool("get_company_evidence", {"company_id": "NVDA", "limit": 3})
    assert "evidence" in r
    print(f"        evidence count={len(r['evidence'])}")

async def t_tool3():
    from app.mcp.server import call_tool
    r = await call_tool("generate_justification", {"company_id": "NVDA", "dimension": "talent"})
    print(f"        dimension={r.get('dimension')}  score={r.get('score')}  level={r.get('level')}")

async def t_tool4():
    from app.mcp.server import call_tool
    r = await call_tool("project_ebitda_impact", {
        "company_id": "NVDA", "entry_score": 62.0, "target_score": 85.0, "h_r_score": 93.0
    })
    assert "scenarios" in r
    print(f"        base={r['scenarios'].get('base')}  risk_adj={r.get('risk_adjusted')}")

async def t_tool5():
    from app.mcp.server import call_tool
    r = await call_tool("run_gap_analysis", {"company_id": "NVDA", "target_org_air": 90.0})
    print(f"        gap result keys: {list(r.keys())[:4]}")

async def t_tool6():
    from app.mcp.server import call_tool
    r = await call_tool("get_portfolio_summary", {"fund_id": "PE-FUND-I"})
    companies = r.get("companies", [])
    print(f"        fund_air={r.get('fund_air')}  companies={len(companies)}")
    for c in companies:
        print(f"          {c.get('ticker'):6s}  org_air={c.get('org_air', 0):.1f}")

check("Tool 1 — calculate_org_air_score(NVDA)", t_tool1)
check("Tool 2 — get_company_evidence(NVDA)", t_tool2)
check("Tool 3 — generate_justification(NVDA/talent)", t_tool3)
check("Tool 4 — project_ebitda_impact(NVDA)", t_tool4)
check("Tool 5 — run_gap_analysis(NVDA)", t_tool5)
check("Tool 6 — get_portfolio_summary(PE-FUND-I)", t_tool6)


# ===========================================================================
# 2. HITL — Grader-style patch test
# ===========================================================================
print("\n" + "="*60)
print("[2] HITL — Human-In-The-Loop")
print("="*60)

async def t_hitl_high_score():
    """Score > 85 must set requires_approval=True."""
    from unittest.mock import MagicMock
    from app.agents.specialists import ScoringAgent, MCPToolCaller

    mock_caller = MagicMock(spec=MCPToolCaller)
    mock_caller.calculate_org_air_score.return_value = {
        "org_air": 91.0, "vr_score": 88.0, "hr_score": 92.0
    }
    agent = ScoringAgent(tool_caller=mock_caller)
    result = agent({"company_id": "NVDA", "assessment_type": "full", "messages": []})
    assert result["requires_approval"] is True, f"Expected HITL for score=91.0, got: {result}"
    print(f"        score=91.0 → requires_approval={result['requires_approval']}  ✓")

async def t_hitl_low_score():
    """Score < 40 must also trigger HITL."""
    from unittest.mock import MagicMock
    from app.agents.specialists import ScoringAgent, MCPToolCaller

    mock_caller = MagicMock(spec=MCPToolCaller)
    mock_caller.calculate_org_air_score.return_value = {
        "org_air": 32.0, "vr_score": 28.0, "hr_score": 35.0
    }
    agent = ScoringAgent(tool_caller=mock_caller)
    result = agent({"company_id": "DG", "assessment_type": "full", "messages": []})
    assert result["requires_approval"] is True, f"Expected HITL for score=32.0"
    print(f"        score=32.0 → requires_approval={result['requires_approval']}  ✓")

async def t_hitl_normal_score():
    """Score in [40, 85] must NOT trigger HITL."""
    from unittest.mock import MagicMock
    from app.agents.specialists import ScoringAgent, MCPToolCaller

    mock_caller = MagicMock(spec=MCPToolCaller)
    mock_caller.calculate_org_air_score.return_value = {
        "org_air": 65.0, "vr_score": 60.0, "hr_score": 70.0
    }
    agent = ScoringAgent(tool_caller=mock_caller)
    result = agent({"company_id": "WMT", "assessment_type": "full", "messages": []})
    assert result["requires_approval"] is False, f"Expected no HITL for score=65.0"
    print(f"        score=65.0 → requires_approval={result['requires_approval']}  ✓")

async def t_hitl_auto_approve():
    """Supervisor hitl_approval_node must auto-approve with 'exercise_auto_approve'."""
    from app.agents.supervisor import hitl_approval_node
    state = {
        "company_id": "NVDA",
        "requires_approval": True,
        "approval_reason": "Score 91.0 outside range",
        "approval_status": "pending",
        "approved_by": None,
        "messages": [],
    }
    result = hitl_approval_node(state)
    assert result.get("approval_status") == "approved", f"Expected approved: {result}"
    assert result.get("approved_by") == "exercise_auto_approve", f"Wrong approver: {result}"
    print(f"        approval_status={result['approval_status']}  approved_by={result['approved_by']}  ✓")

async def t_hitl_real_score():
    """Run HITL check against real NVDA score from FastAPI."""
    from app.mcp.server import call_tool
    r = await call_tool("calculate_org_air_score", {"company_id": "NVDA"})
    org_air = r.get("org_air", 0.0)
    expected_hitl = org_air > 85 or org_air < 40
    print(f"        NVDA real org_air={org_air:.2f}  HITL expected={expected_hitl}")
    # Just informational — not a hard assert since score depends on data

check("HITL triggers for score > 85 (mock=91.0)", t_hitl_high_score)
check("HITL triggers for score < 40 (mock=32.0)", t_hitl_low_score)
check("HITL does NOT trigger for score in [40,85] (mock=65.0)", t_hitl_normal_score)
check("hitl_approval_node auto-approves with 'exercise_auto_approve'", t_hitl_auto_approve)
check("NVDA real score HITL check (informational)", t_hitl_real_score)


# ===========================================================================
# 3. LangGraph State — structure checks
# ===========================================================================
print("\n" + "="*60)
print("[3] LANGGRAPH STATE")
print("="*60)

def t_state_fields():
    from app.agents.state import DueDiligenceState, AgentMessage
    import typing
    hints = typing.get_type_hints(DueDiligenceState)
    required = ["company_id", "messages", "scoring_result", "requires_approval",
                "approval_status", "approved_by", "sec_analysis", "value_creation_plan"]
    for f in required:
        assert f in hints, f"Missing field: {f}"
    print(f"        all required fields present: {required[:4]}...")

def t_state_messages_reducer():
    """messages must use operator.add reducer (append-only)."""
    import operator, typing
    from app.agents.state import DueDiligenceState
    hints = typing.get_type_hints(DueDiligenceState, include_extras=True)
    msg_hint = str(hints.get("messages", ""))
    assert "add" in msg_hint.lower() or "Annotated" in msg_hint, \
        "messages field should use Annotated[List, operator.add]"
    print(f"        messages reducer: {msg_hint[:80]}")

def t_graph_exists():
    from app.agents.supervisor import dd_graph, create_due_diligence_graph
    assert dd_graph is not None
    print(f"        dd_graph type: {type(dd_graph).__name__}")

check("DueDiligenceState has all required fields", t_state_fields)
check("messages field uses operator.add reducer", t_state_messages_reducer)
check("dd_graph module-level instance exists", t_graph_exists)


# ===========================================================================
# 4. Assessment History
# ===========================================================================
print("\n" + "="*60)
print("[4] ASSESSMENT HISTORY")
print("="*60)

def t_history_record():
    """record_assessment must call cs3_client.get_assessment."""
    from unittest.mock import MagicMock
    from app.services.tracking.history_service import AssessmentHistoryService

    mock_cs3 = MagicMock()
    mock_assessment = MagicMock()
    mock_assessment.org_air_score = 84.9
    mock_assessment.valuation_risk = 80.7
    mock_assessment.human_capital_risk = 93.2
    mock_assessment.synergy = 10.0
    mock_assessment.dimension_scores = {}
    mock_cs3.get_assessment.return_value = mock_assessment

    mock_cs1 = MagicMock()
    svc = AssessmentHistoryService(cs1_client=mock_cs1, cs3_client=mock_cs3)
    snap = svc.record_assessment("NVDA", assessor_id="analyst@pe.com")
    assert snap.org_air_score == 84.9
    assert snap.assessor_id == "analyst@pe.com"
    print(f"        snapshot org_air={snap.org_air_score}  assessor_id={snap.assessor_id}")

def t_history_trend():
    from unittest.mock import MagicMock
    from app.services.tracking.history_service import AssessmentHistoryService

    mock_cs3 = MagicMock()
    mock_cs1 = MagicMock()

    def make_assessment(score):
        a = MagicMock()
        a.org_air_score = score
        a.valuation_risk = score * 0.9
        a.human_capital_risk = score * 1.1
        a.synergy = 10.0
        a.dimension_scores = {}
        return a

    mock_cs3.get_assessment.side_effect = [
        make_assessment(70.0), make_assessment(75.0), make_assessment(80.0)
    ]
    svc = AssessmentHistoryService(cs1_client=mock_cs1, cs3_client=mock_cs3)
    svc.record_assessment("NVDA")
    svc.record_assessment("NVDA")
    svc.record_assessment("NVDA")
    trend = svc.calculate_trend("NVDA")
    assert trend.direction == "improving"
    assert trend.current_score == 80.0
    print(f"        trend direction={trend.direction}  delta_30d={trend.delta_30d}")

check("record_assessment stores snapshot with assessor_id", t_history_record)
check("calculate_trend returns 'improving' after 3 rising snapshots", t_history_trend)


# ===========================================================================
# 5. Fund-AI-R
# ===========================================================================
print("\n" + "="*60)
print("[5] FUND-AI-R")
print("="*60)

def t_fund_air_weighted():
    from app.services.analytics.fund_air import FundAIRCalculator
    from dataclasses import dataclass

    @dataclass
    class Co:
        company_id: str
        org_air: float
        sector: str
        delta_since_entry: float = 5.0

    calc = FundAIRCalculator()
    companies = [Co("NVDA", 84.9, "technology"), Co("JPM", 72.1, "financial_services"),
                 Co("WMT", 55.3, "retail"), Co("GE", 60.2, "manufacturing"),
                 Co("DG", 48.7, "retail")]
    evs = {"NVDA": 1200.0, "JPM": 450.0, "WMT": 380.0, "GE": 220.0, "DG": 90.0}
    result = calc.calculate_fund_metrics("PE-FUND-I", companies, evs)
    print(f"        fund_air={result.fund_air:.2f}  leaders={result.ai_leaders_count}  laggards={result.ai_laggards_count}")
    print(f"        quartiles={result.quartile_distribution}  hhi={result.sector_hhi:.4f}")
    assert result.fund_air > 0
    assert result.ai_leaders_count == 2  # NVDA + JPM >= 70
    assert result.ai_laggards_count == 1  # DG < 50

check("calculate_fund_metrics EV-weighted with 5 companies", t_fund_air_weighted)


# ===========================================================================
# 6. EvidenceAgent — 3 dimensions
# ===========================================================================
print("\n" + "="*60)
print("[6] EVIDENCE AGENT — 3 dimensions")
print("="*60)

def t_evidence_3dims():
    from unittest.mock import MagicMock, patch
    from app.agents.specialists import EvidenceAgent, MCPToolCaller

    call_log = []

    def fake_get_evidence(company_id, dimension=None, limit=5):
        call_log.append(dimension)
        return {"evidence": [{"content": f"fake {dimension}", "source_type": "sec"}]}

    mock_caller = MagicMock(spec=MCPToolCaller)
    mock_caller.get_company_evidence.side_effect = fake_get_evidence

    mock_router = MagicMock()
    mock_router.complete_sync.return_value = "Evidence summary."

    agent = EvidenceAgent(router=mock_router, tool_caller=mock_caller)
    result = agent({"company_id": "NVDA", "assessment_type": "full", "messages": []})

    dims_called = [d for d in call_log if d is not None]
    print(f"        dimensions fetched: {dims_called}")
    assert "data_infrastructure" in dims_called, "data_infrastructure not fetched"
    assert "talent" in dims_called, "talent not fetched"
    assert "use_case_portfolio" in dims_called, "use_case_portfolio not fetched"
    assert len(dims_called) == 3

check("EvidenceAgent fetches exactly 3 dimensions", t_evidence_3dims)


# ===========================================================================
# 7. MCP Grader test — cs3_client.get_assessment patching
# ===========================================================================
print("\n" + "="*60)
print("[7] GRADER TEST — cs3_client patch")
print("="*60)

async def t_grader_patch():
    """Simulates the exact grader test from CS5 pg 34."""
    from unittest.mock import patch, MagicMock
    from app.mcp.server import call_tool, _cs3

    # Initialize cs3_client so patch.object has a real object
    _cs3()
    from app.mcp.server import cs3_client

    mock_assessment = MagicMock()
    mock_assessment.org_air_score = 84.94
    mock_assessment.valuation_risk = 80.77
    mock_assessment.human_capital_risk = 93.19
    mock_assessment.synergy = 10.0
    mock_assessment.confidence_interval = (79.0, 90.0)
    mock_assessment.dimension_scores = {}

    with patch.object(cs3_client, "get_assessment", return_value=mock_assessment) as mock:
        result = await call_tool("calculate_org_air_score", {"company_id": "NVDA"})
        mock.assert_called_once_with("NVDA")
        assert result["org_air"] == 84.94
        print(f"        patch worked: org_air={result['org_air']}  called_with='NVDA'  ✓")

check("cs3_client.get_assessment patchable (grader test CS5 pg 34)", t_grader_patch)


# ===========================================================================
# 8. DD Workflow — full run (optional, slow)
# ===========================================================================
print("\n" + "="*60)
print("[8] DD WORKFLOW — full run for NVDA")
print("="*60)
print("  (This runs all 4 agents + supervisor. May take 30-60s with LLM calls.)")

async def t_dd_full():
    from app.agents.supervisor import dd_graph
    from app.agents.state import DueDiligenceState
    from datetime import datetime, timezone

    initial: DueDiligenceState = {
        "company_id": "NVDA",
        "assessment_type": "screening",   # screening skips ValueCreationAgent
        "requested_by": "analyst",
        "messages": [],
        "sec_analysis": None, "talent_analysis": None,
        "scoring_result": None, "evidence_justifications": None,
        "value_creation_plan": None, "next_agent": None,
        "requires_approval": False, "approval_reason": None,
        "approval_status": None, "approved_by": None,
        "started_at": datetime.now(timezone.utc), "completed_at": None,
        "total_tokens": 0, "error": None,
    }
    config = {"configurable": {"thread_id": "e2e-test-nvda"}}
    result = await dd_graph.ainvoke(initial, config)

    assert result.get("scoring_result") is not None, "scoring_result missing"
    org_air = result["scoring_result"].get("org_air", 0)
    hitl = result.get("requires_approval", False)
    status = result.get("approval_status")
    msgs = len(result.get("messages", []))

    print(f"        org_air={org_air:.2f}  HITL={hitl}  approval={status}  messages={msgs}")

    if hitl:
        assert status == "approved", f"HITL triggered but not auto-approved: {status}"
        assert result.get("approved_by") == "exercise_auto_approve"
        print(f"        HITL auto-approved by: {result.get('approved_by')}  ✓")

check("Full DD workflow (screening) for NVDA", t_dd_full)


# ===========================================================================
# Summary
# ===========================================================================
total = len(PASS) + len(FAIL) + len(SKIP)
print("\n" + "="*60)
print(f"  PASSED : {len(PASS)}/{total - len(SKIP)}")
print(f"  FAILED : {len(FAIL)}")
print(f"  SKIPPED: {len(SKIP)}")
if FAIL:
    print("\n  Failed tests:")
    for f in FAIL:
        print(f"    ✗ {f}")
print("="*60)
sys.exit(0 if not FAIL else 1)
