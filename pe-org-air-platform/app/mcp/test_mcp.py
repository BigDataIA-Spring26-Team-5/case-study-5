# import asyncio
# import json
# from mcp.client.stdio import stdio_client, StdioServerParameters
# from mcp.client.session import ClientSession

# async def main():
#     server_params = StdioServerParameters(
#         command="python",
#         args=["-m", "app.mcp.server"],
#     )

#     async with stdio_client(server_params) as (read, write):
#         async with ClientSession(read, write) as session:
#             await session.initialize()
#             print("MCP session initialized!\n")

#             print("=== Test: project_ebitda_impact (local math, no API calls) ===")
#             try:
#                 result = await asyncio.wait_for(
#                     session.call_tool("project_ebitda_impact", {
#                         "company_id": "NVDA",
#                         "entry_score": 50.0,
#                         "target_score": 80.0,
#                         "h_r_score": 70.0
#                     }),
#                     timeout=10
#                 )
#                 raw = result.content[0].text
#                 print(f"Raw response: {raw}")

#                 if raw.startswith("{"):
#                     parsed = json.loads(raw)
#                     print("\nParsed:")
#                     print(json.dumps(parsed, indent=2))
#                     print("\nSUCCESS - MCP tool wiring works!")
#             except asyncio.TimeoutError:
#                 print("TIMEOUT after 10s - something is wrong")
#             except Exception as e:
#                 print(f"ERROR: {e}")

# asyncio.run(main())

import asyncio
import json
import httpx
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

FASTAPI_URL = "http://localhost:8000"

# Tools that make HTTP calls to FastAPI
NEEDS_FASTAPI = {
    "calculate_org_air_score", "run_gap_analysis", "get_portfolio_summary",
    "generate_justification", "get_company_evidence",
}


def check_fastapi() -> bool:
    try:
        r = httpx.get(f"{FASTAPI_URL}/healthz", timeout=3.0)
        return r.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tool menu: name → (description, default arguments, timeout)
# ---------------------------------------------------------------------------

TOOLS = {
    "1": {
        "name": "calculate_org_air_score",
        "description": "Get Org-AI-R score for a company (requires FastAPI running)",
        "defaults": {"company_id": "NVDA"},
        "prompts": [("company_id", "Ticker (e.g. NVDA, JPM, WMT, GE, DG)", str)],
        "timeout": 30,
    },
    "2": {
        "name": "get_company_evidence",
        "description": "Retrieve CS2 evidence for a company (requires FastAPI running)",
        "defaults": {"company_id": "NVDA", "limit": 5},
        "prompts": [
            ("company_id", "Ticker", str),
            ("dimension", "Dimension filter or leave blank (data_infrastructure / ai_governance / technology_stack / talent / leadership / use_case_portfolio / culture)", str),
            ("limit", "Max items [5]", int),
        ],
        "timeout": 30,
    },
    "3": {
        "name": "generate_justification",
        "description": "Generate RAG justification for a dimension (requires FastAPI running)",
        "defaults": {"company_id": "NVDA", "dimension": "talent"},
        "prompts": [
            ("company_id", "Ticker", str),
            ("dimension", "Dimension (data_infrastructure / ai_governance / technology_stack / talent / leadership / use_case_portfolio / culture)", str),
        ],
        "timeout": 60,
    },
    "4": {
        "name": "project_ebitda_impact",
        "description": "Project EBITDA impact (pure local math, no external services needed)",
        "defaults": {"company_id": "NVDA", "entry_score": 50.0, "target_score": 80.0, "h_r_score": 70.0},
        "prompts": [
            ("company_id", "Ticker", str),
            ("entry_score", "Entry Org-AI-R score [50.0]", float),
            ("target_score", "Target Org-AI-R score [80.0]", float),
            ("h_r_score", "H^R score [70.0]", float),
        ],
        "timeout": 15,
    },
    "5": {
        "name": "run_gap_analysis",
        "description": "Run gap analysis toward a target score (requires FastAPI running)",
        "defaults": {"company_id": "NVDA", "target_org_air": 85.0},
        "prompts": [
            ("company_id", "Ticker", str),
            ("target_org_air", "Target Org-AI-R score [85.0]", float),
        ],
        "timeout": 30,
    },
    "6": {
        "name": "get_portfolio_summary",
        "description": "Get fund-level portfolio summary (requires FastAPI running)",
        "defaults": {"fund_id": "PE-FUND-I"},
        "prompts": [("fund_id", "Fund ID [PE-FUND-I]", str)],
        "timeout": 60,
    },
}


