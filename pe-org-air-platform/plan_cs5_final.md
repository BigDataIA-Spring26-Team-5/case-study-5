# CS5 Final Implementation Plan — CS5 PDF Aligned
> **Source of truth:** CS5 v4.0 PDF only. No deviations.
> **Goal:** 100 base pts + 20 bonus pts = 120 pts total.
> **Due:** March 27, 2026 at 3:59 PM

---

## Execution Status

| # | Task | Pts | File | Status |
|---|------|-----|------|--------|
| 1 | Task 9.1 Portfolio Data Service | 8 | `app/services/portfolio_data_service.py` | FIX |
| 2 | Task 9.2 MCP Server Core | 12 | `app/mcp/server.py` | FIX |
| 3 | Task 9.3 MCP Resources & Prompts | 8 | `app/mcp/server.py` | FIX (minor) |
| 4 | Task 9.4 Assessment History | 6 | `app/services/tracking/history_service.py` | FIX |
| 5 | Task 9.5 Evidence Display | 6 | `streamlit/components/evidence_display.py` | FIX |
| 6 | Task 9.6 Portfolio Dashboard | 10 | `streamlit/cs5_app.py` | FIX |
| 7 | Task 10.1 LangGraph State | 8 | `app/agents/state.py` | DONE ✅ |
| 8 | Task 10.2 Specialist Agents | 12 | `app/agents/specialists.py` | FIX |
| 9 | Task 10.3 Supervisor + HITL | 10 | `app/agents/supervisor.py` | DONE ✅ |
| 10 | Task 10.4 DD Workflow Exercise | 10 | `exercises/agentic_due_diligence.py` | CREATE |
| 11 | Task 10.5 Fund-AI-R | 5 | `app/services/analytics/fund_air.py` | FIX |
| 12 | Task 10.6 Prometheus Metrics | 5 | `app/services/observability/metrics.py` | DONE ✅ |
| B1 | Bonus: Mem0 Semantic Memory | +5 | `app/agents/memory.py` | CREATE |
| B2 | Bonus: Investment Tracker with ROI | +5 | `app/services/tracking/investment_tracker.py` | CREATE |
| B3 | Bonus: IC Memo Generator | +5 | `app/services/reporting/ic_memo.py` | CREATE |
| B4 | Bonus: LP Letter Generator | +5 | `app/services/reporting/lp_letter.py` | CREATE |

---

## PATH MAPPING (our `app/` → CS5 submission `src/`)

CS5 submission expects paths under `src/`. Our codebase lives under `app/`. Map is:

| CS5 expects | Our file |
|-------------|----------|
| `services/integration/portfolio_data_service.py` | `app/services/portfolio_data_service.py` |
| `services/tracking/assessment_history.py` | `app/services/tracking/history_service.py` |
| `services/analytics/fund_air.py` | `app/services/analytics/fund_air.py` |
| `services/observability/metrics.py` | `app/services/observability/metrics.py` |
| `mcp/server.py` | `app/mcp/server.py` |
| `agents/state.py` | `app/agents/state.py` |
| `agents/specialists.py` | `app/agents/specialists.py` |
| `agents/supervisor.py` | `app/agents/supervisor.py` |
| `dashboard/app.py` | `streamlit/cs5_app.py` |
| `dashboard/components/evidence_display.py` | `streamlit/components/evidence_display.py` |
| `exercises/agentic_due_diligence.py` | `exercises/agentic_due_diligence.py` |

---

## DONE — No changes needed

### Task 10.1 — LangGraph State (`app/agents/state.py`) ✅
Fully aligned with CS5:
- `AgentMessage.role: Literal["user", "assistant", "system", "tool"]` ✅
- `started_at: datetime` (not str) ✅
- All `DueDiligenceState` fields present ✅

### Task 10.3 — Supervisor + HITL (`app/agents/supervisor.py`) ✅
Fully aligned with CS5:
- Routing order: HITL check → sec_analyst → scorer → evidence_agent → value_creator → complete ✅
- `hitl_approval_node` auto-approves with `"exercise_auto_approve"` ✅
- `MemorySaver()` checkpointer ✅
- `dd_graph = create_due_diligence_graph()` at module level ✅

### Task 10.6 — Prometheus Metrics (`app/services/observability/metrics.py`) ✅
All 4 counters/histograms and 3 decorators match CS5 exactly ✅

