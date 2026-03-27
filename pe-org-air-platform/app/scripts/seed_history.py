"""
Seed assessment history snapshots for portfolio companies.
Creates entry-point + monthly snapshots showing score progression.

Run:
  cd pe-org-air-platform
  python -m app.scripts.seed_history
"""
import sys
import uuid
import json
from pathlib import Path
from datetime import datetime, timedelta

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from app.repositories.base import get_snowflake_connection

# Current scores (from GET /assessments/{ticker}) and realistic entry baselines
COMPANIES = {
    "NVDA": {"current": {"org_air": 84.94, "vr": 80.77, "hr": 93.19, "syn": 79.11}, "entry_discount": 12},
    "CRM":  {"current": {"org_air": 61.49, "vr": 74.76, "hr": 52.23, "syn": 30.20}, "entry_discount": 8},
    "GOOGL":{"current": {"org_air": 60.24, "vr": 72.59, "hr": 52.03, "syn": 30.00}, "entry_discount": 7},
    "JPM":  {"current": {"org_air": 66.80, "vr": 67.26, "hr": 72.36, "syn": 48.50}, "entry_discount": 10},
    "WMT":  {"current": {"org_air": 60.03, "vr": 67.35, "hr": 57.58, "syn": 35.00}, "entry_discount": 6},
    "ADP":  {"current": {"org_air": 54.30, "vr": 62.34, "hr": 51.11, "syn": 28.30}, "entry_discount": 5},
    "UNH":  {"current": {"org_air": 0.0,   "vr": 0.0,   "hr": 0.0,   "syn": 0.0},   "entry_discount": 0},
}

# Generate monthly snapshots from 6 months ago to now
NUM_SNAPSHOTS = 7  # entry + 6 months
ASSESSMENT_TYPES = ["full", "full", "limited", "full", "full", "limited", "full"]
ASSESSORS = ["analyst_01", "analyst_01", "analyst_02", "analyst_01", "analyst_01", "analyst_02", "analyst_01"]


def lerp(start: float, end: float, t: float) -> float:
    """Linear interpolation with slight noise."""
    base = start + (end - start) * t
    # Add small variation to make it look realistic
    import random
    random.seed(int(start * 100 + t * 1000))
    noise = random.uniform(-1.5, 1.5)
    return round(base + noise, 2)


def main():
    conn = get_snowflake_connection()
    cur = conn.cursor()

    # Ensure table exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS CS5_ASSESSMENT_SNAPSHOTS (
            id VARCHAR(36) PRIMARY KEY,
            portfolio_id VARCHAR(36),
            ticker VARCHAR(20),
            captured_at TIMESTAMP_NTZ,
            assessment_type VARCHAR(20),
            assessor_id VARCHAR(255),
            org_air FLOAT,
            vr_score FLOAT,
            hr_score FLOAT,
            synergy_score FLOAT,
            confidence_lower FLOAT,
            confidence_upper FLOAT,
            evidence_count INTEGER,
            dimension_scores_json VARCHAR,
            created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)

    # Clear old seeded data
    cur.execute("DELETE FROM CS5_ASSESSMENT_SNAPSHOTS WHERE assessor_id IN ('analyst_01', 'analyst_02', 'system')")
    print("Cleared existing snapshots.")

    now = datetime.utcnow()
    inserted = 0

    for ticker, data in COMPANIES.items():
        curr = data["current"]
        discount = data["entry_discount"]

        # Skip companies with no scores
        if curr["org_air"] == 0:
            print(f"  SKIP  {ticker} - no current scores")
            continue

        entry = {
            "org_air": round(curr["org_air"] - discount, 2),
            "vr": round(curr["vr"] - discount * 0.8, 2),
            "hr": round(curr["hr"] - discount * 0.6, 2),
            "syn": round(curr["syn"] - discount * 0.5, 2),
        }

        for i in range(NUM_SNAPSHOTS):
            t = i / (NUM_SNAPSHOTS - 1)  # 0.0 to 1.0
            captured = now - timedelta(days=int((NUM_SNAPSHOTS - 1 - i) * 30))

            org_air = lerp(entry["org_air"], curr["org_air"], t)
            vr = lerp(entry["vr"], curr["vr"], t)
            hr = lerp(entry["hr"], curr["hr"], t)
            syn = lerp(entry["syn"], curr["syn"], t)

            # Evidence count grows over time
            ev_base = 15 + int(i * 5)
            evidence_count = ev_base + int(discount * 0.5)

            snapshot_id = str(uuid.uuid4())
            atype = ASSESSMENT_TYPES[i]
            assessor = ASSESSORS[i]
            ci_half = max(2.0, 6.0 - org_air / 30)

            cur.execute("""
                INSERT INTO CS5_ASSESSMENT_SNAPSHOTS
                (id, portfolio_id, ticker, captured_at, assessment_type, assessor_id,
                 org_air, vr_score, hr_score, synergy_score,
                 confidence_lower, confidence_upper, evidence_count,
                 dimension_scores_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                snapshot_id, None, ticker.upper(), captured, atype, assessor,
                org_air, vr, hr, syn,
                round(org_air - ci_half, 2), round(org_air + ci_half, 2),
                evidence_count,
                json.dumps({}),
            ))
            inserted += 1

        entry_score = round(curr["org_air"] - discount, 1)
        print(f"  DONE  {ticker} - {NUM_SNAPSHOTS} snapshots, entry={entry_score} -> current={curr['org_air']}, delta=+{discount}")

    print(f"\nInserted {inserted} snapshots total.")

    # Verify
    print("\n--- Verification ---")
    cur.execute("""
        SELECT ticker, COUNT(*) as cnt,
               MIN(org_air) as min_score, MAX(org_air) as max_score,
               MIN(captured_at) as first, MAX(captured_at) as last
        FROM CS5_ASSESSMENT_SNAPSHOTS
        GROUP BY ticker ORDER BY ticker
    """)
    for row in cur.fetchall():
        print(f"  {row[0]:6s}  snapshots={row[1]}  range=[{row[2]:.1f}, {row[3]:.1f}]  period={str(row[4])[:10]} -> {str(row[5])[:10]}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
