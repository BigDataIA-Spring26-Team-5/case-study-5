"""Manual smoke test for specialist agents — requires uvicorn running at localhost:8000.

Run:
    poetry run uvicorn app.main:app --reload   (Terminal 1)
    poetry run python -m app.agents.test_specialists   (Terminal 2)
"""
import json
from datetime import datetime, timezone

from app.agents.state import DueDiligenceState
from app.agents.specialists import (
    SECAnalysisAgent,
    ScoringAgent,
    EvidenceAgent,
    ValueCreationAgent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(company_id: str = "NVDA") -> DueDiligenceState:
    return DueDiligenceState(
        company_id=company_id,
        assessment_type="full",
        requested_by="analyst@test.com",
        messages=[],
        sec_analysis=None,
        talent_analysis=None,
        scoring_result=None,
        evidence_justifications=None,
        value_creation_plan=None,
        next_agent=None,
        requires_approval=False,
        approval_reason=None,
        approval_status=None,
        approved_by=None,
        started_at=datetime.now(tz=timezone.utc),
        completed_at=None,
        total_tokens=0,
        error=None,
    )


def print_section(label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")


def run_agent(label: str, agent, state: DueDiligenceState) -> DueDiligenceState:
    """Call an agent, print its updates, merge into state."""
    print_section(label)
    try:
        updates = agent(state)
        state.update(updates)
        # Print everything except the raw evidence lists (too verbose)
        display = {
            k: v for k, v in updates.items()
            if k != "messages"
        }
        print(json.dumps(display, indent=2, default=str))

        msgs = updates.get("messages", [])
        if msgs:
            print(f"\n  [message from {msgs[0].get('agent_name')}]")
            print(f"  {msgs[0].get('content', '')[:300]}")

        print("\n  STATUS: OK")
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        state["error"] = str(exc)
    return state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ticker = input("Ticker [NVDA]: ").strip() or "NVDA"
    print(f"\nRunning full specialist pipeline for: {ticker}")

    state = make_state(ticker)

    # CS2 — broad evidence collection
    state = run_agent("1. SECAnalysisAgent  (CS2 — evidence)", SECAnalysisAgent(), state)

    # CS3 — composite scoring
    state = run_agent("2. ScoringAgent  (CS3 — Org-AI-R score)", ScoringAgent(), state)

    # CS2 talent dimension
    state = run_agent("3. EvidenceAgent  (CS2 — talent dimension)", EvidenceAgent(), state)

    # CS5 — gap analysis + EBITDA
    state = run_agent("4. ValueCreationAgent  (CS5 — value creation)", ValueCreationAgent(), state)

    # Final summary
    print_section("PIPELINE SUMMARY")
    print(f"  Company          : {state['company_id']}")
    print(f"  Requires approval: {state.get('requires_approval')}")
    if state.get("approval_reason"):
        print(f"  Approval reason  : {state['approval_reason']}")
    print(f"  Messages logged  : {len(state.get('messages', []))}")
    if state.get("error"):
        print(f"  Error            : {state['error']}")

    org_air = (state.get("scoring_result") or {}).get("org_air")
    if org_air is not None:
        print(f"  Org-AI-R score   : {org_air:.1f}")

    delta = (state.get("value_creation_plan") or {}).get("delta_air")
    if delta is not None:
        print(f"  Delta AI-R       : {delta}")

    print()


if __name__ == "__main__":
    main()