---

## FIX — Changes required in existing files

---

### FIX 1 — Task 9.1: Portfolio Data Service (`app/services/portfolio_data_service.py`)

**Problem:** Constructor takes client objects. CS5 requires URL strings. Return type is `CompanyView` not `List[PortfolioCompanyView]`.

**CS5 spec (Task 9.1, pg 7-8):**
```python
@dataclass
class PortfolioCompanyView:
    company_id: str
    ticker: str
    name: str
    sector: str
    org_air: float
    vr_score: float
    hr_score: float
    synergy_score: float
    dimension_scores: Dict[str, float]
    confidence_interval: tuple
    entry_org_air: float
    delta_since_entry: float
    evidence_count: int

class PortfolioDataService:
    def __init__(
        self,
        cs1_url: str = "http://localhost:8000",
        cs2_url: str = "http://localhost:8001",
        cs3_url: str = "http://localhost:8002",
    ):
        self.cs1 = CS1Client(base_url=cs1_url)
        self.cs2 = CS2Client(base_url=cs2_url)
        self.cs3 = CS3Client(base_url=cs3_url)
        self.cs4 = CS4Client()

    async def get_portfolio_view(self, fund_id: str) -> List[PortfolioCompanyView]:
        companies = await self.cs1.get_portfolio_companies(fund_id)
        views = []
        for company in companies:
            assessment = await self.cs3.get_assessment(company.ticker)
            evidence = await self.cs2.get_evidence(company.ticker)
            entry_score = await self._get_entry_score(company.company_id)
            views.append(PortfolioCompanyView(...))
        return views

    async def _get_entry_score(self, company_id: str) -> float:
        return 45.0  # placeholder

portfolio_data_service = PortfolioDataService()
```

**What to change:**
- Add `PortfolioCompanyView` dataclass with exact fields above
- Change constructor to take URL strings and create clients internally
- Add `get_portfolio_view(fund_id) -> List[PortfolioCompanyView]` that calls cs1 → cs3 → cs2
- Add `_get_entry_score(company_id) -> float` stub returning 45.0
- Keep existing methods — they are used internally by other services
- Add `portfolio_data_service = PortfolioDataService()` singleton at bottom

---

### FIX 2 — Task 9.2: MCP Server Core (`app/mcp/server.py`)

**Problem:** `calculate_org_air_score` calls `CompositeScoringRepository` directly instead of `cs3_client.get_assessment()`. Grader test patches `cs3_client` — if not called, test fails.

**CS5 grader test (pg 34):**
```python
from mcp.server import call_tool, cs3_client
with patch.object(cs3_client, 'get_assessment', new_callable=AsyncMock) as mock:
    result = await call_tool("calculate_org_air_score", {"company_id": "NVDA"})
    mock.assert_called_once_with("NVDA")
```

**CS5 requires (pg 11):**
```python
# Module-level clients (not lazy hidden inside functions)
cs2_client = CS2Client()
cs3_client = CS3Client()
cs4_client = CS4Client()

# Tool 1: calls cs3_client.get_assessment()
assessment = await cs3_client.get_assessment(arguments["company_id"])

# Tool 2: calls cs2_client.get_evidence()
evidence = await cs2_client.get_evidence(
    company_id=arguments["company_id"],
    dimension=arguments.get("dimension", "all"),
    limit=arguments.get("limit", 10),
)

# Tool 3: calls cs4_client.generate_justification()
justification = await cs4_client.generate_justification(
    company_id=arguments["company_id"],
    dimension=Dimension(arguments["dimension"]),
)

# Tool 6: calls portfolio_data_service.get_portfolio_view()
portfolio = await portfolio_data_service.get_portfolio_view(arguments["fund_id"])
```

**What to change:**
- Expose `cs3_client`, `cs2_client`, `cs4_client` as **module-level variables** (not hidden inside `_cs3()` functions) so the grader can patch them
- `calculate_org_air_score` handler: replace `CompositeScoringRepository._query()` with `_cs3().get_assessment(ticker)` (or module-level `cs3_client.get_assessment()`)
- `get_company_evidence` handler: replace FastAPI HTTP call with `_cs2().get_evidence()` call directly
- `generate_justification` handler: replace FastAPI HTTP call with `_cs4().generate_justification()` call
- `get_portfolio_summary` handler: replace CS3_PORTFOLIO iteration with `portfolio_data_service.get_portfolio_view(fund_id)` call
- Keep lazy singleton pattern internally for startup safety — just expose the result as module-level refs after first call, OR expose them upfront accepting the import cost

