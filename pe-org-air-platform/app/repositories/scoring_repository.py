"""
Scoring Repository — CS3 Snowflake persistence
app/repositories/scoring_repository.py

Tables:
  - signal_dimension_mapping   (CS3 Table 1 matrix view per ticker)
  - evidence_dimension_scores  (7 aggregated dimension scores per ticker)

Follows existing repo pattern: singleton, get_snowflake_connection(), cursor-based.
"""

import structlog
from typing import Dict, List, Optional
from uuid import uuid4
from app.repositories.base import BaseRepository

logger = structlog.get_logger()


class ScoringRepository(BaseRepository):
    """Repository for CS3 scoring tables in Snowflake."""


    # =====================================================================
    # signal_dimension_mapping — the mapping matrix (Table 1) per ticker
    # =====================================================================

    def upsert_mapping_row(
        self,
        ticker: str,
        source: str,
        raw_score: Optional[float],
        confidence: Optional[float],
        evidence_count: int,
        data_infrastructure: Optional[float],
        ai_governance: Optional[float],
        technology_stack: Optional[float],
        talent_skills: Optional[float],
        leadership_vision: Optional[float],
        use_case_portfolio: Optional[float],
        culture_change: Optional[float],
    ) -> str:
        """Upsert one row into signal_dimension_mapping (MERGE by ticker+source)."""
        row_id = str(uuid4())
        sql = """
        MERGE INTO signal_dimension_mapping t
        USING (SELECT %s AS ticker, %s AS source) s
        ON t.ticker = s.ticker AND t.source = s.source
        WHEN MATCHED THEN UPDATE SET
            raw_score = %s,
            confidence = %s,
            evidence_count = %s,
            data_infrastructure = %s,
            ai_governance = %s,
            technology_stack = %s,
            talent_skills = %s,
            leadership_vision = %s,
            use_case_portfolio = %s,
            culture_change = %s,
            created_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            id, ticker, source, raw_score, confidence, evidence_count,
            data_infrastructure, ai_governance, technology_stack,
            talent_skills, leadership_vision, use_case_portfolio, culture_change,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            CURRENT_TIMESTAMP()
        )
        """
        params = (
            ticker.upper(), source,
            # UPDATE values
            raw_score, confidence, evidence_count,
            data_infrastructure, ai_governance, technology_stack,
            talent_skills, leadership_vision, use_case_portfolio, culture_change,
            # INSERT values
            row_id, ticker.upper(), source, raw_score, confidence, evidence_count,
            data_infrastructure, ai_governance, technology_stack,
            talent_skills, leadership_vision, use_case_portfolio, culture_change,
        )
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                conn.commit()
                return row_id
            except Exception as e:
                logger.error(f"Failed to upsert mapping row: {e}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def upsert_mapping_matrix(self, rows: List[Dict]) -> int:
        """
        Upsert the full mapping matrix for a ticker using one shared connection.

        Args:
            rows: Output of EvidenceMapper.build_mapping_matrix()

        Returns:
            Number of rows upserted
        """
        if not rows:
            return 0
        sql = """
        MERGE INTO signal_dimension_mapping t
        USING (SELECT %s AS ticker, %s AS source) s
        ON t.ticker = s.ticker AND t.source = s.source
        WHEN MATCHED THEN UPDATE SET
            raw_score = %s,
            confidence = %s,
            evidence_count = %s,
            data_infrastructure = %s,
            ai_governance = %s,
            technology_stack = %s,
            talent_skills = %s,
            leadership_vision = %s,
            use_case_portfolio = %s,
            culture_change = %s,
            created_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            id, ticker, source, raw_score, confidence, evidence_count,
            data_infrastructure, ai_governance, technology_stack,
            talent_skills, leadership_vision, use_case_portfolio, culture_change,
            created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            CURRENT_TIMESTAMP()
        )
        """
        count = 0
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                for row in rows:
                    row_id = str(uuid4())
                    params = (
                        row["ticker"].upper(), row["source"],
                        # UPDATE values
                        row.get("raw_score"), row.get("confidence"), row.get("evidence_count", 0),
                        row.get("data_infrastructure"), row.get("ai_governance"),
                        row.get("technology_stack"), row.get("talent_skills"),
                        row.get("leadership_vision"), row.get("use_case_portfolio"),
                        row.get("culture_change"),
                        # INSERT values
                        row_id, row["ticker"].upper(), row["source"],
                        row.get("raw_score"), row.get("confidence"), row.get("evidence_count", 0),
                        row.get("data_infrastructure"), row.get("ai_governance"),
                        row.get("technology_stack"), row.get("talent_skills"),
                        row.get("leadership_vision"), row.get("use_case_portfolio"),
                        row.get("culture_change"),
                    )
                    cur.execute(sql, params)
                    count += 1
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                cur.close()
        logger.info(f"Upserted {count} mapping rows for {rows[0]['ticker'] if rows else '?'}")
        return count

    def get_mapping_matrix(self, ticker: str) -> List[Dict]:
        """Get the full mapping matrix for a ticker (Table 1 view)."""
        sql = """
        SELECT ticker, source, raw_score, confidence, evidence_count,
               data_infrastructure, ai_governance, technology_stack,
               talent_skills, leadership_vision, use_case_portfolio, culture_change,
               created_at
        FROM signal_dimension_mapping
        WHERE ticker = %s
        ORDER BY CASE source
            WHEN 'technology_hiring' THEN 1
            WHEN 'innovation_activity' THEN 2
            WHEN 'digital_presence' THEN 3
            WHEN 'leadership_signals' THEN 4
            WHEN 'sec_item_1' THEN 5
            WHEN 'sec_item_1a' THEN 6
            WHEN 'sec_item_7' THEN 7
            WHEN 'glassdoor_reviews' THEN 8
            WHEN 'board_composition' THEN 9
            ELSE 10
        END
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def get_all_mapping_matrices(self) -> List[Dict]:
        """Get mapping matrices for all tickers."""
        sql = """
        SELECT ticker, source, raw_score, confidence, evidence_count,
               data_infrastructure, ai_governance, technology_stack,
               talent_skills, leadership_vision, use_case_portfolio, culture_change
        FROM signal_dimension_mapping
        ORDER BY ticker, CASE source
            WHEN 'technology_hiring' THEN 1
            WHEN 'innovation_activity' THEN 2
            WHEN 'digital_presence' THEN 3
            WHEN 'leadership_signals' THEN 4
            WHEN 'sec_item_1' THEN 5
            WHEN 'sec_item_1a' THEN 6
            WHEN 'sec_item_7' THEN 7
            WHEN 'glassdoor_reviews' THEN 8
            WHEN 'board_composition' THEN 9
            ELSE 10
        END
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def delete_mapping_matrix(self, ticker: str) -> int:
        """Delete all mapping rows for a ticker."""
        sql = "DELETE FROM signal_dimension_mapping WHERE ticker = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    # =====================================================================
    # evidence_dimension_scores — 7 aggregated dimension scores per ticker
    # =====================================================================

    def upsert_dimension_score(
        self,
        ticker: str,
        dimension: str,
        score: float,
        confidence: float,
        source_count: int,
        sources: str,
        total_weight: float,
    ) -> str:
        """Upsert one dimension score (MERGE by ticker+dimension)."""
        row_id = str(uuid4())
        sql = """
        MERGE INTO evidence_dimension_scores t
        USING (SELECT %s AS ticker, %s AS dimension) s
        ON t.ticker = s.ticker AND t.dimension = s.dimension
        WHEN MATCHED THEN UPDATE SET
            score = %s,
            confidence = %s,
            source_count = %s,
            sources = %s,
            total_weight = %s,
            created_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            id, ticker, dimension, score, confidence, source_count, sources, total_weight, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP()
        )
        """
        params = (
            ticker.upper(), dimension,
            # UPDATE
            score, confidence, source_count, sources, total_weight,
            # INSERT
            row_id, ticker.upper(), dimension, score, confidence, source_count, sources, total_weight,
        )
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                conn.commit()
                return row_id
            except Exception as e:
                logger.error(f"Failed to upsert dimension score: {e}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def upsert_dimension_scores(self, rows: List[Dict]) -> int:
        """
        Upsert all 7 dimension scores for a ticker using one shared connection.

        Args:
            rows: Output of EvidenceMapper.build_dimension_summary()

        Returns:
            Number of rows upserted
        """
        if not rows:
            return 0
        sql = """
        MERGE INTO evidence_dimension_scores t
        USING (SELECT %s AS ticker, %s AS dimension) s
        ON t.ticker = s.ticker AND t.dimension = s.dimension
        WHEN MATCHED THEN UPDATE SET
            score = %s,
            confidence = %s,
            source_count = %s,
            sources = %s,
            total_weight = %s,
            created_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            id, ticker, dimension, score, confidence, source_count, sources, total_weight, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP()
        )
        """
        count = 0
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                for row in rows:
                    row_id = str(uuid4())
                    params = (
                        row["ticker"].upper(), row["dimension"],
                        # UPDATE values
                        row["score"], row["confidence"], row["source_count"],
                        row["sources"], row["total_weight"],
                        # INSERT values
                        row_id, row["ticker"].upper(), row["dimension"],
                        row["score"], row["confidence"], row["source_count"],
                        row["sources"], row["total_weight"],
                    )
                    cur.execute(sql, params)
                    count += 1
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                cur.close()
        logger.info(f"Upserted {count} dimension scores for {rows[0]['ticker'] if rows else '?'}")
        return count

    def get_dimension_scores(self, ticker: str) -> List[Dict]:
        """Get all 7 dimension scores for a ticker."""
        sql = """
        SELECT ticker, dimension, score, confidence, source_count, sources, total_weight, created_at
        FROM evidence_dimension_scores
        WHERE ticker = %s
        ORDER BY CASE dimension
            WHEN 'data_infrastructure' THEN 1
            WHEN 'ai_governance' THEN 2
            WHEN 'technology_stack' THEN 3
            WHEN 'talent_skills' THEN 4
            WHEN 'leadership_vision' THEN 5
            WHEN 'use_case_portfolio' THEN 6
            WHEN 'culture_change' THEN 7
            ELSE 8
        END
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def get_all_dimension_scores(self) -> List[Dict]:
        """Get dimension scores for all tickers."""
        sql = """
        SELECT ticker, dimension, score, confidence, source_count, sources, total_weight
        FROM evidence_dimension_scores
        ORDER BY ticker, CASE dimension
            WHEN 'data_infrastructure' THEN 1
            WHEN 'ai_governance' THEN 2
            WHEN 'technology_stack' THEN 3
            WHEN 'talent_skills' THEN 4
            WHEN 'leadership_vision' THEN 5
            WHEN 'use_case_portfolio' THEN 6
            WHEN 'culture_change' THEN 7
            ELSE 8
        END
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def delete_dimension_scores(self, ticker: str) -> int:
        """Delete dimension scores for a ticker."""
        sql = "DELETE FROM evidence_dimension_scores WHERE ticker = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    # =====================================================================
    # scoring_runs — Phase 3A run tracking
    # =====================================================================

    def create_scoring_run(self, run_id: str, ticker: str) -> None:
        sql = """
        INSERT INTO scoring_runs (run_id, ticker, status, started_at)
        VALUES (%s, %s, 'running', CURRENT_TIMESTAMP())
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (run_id, ticker.upper()))
                conn.commit()
            finally:
                cur.close()

    def complete_scoring_run(self, run_id: str, dimensions_written: int) -> None:
        sql = """
        UPDATE scoring_runs
        SET status = 'completed', completed_at = CURRENT_TIMESTAMP(),
            dimensions_written = %s
        WHERE run_id = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (dimensions_written, run_id))
                conn.commit()
            finally:
                cur.close()

    def fail_scoring_run(self, run_id: str, error_message: str) -> None:
        sql = """
        UPDATE scoring_runs
        SET status = 'failed', completed_at = CURRENT_TIMESTAMP(),
            error_message = %s
        WHERE run_id = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (str(error_message)[:2000], run_id))
                conn.commit()
            finally:
                cur.close()

    def get_latest_scoring_run(self, ticker: str) -> Optional[Dict]:
        sql = """
        SELECT run_id, ticker, status, started_at, completed_at,
               dimensions_written, error_message
        FROM scoring_runs
        WHERE ticker = %s
        ORDER BY started_at DESC
        LIMIT 1
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def upsert_culture_mapping(self, ticker: str, signal_data: dict) -> bool:
        """
        Upsert the glassdoor_reviews row into signal_dimension_mapping.

        CS3 Table 1 fixed weights for glassdoor_reviews:
          talent_skills = 0.10, leadership_vision = 0.10, culture_change = 0.80
        """
        try:
            overall = signal_data.get("overall_score", 0)
            confidence = signal_data.get("confidence", 0)
            review_count = signal_data.get("review_count", 0)
            self.upsert_mapping_row(
                ticker=ticker.upper(),
                source="glassdoor_reviews",
                raw_score=float(overall) if overall else None,
                confidence=float(confidence) if confidence else None,
                evidence_count=int(review_count),
                data_infrastructure=None,
                ai_governance=None,
                technology_stack=None,
                talent_skills=0.100,
                leadership_vision=0.100,
                use_case_portfolio=None,
                culture_change=0.800,
            )
            return True
        except Exception as e:
            logger.error(f"[{ticker}] upsert_culture_mapping failed: {e}", exc_info=True)
            return False