def collect_args(tool_cfg: dict) -> dict:
    """Prompt the user for each argument, using defaults on empty input."""
    args = dict(tool_cfg["defaults"])
    print()
    for key, label, cast in tool_cfg["prompts"]:
        default = tool_cfg["defaults"].get(key, "")
        raw = input(f"  {label} [{default}]: ").strip()
        if raw == "":
            continue  # keep default
        if cast == int:
            args[key] = int(raw)
        elif cast == float:
            args[key] = float(raw)
        else:
            if raw:  # only override if user typed something
                args[key] = raw
    # Drop blank optional string args (e.g. empty dimension filter)
    return {k: v for k, v in args.items() if v != ""}


def print_menu():
    print("\n" + "=" * 55)
    print("  PE Org-AI-R MCP Tool Tester")
    print("=" * 55)
    print("  -- Tools --")
    for num, cfg in TOOLS.items():
        print(f"  {num}. {cfg['name']}")
        print(f"     {cfg['description']}")
    print("  -- Resources --")
    print("  r1. List all resources")
    print("  r2. Read orgair://parameters/v2.0")
    print("  r3. Read orgair://sectors")
    print("  -- Prompts --")
    print("  p1. List all prompts")
    print("  p2. Get due_diligence_assessment prompt")
    print("  p3. Get ic_meeting_prep prompt")
    print("  q. Quit")
    print("=" * 55)


async def run_tool(session: ClientSession, tool_cfg: dict, args: dict):
    tool_name = tool_cfg["name"]

    # Pre-flight checks
    if tool_name in NEEDS_FASTAPI:
        if check_fastapi():
            print(f"  FastAPI reachable at {FASTAPI_URL}")
        else:
            print(f"  WARNING: FastAPI is NOT reachable at {FASTAPI_URL}")
            print("  Start it with:  uvicorn app.main:app --reload")
            print("  Proceeding anyway — tool will likely timeout.\n")


    print(f"\n>>> Calling: {tool_cfg['name']}")
    print(f"    Args: {json.dumps(args, indent=2)}")
    print("    Waiting for response (no timeout)...\n")
    try:
        result = await session.call_tool(tool_cfg["name"], args)
        raw = result.content[0].text if result.content else ""
        if not raw:
            print("ERROR: empty response from server")
            return
        try:
            parsed = json.loads(raw)
            print("Result:")
            print(json.dumps(parsed, indent=2))
            print("\nSUCCESS")
        except json.JSONDecodeError:
            print(f"SERVER ERROR: {raw}")
    except Exception as e:
        print(f"ERROR: {e}")


async def run_resource(session: ClientSession, uri: str):
    print(f"\n>>> Reading resource: {uri}\n")
    try:
        result = await session.read_resource(uri)
        for content in result.contents:
            try:
                parsed = json.loads(content.text)
                print(json.dumps(parsed, indent=2))
            except (json.JSONDecodeError, AttributeError):
                print(content)
        print("\nSUCCESS")
    except Exception as e:
        print(f"ERROR: {e}")


async def run_prompt(session: ClientSession, name: str):
    company_id = input(f"  Ticker for '{name}' [NVDA]: ").strip() or "NVDA"
    print(f"\n>>> Getting prompt: {name}  company_id={company_id}\n")
    try:
        result = await session.get_prompt(name, {"company_id": company_id})
        for msg in result.messages:
            print(f"[{msg.role}]")
            print(msg.content.text)
        print("\nSUCCESS")
    except Exception as e:
        print(f"ERROR: {e}")


async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "app.mcp.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("MCP session initialized!")

            while True:
                print_menu()
                choice = input("Select an option: ").strip().lower()

                if choice == "q":
                    print("Goodbye.")
                    break

                elif choice == "r1":
                    result = await session.list_resources()
                    print("\n>>> Resources:")
                    for r in result.resources:
                        print(f"  {r.uri}  —  {r.name}")
                        print(f"    {r.description}")

                elif choice == "r2":
                    await run_resource(session, "orgair://parameters/v2.0")

                elif choice == "r3":
                    await run_resource(session, "orgair://sectors")

                elif choice == "p1":
                    result = await session.list_prompts()
                    print("\n>>> Prompts:")
                    for p in result.prompts:
                        print(f"  {p.name}  —  {p.description}")

                elif choice == "p2":
                    await run_prompt(session, "due_diligence_assessment")

                elif choice == "p3":
                    await run_prompt(session, "ic_meeting_prep")

                elif choice in TOOLS:
                    tool_cfg = TOOLS[choice]
                    args = collect_args(tool_cfg)
                    await run_tool(session, tool_cfg, args)

                else:
                    print(f"Invalid choice '{choice}'.")
                    continue

                again = input("\nRun another? (y/n) [y]: ").strip().lower()
                if again == "n":
                    print("Goodbye.")
                    break


asyncio.run(main())
