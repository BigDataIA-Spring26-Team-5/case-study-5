"""CS5 Assessment Snapshot Repository.

Implements CS5 Task 9.4 persistence for assessment history snapshots.

Notes
-----
- Uses a dedicated Snowflake table so CS5 snapshots can store Org-AI-R and the
  full dimension breakdown without altering the existing CS1 `assessments` table.
- The backing table is created on first use via `CREATE TABLE IF NOT EXISTS`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.repositories.base import BaseRepository


class AssessmentSnapshotRepository(BaseRepository):
    """Persistence for CS5 assessment snapshots."""

    TABLE_NAME = "CS5_ASSESSMENT_SNAPSHOTS"

    def ensure_table(self) -> None:
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            id                    VARCHAR(36)   NOT NULL PRIMARY KEY,
            portfolio_id          VARCHAR(36),
            ticker                VARCHAR(20)   NOT NULL,
            captured_at           TIMESTAMP_NTZ NOT NULL,
            assessment_type       VARCHAR(20),
            assessor_id           VARCHAR(255),
            org_air               FLOAT,
            vr_score              FLOAT,
            hr_score              FLOAT,
            synergy_score         FLOAT,
            confidence_lower      FLOAT,
            confidence_upper      FLOAT,
            evidence_count        INTEGER,
            dimension_scores_json VARCHAR,
            created_at            TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
        );
        """
        self.execute_query(sql)

    def insert_snapshot(
        self,
        *,
        ticker: str,
        portfolio_id: Optional[str],
        assessment_type: str,
        assessor_id: str,
        captured_at: datetime,
        org_air: float,
        vr_score: float,
        hr_score: float,
        synergy_score: float,
        confidence_lower: float,
        confidence_upper: float,
        evidence_count: int,
        dimension_scores: Dict[str, float],
    ) -> str:
        self.ensure_table()
        snapshot_id = str(uuid4())
        sql = f"""
        INSERT INTO {self.TABLE_NAME} (
            id, portfolio_id, ticker, captured_at, assessment_type, assessor_id,
            org_air, vr_score, hr_score, synergy_score, confidence_lower, confidence_upper,
            evidence_count, dimension_scores_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s
        )
        """
        params = (
            snapshot_id,
            portfolio_id,
            ticker.upper(),
            captured_at.replace(tzinfo=None),
            assessment_type,
            assessor_id,
            float(org_air),
            float(vr_score),
            float(hr_score),
            float(synergy_score),
            float(confidence_lower),
            float(confidence_upper),
            int(evidence_count),
            json.dumps(dimension_scores or {}),
        )
        self.execute_query(sql, params, commit=True)
        return snapshot_id

    def list_snapshots(
        self,
        *,
        ticker: str,
        portfolio_id: Optional[str] = None,
        days: int = 365,
    ) -> List[Dict[str, Any]]:
        self.ensure_table()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        where = ["UPPER(ticker) = UPPER(%s)", "captured_at >= %s"]
        params: List[Any] = [ticker.upper(), cutoff.replace(tzinfo=None)]
        if portfolio_id:
            where.append("portfolio_id = %s")
            params.append(portfolio_id)
        where_sql = " AND ".join(where)
        sql = f"""
        SELECT
            id, portfolio_id, ticker, captured_at, assessment_type, assessor_id,
            org_air, vr_score, hr_score, synergy_score, confidence_lower, confidence_upper,
            evidence_count, dimension_scores_json
        FROM {self.TABLE_NAME}
        WHERE {where_sql}
        ORDER BY captured_at ASC
        """
        rows = self.execute_query(sql, tuple(params), fetch_all=True) or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            r = self.row_to_dict(row)
            try:
                r["dimension_scores"] = json.loads(r.get("dimension_scores_json") or "{}")
            except Exception:
                r["dimension_scores"] = {}
            out.append(r)
        return out

    def get_entry_org_air(
        self,
        *,
        ticker: str,
        portfolio_id: Optional[str] = None,
    ) -> Optional[float]:
        self.ensure_table()
        where = ["UPPER(ticker) = UPPER(%s)"]
        params: List[Any] = [ticker.upper()]
        if portfolio_id:
            where.append("portfolio_id = %s")
            params.append(portfolio_id)
        where_sql = " AND ".join(where)
        sql = f"""
        SELECT org_air
        FROM {self.TABLE_NAME}
        WHERE {where_sql}
        ORDER BY captured_at ASC
        LIMIT 1
        """
        row = self.execute_query(sql, tuple(params), fetch_one=True)
        if not row:
            return None
        r = self.row_to_dict(row)
        val = r.get("org_air")
        return float(val) if val is not None else None

