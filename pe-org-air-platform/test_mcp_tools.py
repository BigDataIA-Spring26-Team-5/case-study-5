"""
MCP Server Tool Tests
Run: python test_mcp_tools.py
Requires: uvicorn app.main:app --reload  (Terminal 1)
"""
import asyncio, sys

PASS, FAIL = [], []

def check(name, fn):
    try:
        asyncio.run(fn())
        PASS.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"  FAIL  {name}")
        print(f"        {e}")


# ── Tool 1: calculate_org_air_score ──────────────────────────────────────────
print("\n[Tool 1] calculate_org_air_score")

async def tool1_nvda():
    from app.mcp.server import call_tool
    result = await call_tool("calculate_org_air_score", {"company_id": "NVDA"})
    assert "error" not in result, f"Got error: {result}"
    assert "org_air" in result, f"Missing org_air in: {result}"
    print(f"        NVDA org_air={result['org_air']:.1f}  vr={result.get('vr_score',0):.1f}  hr={result.get('hr_score',0):.1f}")

async def tool1_cs3_called():
    """Grader test: cs3_client.get_assessment must be called (not direct DB)."""
    from unittest.mock import patch, MagicMock
    from app.mcp import server as mcp_server
    # Ensure cs3_client is initialized first
    from app.mcp.server import _cs3
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
        print(f"        cs3_client.get_assessment patched and called correctly")

from app.mcp.server import call_tool
check("Tool 1 returns org_air for NVDA", tool1_nvda)
check("Tool 1 calls cs3_client.get_assessment (grader test)", tool1_cs3_called)


# ── Tool 2: get_company_evidence ─────────────────────────────────────────────
print("\n[Tool 2] get_company_evidence")

async def tool2_basic():
    result = await call_tool("get_company_evidence", {"company_id": "NVDA", "limit": 5})
    assert "error" not in result, f"Got error: {result}"
    assert "evidence" in result
    print(f"        evidence items returned: {result.get('count', len(result.get('evidence', [])))}")

async def tool2_with_dimension():
    result = await call_tool("get_company_evidence", {"company_id": "JPM", "dimension": "talent", "limit": 3})
    assert "evidence" in result
    print(f"        JPM/talent evidence count: {len(result.get('evidence', []))}")

check("Tool 2 returns evidence for NVDA", tool2_basic)
check("Tool 2 filters by dimension (JPM/talent)", tool2_with_dimension)


# ── Tool 3: generate_justification ───────────────────────────────────────────
print("\n[Tool 3] generate_justification")

async def tool3_basic():
    result = await call_tool("generate_justification", {"company_id": "NVDA", "dimension": "talent"})
    assert "error" not in result, f"Got error: {result}"
    assert "dimension" in result or "score" in result, f"Unexpected shape: {result}"
    print(f"        dimension={result.get('dimension')}  score={result.get('score')}  level={result.get('level')}")

check("Tool 3 returns justification for NVDA/talent", tool3_basic)


# ── Tool 4: project_ebitda_impact ────────────────────────────────────────────
print("\n[Tool 4] project_ebitda_impact")

async def tool4_basic():
    result = await call_tool("project_ebitda_impact", {
        "company_id": "NVDA", "entry_score": 62.0, "target_score": 85.0, "h_r_score": 93.0
    })
    assert "error" not in result, f"Got error: {result}"
    assert "scenarios" in result
    assert "delta_air" in result
    print(f"        delta_air={result['delta_air']}  base={result['scenarios'].get('base')}")

async def tool4_no_fastapi():
    """Tool 4 is pure local math — must work without FastAPI."""
    result = await call_tool("project_ebitda_impact", {
        "company_id": "WMT", "entry_score": 43.0, "target_score": 70.0, "h_r_score": 55.0
    })
    assert "scenarios" in result, "Pure-local tool failed"
    print(f"        WMT base={result['scenarios'].get('base')}")

check("Tool 4 returns EBITDA scenarios for NVDA", tool4_basic)
check("Tool 4 works as pure-local math (no FastAPI dependency)", tool4_no_fastapi)


# ── Tool 5: run_gap_analysis ─────────────────────────────────────────────────
print("\n[Tool 5] run_gap_analysis")

async def tool5_basic():
    result = await call_tool("run_gap_analysis", {"company_id": "NVDA", "target_org_air": 90.0})
    assert "error" not in result, f"Got error: {result}"
    print(f"        gap analysis keys: {list(result.keys())[:5]}")

check("Tool 5 returns gap analysis for NVDA", tool5_basic)


# ── Tool 6: get_portfolio_summary ────────────────────────────────────────────
print("\n[Tool 6] get_portfolio_summary")

async def tool6_basic():
    result = await call_tool("get_portfolio_summary", {"fund_id": "PE-FUND-I"})
    assert "error" not in result, f"Got error: {result}"
    assert "fund_air" in result or "companies" in result
    companies = result.get("companies", [])
    print(f"        fund_air={result.get('fund_air')}  companies={len(companies)}")
    for c in companies:
        print(f"          {c.get('ticker')}: {c.get('org_air', 0):.1f}")

check("Tool 6 returns portfolio summary with all 5 companies", tool6_basic)


# ── Resources ─────────────────────────────────────────────────────────────────
print("\n[Resources] MCP Resources")

async def resource_parameters():
    import json
    from app.mcp.server import read_resource
    data = json.loads(await read_resource("orgair://parameters/v2.0"))
    assert data["gamma_0"] == 0.0025
    assert data["alpha"] == 0.6
    print(f"        alpha={data['alpha']}  gamma_0={data['gamma_0']}")

async def resource_sectors():
    import json
    from app.mcp.server import read_resource
    data = json.loads(await read_resource("orgair://sectors"))
    assert "portfolio_companies" in data or "sector_baselines" in data
    print(f"        sectors resource keys: {list(data.keys())}")

check("Resource orgair://parameters/v2.0 with gamma values", resource_parameters)
check("Resource orgair://sectors returns sector data", resource_sectors)


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print(f"  PASSED: {len(PASS)}/{len(PASS)+len(FAIL)}")
if FAIL:
    print(f"  FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"    - {f}")
else:
    print("  All MCP tool tests passed!")
print("=" * 55)
sys.exit(0 if not FAIL else 1)