**Exact module-level change needed:**
```python
# At module level after server = Server(...)
from app.services.integration.cs3_client import CS3Client
from app.services.integration.cs2_client import CS2Client
from app.services.integration.cs4_client import CS4Client
from app.services.portfolio_data_service import portfolio_data_service

cs3_client = CS3Client()
cs2_client = CS2Client()
cs4_client = CS4Client()
```

---

### FIX 3 — Task 9.3: MCP Resources (`app/mcp/server.py`)

**Problem:** `orgair://parameters/v2.0` missing gamma values CS5 specifies.

**CS5 requires (pg 13):**
```python
return json.dumps({
    "version": "2.0",
    "alpha": 0.60, "beta": 0.12,
    "gamma_0": 0.0025, "gamma_1": 0.05, "gamma_2": 0.025, "gamma_3": 0.01,
})
```

**What to change:**
- In `read_resource()` handler for `orgair://parameters/v2.0`, add `gamma_0`, `gamma_1`, `gamma_2`, `gamma_3` fields
- Keep existing fields (lambda, delta, dimension weights) — adding gamma doesn't break anything

---

### FIX 4 — Task 9.4: Assessment History (`app/services/tracking/history_service.py`)

**Problem:** Constructor takes only `cs3_client`. CS5 requires `(cs1_client, cs3_client)`. Missing `create_history_service()` factory.

**CS5 requires (pg 14-16):**
```python
class AssessmentHistoryService:
    def __init__(self, cs1_client: CS1Client, cs3_client: CS3Client):
        self.cs1 = cs1_client
        self.cs3 = cs3_client
        self._cache: Dict[str, List[AssessmentSnapshot]] = {}

    async def record_assessment(
        self, company_id: str, assessor_id: str, assessment_type: str = "full"
    ) -> AssessmentSnapshot: ...

    async def get_history(self, company_id: str, days: int = 365) -> List[AssessmentSnapshot]: ...

    async def calculate_trend(self, company_id: str) -> AssessmentTrend: ...

def create_history_service(cs1: CS1Client, cs3: CS3Client) -> AssessmentHistoryService:
    return AssessmentHistoryService(cs1, cs3)
```

**What to change:**
- Add `cs1_client` as first parameter in `__init__`
- Store as `self.cs1 = cs1_client`
- Add `create_history_service(cs1, cs3)` factory function at bottom of file
- `AssessmentSnapshot` fields: rename `assessor` → `assessor_id` to match CS5
- Update `lifespan.py` to pass both clients when creating `AssessmentHistoryService`

---

### FIX 5 — Task 9.5: Evidence Display (`streamlit/components/evidence_display.py`)

**Problem:** `render_company_evidence_panel(ticker)` fetches data internally. CS5 requires it to receive pre-fetched `Dict[str, ScoreJustification]`.

**CS5 requires (pg 17-19):**
```python
def render_evidence_card(justification: ScoreJustification) -> None:
    """Takes ScoreJustification object (not dict)."""
    color = LEVEL_COLORS.get(justification.level, "#6b7280")
    # access .level, .score, .evidence_strength, .rubric_criteria,
    # .supporting_evidence, .gaps_identified as attributes

def render_company_evidence_panel(
    company_id: str,
    justifications: Dict[str, ScoreJustification],
) -> None:
    """Receives pre-fetched justifications — does NOT fetch internally."""
    st.header(f"Evidence Analysis: {company_id}")
    # summary metrics row
    # st.tabs for 7 dimensions
    # calls render_evidence_card(just) per tab

def render_evidence_summary_table(
    justifications: Dict[str, ScoreJustification],
) -> None:
    """Renders compact dataframe with Level column color-coded."""
```

**What to change:**
- Keep all existing `fetch_*` helper functions — they are fine as utilities
- Change `render_evidence_card(justification: dict)` to work with dict keys (`.get()`) OR add `ScoreJustification` import — dict access is acceptable since CS5 itself accesses `justification.level` etc.
- Change `render_company_evidence_panel(ticker)` signature to `render_company_evidence_panel(company_id: str, justifications: dict)` — receives pre-fetched data, does NOT call fetch internally
- The dashboard page that uses this component will call `fetch_all_justifications(ticker)` first, then pass result to `render_company_evidence_panel`

