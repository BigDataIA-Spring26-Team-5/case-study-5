"""
Composite Scoring Repository — PE Org-AI-R Platform
app/repositories/composite_scoring_repository.py

Unified read + write access to the SCORING family of tables:
  - SCORING        (composite row per ticker: tc, vr, pf, hr, org_air, …)
  - TC_SCORING     (Talent Concentration detail)
  - VR_SCORING     (Valuation Readiness detail)
  - PF_SCORING     (Position Factor detail)
  - HR_SCORING     (Human Readiness detail)

The four read-only fetch helpers (fetch_tc_vr_row, fetch_pf_row, fetch_hr_row,
fetch_orgair_row) migrated from scoring_read_repository.py are preserved unchanged.

The eight write helpers (upsert_*) are migrated verbatim from the router files —
no SQL semantics were altered.
"""
from typing import Dict, List, Optional, Any

from app.repositories.base import BaseRepository


class CompositeScoringRepository(BaseRepository):
    """Read + write access to SCORING-family tables in Snowflake."""

    # =====================================================================
    # Internal helpers
    # =====================================================================

    def _query(self, ticker: str, columns: List[str]) -> Optional[Dict]:
        """Execute SELECT <columns> FROM SCORING WHERE ticker = %s."""
        cols = ", ".join(columns)
        sql = f"SELECT {cols} FROM SCORING WHERE ticker = %s"
        return self.execute_query(sql, [ticker.upper()], fetch_one=True)

    def _execute(self, sql: str, params: list) -> None:
        """Execute a DML statement (MERGE/INSERT/UPDATE) and commit."""
        self.execute_query(sql, params, commit=True)

    # =====================================================================
    # READ — fetch rows from SCORING
    # =====================================================================

    def fetch_tc_vr_row(self, ticker: str) -> Optional[Dict]:
        """Fetch TC, VR, PF, HR columns for one ticker."""
        return self._query(ticker, ["ticker", "tc", "vr", "pf", "hr", "scored_at", "updated_at"])

    def fetch_pf_row(self, ticker: str) -> Optional[Dict]:
        """Fetch PF column for one ticker."""
        return self._query(ticker, ["ticker", "pf", "scored_at", "updated_at"])

    def fetch_hr_row(self, ticker: str) -> Optional[Dict]:
        """Fetch HR column for one ticker."""
        return self._query(ticker, ["ticker", "hr", "scored_at", "updated_at"])

    def fetch_orgair_row(self, ticker: str) -> Optional[Dict]:
        """Fetch ORG_AIR column for one ticker."""
        return self._query(ticker, ["ticker", "org_air", "scored_at", "updated_at"])

    # =====================================================================
    # WRITE — upsert into SCORING (main table)
    # =====================================================================

    def upsert_scoring_table(
        self,
        ticker: str,
        tc: Optional[float] = None,
        vr: Optional[float] = None,
        pf: Optional[float] = None,
        hr: Optional[float] = None,
    ) -> None:
        """MERGE INTO SCORING — updates only the provided columns, preserving existing values."""
        sql = """
            MERGE INTO SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                tc         = COALESCE(%s, tgt.tc),
                vr         = COALESCE(%s, tgt.vr),
                pf         = COALESCE(%s, tgt.pf),
                hr         = COALESCE(%s, tgt.hr),
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (ticker, tc, vr, pf, hr, scored_at, updated_at)
            VALUES
                (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        self._execute(sql, [ticker, tc, vr, pf, hr, ticker, tc, vr, pf, hr])

    def upsert_scoring_pf(self, ticker: str, pf: Optional[float]) -> None:
        """MERGE INTO SCORING — updates only the pf column, preserving existing tc/vr/hr."""
        sql = """
            MERGE INTO SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                pf         = %s,
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (ticker, pf, scored_at, updated_at)
            VALUES
                (%s, %s, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        self._execute(sql, [ticker, pf, ticker, pf])

    def upsert_scoring_hr(self, ticker: str, hr: Optional[float]) -> None:
        """MERGE INTO SCORING — updates only the hr column, preserving existing tc/vr/pf."""
        sql = """
            MERGE INTO SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                hr         = %s,
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (ticker, hr, scored_at, updated_at)
            VALUES
                (%s, %s, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        self._execute(sql, [ticker, hr, ticker, hr])

    def upsert_orgair_result(
        self,
        ticker: str,
        org_air: Optional[float],
        vr_score: Optional[float],
        hr_score: Optional[float],
        synergy_score: Optional[float],
        ci_lower: Optional[float],
        ci_upper: Optional[float],
    ) -> None:
        """MERGE INTO SCORING — updates Org-AI-R fields."""
        sql = """
            MERGE INTO SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                org_air = %s, vr_score = %s, hr_score = %s,
                synergy_score = %s, ci_lower = %s, ci_upper = %s,
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (ticker, org_air, vr_score, hr_score, synergy_score,
                 ci_lower, ci_upper, scored_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        self._execute(sql, [
            ticker, org_air, vr_score, hr_score,
            synergy_score, ci_lower, ci_upper,
            ticker, org_air, vr_score, hr_score,
            synergy_score, ci_lower, ci_upper,
        ])

    # =====================================================================
    # WRITE — upsert into TC_SCORING
    # =====================================================================

    def upsert_tc_result(
        self,
        ticker: str,
        tc: Optional[float],
        leadership_ratio: Optional[float],
        team_size_factor: Optional[float],
        skill_concentration: Optional[float],
        individual_factor: Optional[float],
        total_ai_jobs: Optional[int],
        senior_ai_jobs: Optional[int],
        mid_ai_jobs: Optional[int],
        entry_ai_jobs: Optional[int],
        unique_skills_cnt: Optional[int],
        individual_mentions: Optional[int],
        review_count: Optional[int],
        ai_mentions: Optional[int],
        tc_in_range: Optional[bool],
        tc_expected: Optional[str],
    ) -> None:
        """MERGE all TC sub-components into TC_SCORING."""
        sql = """
            MERGE INTO TC_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                talent_concentration  = %s,
                leadership_ratio      = %s,
                team_size_factor      = %s,
                skill_concentration   = %s,
                individual_factor     = %s,
                total_ai_jobs         = %s,
                senior_ai_jobs        = %s,
                mid_ai_jobs           = %s,
                entry_ai_jobs         = %s,
                unique_skills_count   = %s,
                individual_mentions   = %s,
                review_count          = %s,
                ai_mentions           = %s,
                tc_in_range           = %s,
                tc_expected           = %s,
                updated_at            = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, talent_concentration, leadership_ratio, team_size_factor,
                skill_concentration, individual_factor, total_ai_jobs, senior_ai_jobs,
                mid_ai_jobs, entry_ai_jobs, unique_skills_count, individual_mentions,
                review_count, ai_mentions, tc_in_range, tc_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        params = [
            # USING clause
            ticker,
            # UPDATE SET
            tc, leadership_ratio, team_size_factor, skill_concentration, individual_factor,
            total_ai_jobs, senior_ai_jobs, mid_ai_jobs, entry_ai_jobs, unique_skills_cnt,
            individual_mentions, review_count, ai_mentions, tc_in_range, tc_expected,
            # INSERT VALUES
            ticker, tc, leadership_ratio, team_size_factor, skill_concentration,
            individual_factor, total_ai_jobs, senior_ai_jobs, mid_ai_jobs, entry_ai_jobs,
            unique_skills_cnt, individual_mentions, review_count, ai_mentions,
            tc_in_range, tc_expected,
        ]
        self._execute(sql, params)

    # =====================================================================
    # WRITE — upsert into VR_SCORING
    # =====================================================================

    # =====================================================================
    # WRITE — batch: SCORING + TC_SCORING + VR_SCORING in one connection
    # =====================================================================

    def upsert_tc_vr_batch(
        self,
        ticker: str,
        tc: Optional[float],
        vr: Optional[float],
        leadership_ratio: Optional[float],
        team_size_factor: Optional[float],
        skill_concentration: Optional[float],
        individual_factor: Optional[float],
        total_ai_jobs: Optional[int],
        senior_ai_jobs: Optional[int],
        mid_ai_jobs: Optional[int],
        entry_ai_jobs: Optional[int],
        unique_skills_cnt: Optional[int],
        individual_mentions: Optional[int],
        review_count: Optional[int],
        ai_mentions: Optional[int],
        tc_in_range: Optional[bool],
        tc_expected: Optional[str],
        vr_score: Optional[float],
        weighted_dim_score: Optional[float],
        talent_risk_adj: Optional[float],
        dim_data_infra: Optional[float],
        dim_ai_gov: Optional[float],
        dim_tech_stack: Optional[float],
        dim_talent: Optional[float],
        dim_leadership: Optional[float],
        dim_use_case: Optional[float],
        dim_culture: Optional[float],
        vr_in_range: Optional[bool],
        vr_expected: Optional[str],
    ) -> None:
        """Run MERGE into SCORING, TC_SCORING, and VR_SCORING in one shared connection."""
        scoring_sql = """
            MERGE INTO SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                tc         = COALESCE(%s, tgt.tc),
                vr         = COALESCE(%s, tgt.vr),
                pf         = COALESCE(%s, tgt.pf),
                hr         = COALESCE(%s, tgt.hr),
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT
                (ticker, tc, vr, pf, hr, scored_at, updated_at)
            VALUES
                (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        tc_sql = """
            MERGE INTO TC_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                talent_concentration  = %s,
                leadership_ratio      = %s,
                team_size_factor      = %s,
                skill_concentration   = %s,
                individual_factor     = %s,
                total_ai_jobs         = %s,
                senior_ai_jobs        = %s,
                mid_ai_jobs           = %s,
                entry_ai_jobs         = %s,
                unique_skills_count   = %s,
                individual_mentions   = %s,
                review_count          = %s,
                ai_mentions           = %s,
                tc_in_range           = %s,
                tc_expected           = %s,
                updated_at            = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, talent_concentration, leadership_ratio, team_size_factor,
                skill_concentration, individual_factor, total_ai_jobs, senior_ai_jobs,
                mid_ai_jobs, entry_ai_jobs, unique_skills_count, individual_mentions,
                review_count, ai_mentions, tc_in_range, tc_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        vr_sql = """
            MERGE INTO VR_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                vr_score                = %s,
                weighted_dim_score      = %s,
                talent_risk_adj         = %s,
                tc_used                 = %s,
                dim_data_infrastructure = %s,
                dim_ai_governance       = %s,
                dim_technology_stack    = %s,
                dim_talent_skills       = %s,
                dim_leadership_vision   = %s,
                dim_use_case_portfolio  = %s,
                dim_culture_change      = %s,
                vr_in_range             = %s,
                vr_expected             = %s,
                updated_at              = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, vr_score, weighted_dim_score, talent_risk_adj, tc_used,
                dim_data_infrastructure, dim_ai_governance, dim_technology_stack,
                dim_talent_skills, dim_leadership_vision, dim_use_case_portfolio,
                dim_culture_change, vr_in_range, vr_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(scoring_sql, [ticker, tc, vr, None, None, ticker, tc, vr, None, None])
                cur.execute(tc_sql, [
                    ticker,
                    tc, leadership_ratio, team_size_factor, skill_concentration, individual_factor,
                    total_ai_jobs, senior_ai_jobs, mid_ai_jobs, entry_ai_jobs, unique_skills_cnt,
                    individual_mentions, review_count, ai_mentions, tc_in_range, tc_expected,
                    ticker, tc, leadership_ratio, team_size_factor, skill_concentration,
                    individual_factor, total_ai_jobs, senior_ai_jobs, mid_ai_jobs, entry_ai_jobs,
                    unique_skills_cnt, individual_mentions, review_count, ai_mentions,
                    tc_in_range, tc_expected,
                ])
                cur.execute(vr_sql, [
                    ticker,
                    vr_score, weighted_dim_score, talent_risk_adj, tc,
                    dim_data_infra, dim_ai_gov, dim_tech_stack, dim_talent,
                    dim_leadership, dim_use_case, dim_culture, vr_in_range, vr_expected,
                    ticker, vr_score, weighted_dim_score, talent_risk_adj, tc,
                    dim_data_infra, dim_ai_gov, dim_tech_stack, dim_talent,
                    dim_leadership, dim_use_case, dim_culture, vr_in_range, vr_expected,
                ])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def upsert_vr_result(
        self,
        ticker: str,
        vr_score: Optional[float],
        weighted_dim_score: Optional[float],
        talent_risk_adj: Optional[float],
        tc_used: Optional[float],
        dim_data_infra: Optional[float],
        dim_ai_gov: Optional[float],
        dim_tech_stack: Optional[float],
        dim_talent: Optional[float],
        dim_leadership: Optional[float],
        dim_use_case: Optional[float],
        dim_culture: Optional[float],
        vr_in_range: Optional[bool],
        vr_expected: Optional[str],
    ) -> None:
        """MERGE all VR sub-components into VR_SCORING."""
        sql = """
            MERGE INTO VR_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                vr_score                = %s,
                weighted_dim_score      = %s,
                talent_risk_adj         = %s,
                tc_used                 = %s,
                dim_data_infrastructure = %s,
                dim_ai_governance       = %s,
                dim_technology_stack    = %s,
                dim_talent_skills       = %s,
                dim_leadership_vision   = %s,
                dim_use_case_portfolio  = %s,
                dim_culture_change      = %s,
                vr_in_range             = %s,
                vr_expected             = %s,
                updated_at              = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, vr_score, weighted_dim_score, talent_risk_adj, tc_used,
                dim_data_infrastructure, dim_ai_governance, dim_technology_stack,
                dim_talent_skills, dim_leadership_vision, dim_use_case_portfolio,
                dim_culture_change, vr_in_range, vr_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        params = [
            # USING clause
            ticker,
            # UPDATE SET
            vr_score, weighted_dim_score, talent_risk_adj, tc_used,
            dim_data_infra, dim_ai_gov, dim_tech_stack, dim_talent,
            dim_leadership, dim_use_case, dim_culture, vr_in_range, vr_expected,
            # INSERT VALUES
            ticker, vr_score, weighted_dim_score, talent_risk_adj, tc_used,
            dim_data_infra, dim_ai_gov, dim_tech_stack, dim_talent,
            dim_leadership, dim_use_case, dim_culture, vr_in_range, vr_expected,
        ]
        self._execute(sql, params)

    # =====================================================================
    # WRITE — upsert into PF_SCORING
    # =====================================================================

    def upsert_pf_result(
        self,
        ticker: str,
        position_factor: Optional[float],
        vr_score_used: Optional[float],
        sector: Optional[str],
        sector_avg_vr: Optional[float],
        vr_diff: Optional[float],
        vr_component: Optional[float],
        market_cap_percentile: Optional[float],
        mcap_component: Optional[float],
        pf_in_range: Optional[bool],
        pf_expected: Optional[str],
    ) -> None:
        """MERGE all PF sub-components into PF_SCORING."""
        sql = """
            MERGE INTO PF_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                position_factor       = %s,
                vr_score_used         = %s,
                sector                = %s,
                sector_avg_vr         = %s,
                vr_diff               = %s,
                vr_component          = %s,
                market_cap_percentile = %s,
                mcap_component        = %s,
                pf_in_range           = %s,
                pf_expected           = %s,
                updated_at            = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, position_factor, vr_score_used, sector, sector_avg_vr,
                vr_diff, vr_component, market_cap_percentile, mcap_component,
                pf_in_range, pf_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        params = [
            # USING clause
            ticker,
            # UPDATE SET
            position_factor, vr_score_used, sector, sector_avg_vr,
            vr_diff, vr_component, market_cap_percentile, mcap_component,
            pf_in_range, pf_expected,
            # INSERT VALUES
            ticker, position_factor, vr_score_used, sector, sector_avg_vr,
            vr_diff, vr_component, market_cap_percentile, mcap_component,
            pf_in_range, pf_expected,
        ]
        self._execute(sql, params)

    # =====================================================================
    # WRITE — upsert into HR_SCORING
    # =====================================================================

    def upsert_hr_result(
        self,
        ticker: str,
        hr_score: Optional[float],
        hr_base: Optional[float],
        position_factor_used: Optional[float],
        position_adjustment: Optional[float],
        sector: Optional[str],
        interpretation: Optional[str],
        hr_in_range: Optional[bool],
        hr_expected: Optional[str],
    ) -> None:
        """MERGE all H^R sub-components into HR_SCORING."""
        sql = """
            MERGE INTO HR_SCORING AS tgt
            USING (SELECT %s AS ticker) AS src
            ON tgt.ticker = src.ticker
            WHEN MATCHED THEN UPDATE SET
                hr_score             = %s,
                hr_base              = %s,
                position_factor_used = %s,
                position_adjustment  = %s,
                sector               = %s,
                interpretation       = %s,
                hr_in_range          = %s,
                hr_expected          = %s,
                updated_at           = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                ticker, hr_score, hr_base, position_factor_used, position_adjustment,
                sector, interpretation, hr_in_range, hr_expected, scored_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
        """
        params = [
            # USING clause
            ticker,
            # UPDATE SET
            hr_score, hr_base, position_factor_used, position_adjustment,
            sector, interpretation, hr_in_range, hr_expected,
            # INSERT VALUES
            ticker, hr_score, hr_base, position_factor_used, position_adjustment,
            sector, interpretation, hr_in_range, hr_expected,
        ]
        self._execute(sql, params)


