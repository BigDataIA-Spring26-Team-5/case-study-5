"""Manual smoke test for the supervisor + HITL graph.

Run:
    poetry run uvicorn app.main:app --reload   (Terminal 1)
    poetry run python -m app.agents.test_supervisor   (Terminal 2)
"""
import asyncio
import json
from datetime import datetime, timezone

from app.agents.state import DueDiligenceState
from app.agents.supervisor import dd_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(company_id: str = "NVDA", assessment_type: str = "full") -> dict:
    return {
        "company_id": company_id,
        "assessment_type": assessment_type,
        "requested_by": "analyst@test.com",
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
        "started_at": datetime.now(tz=timezone.utc),
        "completed_at": None,
        "total_tokens": 0,
        "error": None,
    }


def print_section(label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    ticker = input("Ticker [NVDA]: ").strip() or "NVDA"
    assessment_type = (
        input("Assessment type (full / limited / screening) [full]: ").strip()
        or "full"
    )

    print(f"\nRunning due diligence graph for {ticker} ({assessment_type})...")
    state = make_state(ticker, assessment_type)
    config = {"configurable": {"thread_id": f"{ticker}-{assessment_type}"}}

    # Stream events so we can see each node as it completes
    async for event in dd_graph.astream(state, config=config):
        for node_name, node_output in event.items():
            print(f"\n  [{node_name}]")

            # Routing decisions
            if "next_agent" in node_output:
                print(f"    → next_agent: {node_output['next_agent']}")

            # HITL fields
            for key in ("approval_status", "approved_by", "approval_reason"):
                if node_output.get(key):
                    print(f"    {key}: {node_output[key]}")

            # Completion
            if node_output.get("completed_at"):
                print(f"    completed_at: {node_output['completed_at']}")

            # Errors
            if node_output.get("error"):
                print(f"    ERROR: {node_output['error']}")

            # Agent messages (trim to first 180 chars)
            for msg in node_output.get("messages", []):
                agent = msg.get("agent_name", "?")
                content = msg.get("content", "")[:180]
                print(f"    [{agent}] {content}")

            # Key result fields per specialist
            if "sec_analysis" in node_output:
                sa = node_output["sec_analysis"]
                print(f"    sec_analysis: {sa.get('evidence_count', '?')} evidence items")

            if "scoring_result" in node_output:
                sr = node_output["scoring_result"]
                print(
                    f"    scoring_result: org_air={sr.get('org_air')}, "
                    f"vr={sr.get('vr_score')}, hr={sr.get('hr_score')}"
                )

            if "talent_analysis" in node_output:
                ta = node_output["talent_analysis"]
                tech = len(ta.get("technology_hiring_signals", []))
                print(f"    talent_analysis: {tech} technology_hiring signals")

            if "value_creation_plan" in node_output:
                vcp = node_output["value_creation_plan"]
                print(
                    f"    value_creation_plan: delta_air={vcp.get('delta_air')}, "
                    f"risk_adjusted={vcp.get('risk_adjusted')}"
                )

    # Final state summary
    final = await dd_graph.aget_state(config)
    values = final.values

    print_section("FINAL STATE SUMMARY")
    print(f"  Company          : {values.get('company_id')}")
    print(f"  Assessment type  : {values.get('assessment_type')}")
    print(f"  Completed at     : {values.get('completed_at')}")
    print(f"  Messages logged  : {len(values.get('messages', []))}")
    print(f"  Approval status  : {values.get('approval_status')}")
    if values.get("approved_by"):
        print(f"  Approved by      : {values.get('approved_by')}")
    if values.get("error"):
        print(f"  Error            : {values.get('error')}")

    sr = values.get("scoring_result") or {}
    if sr.get("org_air"):
        print(f"  Org-AI-R score   : {sr.get('org_air'):.2f}")
        print(f"  V^R score        : {sr.get('vr_score'):.2f}")
        print(f"  H^R score        : {sr.get('hr_score'):.2f}")

    vcp = values.get("value_creation_plan") or {}
    if vcp.get("delta_air") is not None:
        print(f"  Delta AI-R       : {vcp.get('delta_air')}")
        print(f"  Risk-adj EBITDA  : {vcp.get('risk_adjusted')}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