---

### FIX 6 — Task 9.6: Portfolio Dashboard (`streamlit/cs5_app.py`)

**Problem:** Loads data via `GET /api/v1/assessments/{ticker}` per company. CS5 requires `portfolio_data_service.get_portfolio_view(fund_id)`. Metrics row shows Leaders/Laggards — CS5 shows Avg V^R and Avg Delta.

**CS5 requires (pg 20-21):**
```python
from services.integration.portfolio_data_service import portfolio_data_service

@st.cache_data(ttl=300)
def load_portfolio(_fund_id: str):
    async def _load():
        return await portfolio_data_service.get_portfolio_view(_fund_id)
    loop = asyncio.new_event_loop()
    try:
        portfolio = loop.run_until_complete(_load())
        return pd.DataFrame([{
            "ticker": c.ticker, "name": c.name, "sector": c.sector,
            "org_air": c.org_air, "vr_score": c.vr_score,
            "hr_score": c.hr_score, "delta": c.delta_since_entry,
            "evidence_count": c.evidence_count,
        } for c in portfolio])
    finally:
        loop.close()

# Metrics row: Fund-AI-R | Companies | Avg V^R | Avg Delta
fund_air = portfolio_df["org_air"].mean()
col1.metric("Fund-AI-R", f"{fund_air:.1f}")
col2.metric("Companies", len(portfolio_df))
col3.metric("Avg V^R", f"{portfolio_df['vr_score'].mean():.1f}")
col4.metric("Avg Delta", f"{portfolio_df['delta'].mean():+.1f}")
```

**What to change:**
- Replace per-ticker HTTP loading with `portfolio_data_service.get_portfolio_view(fund_id)`
- Fix metrics row: 4th metric should be `Avg Delta` (not Laggards)
- Evidence Analysis page: change `render_company_evidence_panel(selected)` to:
  ```python
  justifications = fetch_all_justifications(selected)
  render_company_evidence_panel(selected, justifications)
  ```
- Keep the 3-page structure (Portfolio, Evidence, Agentic Workflow) — CS5 only specifies the dashboard content, not that it must be single-page

---

### FIX 7 — Task 10.2: Specialist Agents (`app/agents/specialists.py`)

**Problem 1 — MCPToolCaller:** Calls FastAPI at `localhost:8000`. CS5 says it calls MCP server.
CS5 (pg 24):
```python
class MCPToolCaller:
    def __init__(self, base_url: str = "http://localhost:3000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        response = await self.client.post(
            f"{self.base_url}/tools/{tool_name}", json=arguments
        )
        return response.json().get("result", "{}")
```
**Practical note:** Our MCP server uses stdio not HTTP. The agents currently call FastAPI which internally calls the same CS services. This architecture is functionally equivalent. For the exercise, keep current approach but rename `MCPToolCaller` to match CS5 interface (has `call_tool` method) — already done.

**Problem 2 — HITL bounds (CRITICAL):**
CS5 (pg 25): `requires_approval = org_air > 85 or org_air < 40`
Current: `org_air >= hitl_score_change_threshold` (threshold=15.0 which is wrong — it's a delta not a score)

**Fix:**
```python
# In ScoringAgent.calculate()
org_air = score_data["org_air"]
requires_approval = org_air > 85 or org_air < 40   # CS5 exact condition
approval_reason = f"Score {org_air:.1f} outside normal range [40, 85]" if requires_approval else None
```

**Problem 3 — EvidenceAgent dimensions:**
CS5 (pg 25): `dimensions = ["data_infrastructure", "talent", "use_case_portfolio"]`
Current: only `["talent"]`

**Fix:**
```python
# In EvidenceAgent.justify()
dimensions = ["data_infrastructure", "talent", "use_case_portfolio"]
```

**Problem 4 — LLM choices:**
CS5 (pg 24-26):
- `SECAnalysisAgent`: `ChatOpenAI(model="gpt-4o", temperature=0.3)`
- `ScoringAgent`: `ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0.2)`
- `EvidenceAgent`: `ChatOpenAI(model="gpt-4o", temperature=0.3)`
- `ValueCreationAgent`: `ChatOpenAI(model="gpt-4o", temperature=0.3)`

Current: uses Groq and Claude Haiku. Update model references to match CS5. Use try/except fallback to Groq if API key unavailable.

---

### FIX 8 — Task 10.5: Fund-AI-R (`app/services/analytics/fund_air.py`)

**Problem:** `calculate(fund_id, tickers)` — fetches internally. CS5 requires `calculate_fund_metrics(fund_id, companies, enterprise_values)` — receives data externally.

**CS5 requires (pg 30-31):**
```python
class FundAIRCalculator:
    def calculate_fund_metrics(
        self,
        fund_id: str,
        companies: List[PortfolioCompanyView],
        enterprise_values: Dict[str, float],
    ) -> FundMetrics:
        total_ev = sum(enterprise_values.get(c.company_id, 100.0) for c in companies)
        weighted_sum = sum(enterprise_values.get(c.company_id, 100.0) * c.org_air for c in companies)
        fund_air = weighted_sum / total_ev if total_ev > 0 else 0.0
        # quartile distribution, HHI, leaders/laggards
        return FundMetrics(...)

fund_air_calculator = FundAIRCalculator()
```

**`FundMetrics` CS5 fields:**
- `fund_id`, `fund_air`, `company_count`, `quartile_distribution`, `sector_hhi`, `avg_delta_since_entry`, `total_ev_mm`, `ai_leaders_count`, `ai_laggards_count`

**What to change:**
- Add `calculate_fund_metrics(fund_id, companies, enterprise_values)` method alongside existing `calculate()` — do NOT remove `calculate()`
- Add CS5-spec `FundMetrics` dataclass with exact field names above (keep existing one too)
- Add `fund_air_calculator = FundAIRCalculator()` singleton (no constructor args)
- `SECTOR_BENCHMARKS` must use CS5 format: `{"technology": {"q1": 75, "q2": 65, "q3": 55, "q4": 45}, ...}`

---

## CREATE — New files

---

### CREATE 1 — Task 10.4: DD Workflow (`exercises/agentic_due_diligence.py`)

**CS5 requires (pg 29):**
```python
"""Agentic Due Diligence - Complete Multi-Agent Workflow."""
import asyncio
from datetime import datetime
from app.agents.supervisor import dd_graph
from app.agents.state import DueDiligenceState

async def run_due_diligence(
    company_id: str, assessment_type: str = "full"
) -> DueDiligenceState:
    initial_state: DueDiligenceState = {
        "company_id": company_id,
        "assessment_type": assessment_type,
        "requested_by": "analyst",
        "messages": [],
        "sec_analysis": None,
        "talent_analysis": None,
        "scoring_result": None,
        "evidence_justifications": None,
        "value_creation_plan": None,
        "next_agent": None,
        "requires_approval": False,
        "approval_reason": None,
        "approval_status": None,
        "approved_by": None,
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "total_tokens": 0,
        "error": None,
    }
    config = {"configurable": {"thread_id": f"dd-{company_id}-{datetime.now().isoformat()}"}}
    return await dd_graph.ainvoke(initial_state, config)

async def main():
    print("=" * 60)
    print("PE Org-AI-R: Agentic Due Diligence")
    print("=" * 60)

    result = await run_due_diligence("NVDA", "full")

    print(f"\nOrg-AI-R: {result['scoring_result']['org_air']:.1f}")
    print(f"HITL Required: {result.get('requires_approval', False)}")
    print(f"Approval Status: {result.get('approval_status', 'N/A')}")
    print(f"Approved By: {result.get('approved_by', 'N/A')}")
    print(f"Messages logged: {len(result.get('messages', []))}")

    if result.get("value_creation_plan"):
        plan = result["value_creation_plan"]
        print(f"\nEBITDA Impact: {plan.get('risk_adjusted', 'N/A')}")

    print("\nAll data came from CS1-CS4 via MCP tools.")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## BONUS — 20 extra points

---

### Bonus 1 — Mem0 Semantic Memory (+5 pts) → `app/agents/memory.py`

Adds persistent semantic memory to agents so they recall prior assessments across sessions.

**What to build:**
```python
"""Mem0 semantic memory for PE Org-AI-R agents."""
from mem0 import Memory

class AgentMemory:
    """Wraps Mem0 for agent cross-session recall."""

    def __init__(self):
        self.memory = Memory()

    def remember_assessment(self, company_id: str, result: dict) -> None:
        """Store key findings from a DD run."""
        summary = (
            f"{company_id} assessed: Org-AI-R={result.get('org_air', 0):.1f}, "
            f"VR={result.get('vr_score', 0):.1f}, HR={result.get('hr_score', 0):.1f}. "
            f"HITL={'triggered' if result.get('requires_approval') else 'not triggered'}."
        )
        self.memory.add(summary, user_id=company_id)

    def recall(self, company_id: str, query: str) -> list:
        """Search memory for prior context about a company."""
        return self.memory.search(query, user_id=company_id)

agent_memory = AgentMemory()
```

**Integration:** In `ValueCreationAgent.plan()`, after building the plan, call `agent_memory.remember_assessment(company_id, value_creation_plan)`. In `SECAnalysisAgent.analyze()`, call `agent_memory.recall(company_id, "prior SEC findings")` and prepend to the LLM prompt.

**Dependency:** Add `mem0ai>=0.1.0` to `requirements.txt`.

---

### Bonus 2 — Investment Tracker with ROI (+5 pts) → `app/services/tracking/investment_tracker.py`

Tracks portfolio entry prices and computes AI-readiness-driven ROI projections.

**What to build:**
```python
"""Investment Tracker - tracks entry prices and projects AI-driven ROI."""
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime

ENTRY_PRICES: Dict[str, float] = {
    "NVDA": 495.0, "JPM": 172.0, "WMT": 58.0, "GE": 110.0, "DG": 148.0,
}

@dataclass
class InvestmentROI:
    ticker: str
    entry_price: float
    entry_org_air: float
    current_org_air: float
    air_improvement: float
    projected_revenue_lift_pct: float   # air_improvement * 0.8%
    projected_ebitda_lift_pct: float    # air_improvement * 0.3%
    projected_exit_multiple_expansion: float  # air_improvement * 0.05x
    roi_estimate_pct: float
    assessment_date: datetime

class InvestmentTracker:
    """Maps Org-AI-R improvement to ROI projections."""

    def compute_roi(
        self,
        ticker: str,
        current_org_air: float,
        entry_org_air: float = 45.0,
    ) -> InvestmentROI:
        improvement = current_org_air - entry_org_air
        revenue_lift = improvement * 0.8
        ebitda_lift = improvement * 0.3
        multiple_expansion = improvement * 0.05
        roi = (revenue_lift * 2.5) + (ebitda_lift * 8.0)  # simplified DCF proxy
        return InvestmentROI(
            ticker=ticker,
            entry_price=ENTRY_PRICES.get(ticker, 100.0),
            entry_org_air=entry_org_air,
            current_org_air=current_org_air,
            air_improvement=round(improvement, 2),
            projected_revenue_lift_pct=round(revenue_lift, 2),
            projected_ebitda_lift_pct=round(ebitda_lift, 2),
            projected_exit_multiple_expansion=round(multiple_expansion, 3),
            roi_estimate_pct=round(roi, 2),
            assessment_date=datetime.utcnow(),
        )

    def portfolio_roi_summary(self, scores: Dict[str, float]) -> Dict:
        results = {t: self.compute_roi(t, s) for t, s in scores.items()}
        avg_roi = sum(r.roi_estimate_pct for r in results.values()) / len(results)
        return {
            "companies": {t: vars(r) for t, r in results.items()},
            "portfolio_avg_roi_pct": round(avg_roi, 2),
            "top_performer": max(results, key=lambda t: results[t].roi_estimate_pct),
        }

investment_tracker = InvestmentTracker()
```

**Integration:** Add ROI section to `streamlit/cs5_app.py` Portfolio Overview page — call `investment_tracker.portfolio_roi_summary(scores)` after loading scores and display as a table.

---

### Bonus 3 — IC Memo Generator — Word doc (+5 pts) → `app/services/reporting/ic_memo.py`

Generates a formatted Investment Committee memo as a `.docx` file.

**What to build:**
```python
"""IC Memo Generator — produces Word document IC package."""
from docx import Document
from docx.shared import Pt, RGBColor
from datetime import datetime
from typing import Dict, Any

class ICMemoGenerator:
    """Generates IC memo Word document from DD results."""

    def generate(
        self,
        company_id: str,
        scoring_result: Dict[str, Any],
        gap_analysis: Dict[str, Any],
        ebitda_projection: Dict[str, Any],
        output_path: str = None,
    ) -> str:
        doc = Document()

        # Title
        title = doc.add_heading(f"Investment Committee Memo: {company_id}", 0)
        doc.add_paragraph(f"Date: {datetime.utcnow().strftime('%B %d, %Y')}")
        doc.add_paragraph(f"Prepared by: PE Org-AI-R Agentic Platform")
        doc.add_paragraph("CONFIDENTIAL", style="Intense Quote")

        # Executive Summary
        doc.add_heading("Executive Summary", level=1)
        org_air = scoring_result.get("org_air", 0)
        vr = scoring_result.get("vr_score", 0)
        hr = scoring_result.get("hr_score", 0)
        doc.add_paragraph(
            f"{company_id} has an Org-AI-R score of {org_air:.1f}, "
            f"reflecting V^R of {vr:.1f} and H^R of {hr:.1f}. "
            f"{'Strong AI readiness positions for value creation.' if org_air >= 70 else 'Improvement opportunities identified across key dimensions.'}"
        )

        # Scoring Section
        doc.add_heading("AI Readiness Assessment", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "Metric", "Score", "Benchmark"
        for metric, score, bench in [
            ("Org-AI-R", org_air, 65.0),
            ("V^R", vr, 60.0),
            ("H^R", hr, 60.0),
        ]:
            row = table.add_row().cells
            row[0].text = metric
            row[1].text = f"{score:.1f}"
            row[2].text = f"{bench:.1f}"

        # Gap Analysis
        doc.add_heading("Gap Analysis & 100-Day Plan", level=1)
        gaps = gap_analysis.get("dimension_gaps", [])
        for gap in gaps[:3]:
            doc.add_paragraph(
                f"• {gap.get('dimension', '').replace('_', ' ').title()}: "
                f"current {gap.get('current_score', 0):.1f} → target {gap.get('target_score', 0):.1f}",
                style="List Bullet",
            )

        # EBITDA
        doc.add_heading("EBITDA Impact Projection", level=1)
        scenarios = ebitda_projection.get("scenarios", {})
        doc.add_paragraph(
            f"Base case EBITDA improvement: {scenarios.get('base', 'N/A')} | "
            f"Risk-adjusted: {ebitda_projection.get('risk_adjusted', 'N/A')}"
        )

        # Recommendation
        doc.add_heading("IC Recommendation", level=1)
        rec = "PROCEED" if org_air >= 60 else "CONDITIONAL — address gaps before next capital deployment"
        doc.add_paragraph(f"Recommendation: {rec}", style="Intense Quote")

        path = output_path or f"ic_memo_{company_id}_{datetime.now().strftime('%Y%m%d')}.docx"
        doc.save(path)
        return path

ic_memo_generator = ICMemoGenerator()
```

**Dependency:** Add `python-docx>=1.1.0` to `requirements.txt`.

**Integration:** Add "Generate IC Memo" button in `streamlit/cs5_app.py` Evidence Analysis page. On click, call `ic_memo_generator.generate(...)` and use `st.download_button` to serve the `.docx`.

---

### Bonus 4 — LP Letter Generator (+5 pts) → `app/services/reporting/lp_letter.py`

Generates Limited Partner update letter summarizing fund AI readiness.

**What to build:**
```python
"""LP Letter Generator — produces LP update letter from Fund-AI-R metrics."""
from docx import Document
from datetime import datetime
from typing import Dict, Any, List

LP_LETTER_TEMPLATE = """
Dear Limited Partners,

We are pleased to share the quarterly AI Readiness update for {fund_id}.

FUND PERFORMANCE SUMMARY
-------------------------
Fund-AI-R Score: {fund_air:.1f} / 100
Portfolio Companies: {company_count}
AI Leaders (≥70): {leaders}
AI Laggards (<50): {laggards}

PORTFOLIO HIGHLIGHTS
--------------------
{highlights}

VALUE CREATION PIPELINE
------------------------
{value_creation}

Our agentic due diligence platform continues to surface actionable insights
across the portfolio. The Org-AI-R framework identifies specific improvement
vectors for each company, enabling precise capital deployment decisions.

We remain committed to driving AI-readiness across the portfolio and look
forward to sharing further progress in our next update.

Sincerely,
The Investment Team
{fund_id}
{date}
"""

class LPLetterGenerator:
    """Generates LP update letters from Fund-AI-R metrics."""

    def generate(
        self,
        fund_id: str,
        fund_metrics: Dict[str, Any],
        company_scores: List[Dict[str, Any]],
        output_path: str = None,
    ) -> str:
        leaders = sum(1 for c in company_scores if c.get("org_air", 0) >= 70)
        laggards = sum(1 for c in company_scores if c.get("org_air", 0) < 50)

        highlights = "\n".join([
            f"  • {c['ticker']}: Org-AI-R {c.get('org_air', 0):.1f} "
            f"({'Leader' if c.get('org_air', 0) >= 70 else 'Developing'})"
            for c in sorted(company_scores, key=lambda x: x.get("org_air", 0), reverse=True)
        ])

        top = max(company_scores, key=lambda x: x.get("org_air", 0))
        value_creation = (
            f"  • {top['ticker']} leads the portfolio at {top.get('org_air', 0):.1f} Org-AI-R. "
            f"Gap analysis identifies data infrastructure and talent as primary value levers."
        )

        letter = LP_LETTER_TEMPLATE.format(
            fund_id=fund_id,
            fund_air=fund_metrics.get("fund_air", 0),
            company_count=len(company_scores),
            leaders=leaders,
            laggards=laggards,
            highlights=highlights,
            value_creation=value_creation,
            date=datetime.utcnow().strftime("%B %d, %Y"),
        )

        # Save as docx
        doc = Document()
        doc.add_heading(f"LP Update — {fund_id}", 0)
        for line in letter.strip().split("\n"):
            doc.add_paragraph(line)

        path = output_path or f"lp_letter_{fund_id}_{datetime.now().strftime('%Y%m%d')}.docx"
        doc.save(path)
        return path

lp_letter_generator = LPLetterGenerator()
```

**Integration:** Add "Generate LP Letter" button in `streamlit/cs5_app.py` Portfolio Overview page. Call `lp_letter_generator.generate(fund_id, fund_metrics, company_scores)` and serve with `st.download_button`.

---

## Verification Checklist (CS5 pg 34-35)

```bash
# 1. MCP server starts
python -m app.mcp.server

# 2. Grader test passes — cs3_client must be called
pytest tests/test_mcp_integration.py -v

# 3. Stop FastAPI → tools must error (not return hardcoded data)
# kill uvicorn, then run tools → expect errors

# 4. DD Workflow runs end-to-end
uvicorn app.main:app --reload            # Terminal 1
python exercises/agentic_due_diligence.py  # Terminal 2

# 5. NVDA (org_air ~84-90) triggers HITL
# Expected: requires_approval=True, org_air > 85

# 6. Dashboard loads
cd streamlit && streamlit run cs5_app.py

# 7. Prometheus counters populate
# Call tools, then check /metrics endpoint
```

---

## Execution Order (by priority + dependencies)

```
Day 1:
  [1] FIX Task 9.2 — MCP Server call chain (cs3_client at module level)
  [2] FIX Task 10.2 — ScoringAgent HITL bounds (org_air > 85 or < 40)
  [3] FIX Task 10.2 — EvidenceAgent 3 dimensions
  [4] CREATE Task 10.4 — DD Workflow exercise (copy from spec above)

Day 2:
  [5] FIX Task 9.1 — PortfolioDataService (add PortfolioCompanyView + get_portfolio_view)
  [6] FIX Task 9.4 — Assessment History (add cs1_client + factory)
  [7] FIX Task 10.5 — Fund-AI-R (add calculate_fund_metrics method)
  [8] FIX Task 9.3 — Add gamma fields to resource

Day 3:
  [9]  FIX Task 9.5 — Evidence Display signature
  [10] FIX Task 9.6 — Dashboard data source + metrics row
  [11] Run full verification checklist

Day 4 (Bonus):
  [B1] Mem0 memory — app/agents/memory.py
  [B2] Investment Tracker — app/services/tracking/investment_tracker.py
  [B3] IC Memo Generator — app/services/reporting/ic_memo.py
  [B4] LP Letter Generator — app/services/reporting/lp_letter.py
  [B5] Wire bonus features into Streamlit dashboard
  [B6] Add to requirements.txt: mem0ai, python-docx
```

---

## Requirements additions

Add to `requirements.txt`:
```
mem0ai>=0.1.0
python-docx>=1.1.0
```
