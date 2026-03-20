# app/repositories/signal_repository.py
import json
import structlog
from typing import List, Dict, Optional
from uuid import uuid4
from datetime import datetime, timezone
from app.repositories.base import BaseRepository

logger = structlog.get_logger()


class SignalRepository(BaseRepository):
    """Repository for external signals in Snowflake."""


    
    # EXTERNAL SIGNALS CRUD
    

    def create_signal(
        self,
        company_id: str,
        category: str,
        source: str,
        signal_date: datetime,
        raw_value: str,
        normalized_score: float,
        confidence: float,
        metadata: Dict
    ) -> Dict:
        """Create a new external signal record."""
        signal_id = str(uuid4())
        
        # Use INSERT with SELECT for PARSE_JSON to work
        sql = """
        INSERT INTO external_signals (
            id, company_id, category, source, signal_date,
            raw_value, normalized_score, confidence, metadata, created_at
        ) 
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), CURRENT_TIMESTAMP()
        """
        
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (
                    signal_id, company_id, category, source, signal_date,
                    raw_value, normalized_score, confidence, json.dumps(metadata)
                ))
                conn.commit()
                logger.info(f"  💾 Signal saved: {category} | Score: {normalized_score}")
                return {"id": signal_id, "normalized_score": normalized_score}
            except Exception as e:
                logger.error(f"Failed to save signal: {e}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_signals_by_company(self, company_id: str) -> List[Dict]:
        """Get all signals for a company."""
        sql = """
        SELECT id, company_id, category, source, signal_date,
               raw_value, normalized_score, confidence, metadata, created_at
        FROM external_signals
        WHERE company_id = %s
        ORDER BY signal_date DESC
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                columns = [col[0].lower() for col in cur.description]
                results = []
                for row in cur.fetchall():
                    record = dict(zip(columns, row))
                    # Parse metadata if it's a string
                    if record.get('metadata') and isinstance(record['metadata'], str):
                        try:
                            record['metadata'] = json.loads(record['metadata'])
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                    results.append(record)
                return results
            finally:
                cur.close()

    def get_signals_by_ticker(self, ticker: str) -> List[Dict]:
        """Get all signals for a ticker."""
        sql = """
        SELECT es.id, es.company_id, es.category, es.source, es.signal_date,
               es.raw_value, es.normalized_score, es.confidence, es.metadata, es.created_at
        FROM external_signals es
        JOIN companies c ON es.company_id = c.id
        WHERE c.ticker = %s
        ORDER BY es.signal_date DESC
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                columns = [col[0].lower() for col in cur.description]
                results = []
                for row in cur.fetchall():
                    record = dict(zip(columns, row))
                    # Parse metadata if it's a string
                    if record.get('metadata') and isinstance(record['metadata'], str):
                        try:
                            record['metadata'] = json.loads(record['metadata'])
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                    results.append(record)
                return results
            finally:
                cur.close()

    def get_signals_by_category(self, company_id: str, category: str) -> List[Dict]:
        """Get signals by category for a company."""
        sql = """
        SELECT id, company_id, category, source, signal_date,
               raw_value, normalized_score, confidence, metadata, created_at
        FROM external_signals
        WHERE company_id = %s AND category = %s
        ORDER BY signal_date DESC
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id, category))
                columns = [col[0].lower() for col in cur.description]
                results = []
                for row in cur.fetchall():
                    record = dict(zip(columns, row))
                    # Parse metadata if it's a string
                    if record.get('metadata') and isinstance(record['metadata'], str):
                        try:
                            record['metadata'] = json.loads(record['metadata'])
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                    results.append(record)
                return results
            finally:
                cur.close()

    def delete_signals_by_category(self, company_id: str, category: str) -> int:
        """Delete all signals of a category for a company (for re-analysis)."""
        sql = "DELETE FROM external_signals WHERE company_id = %s AND category = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id, category))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    def delete_signals_by_company(self, company_id: str) -> int:
        """Delete all signals for a company."""
        sql = "DELETE FROM external_signals WHERE company_id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    
    # COMPANY SIGNAL SUMMARIES
    

    def get_summary(self, company_id: str) -> Optional[Dict]:
        """Get signal summary for a company."""
        sql = """
        SELECT company_id, ticker, technology_hiring_score, innovation_activity_score,
               digital_presence_score, leadership_signals_score, composite_score,
               signal_count, last_updated
        FROM company_signal_summaries
        WHERE company_id = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def get_summary_by_ticker(self, ticker: str) -> Optional[Dict]:
        """Get signal summary by ticker (most recently updated if duplicates exist)."""
        sql = """
        SELECT company_id, ticker, technology_hiring_score, innovation_activity_score,
               digital_presence_score, leadership_signals_score, composite_score,
               signal_count, last_updated
        FROM company_signal_summaries
        WHERE ticker = %s
        ORDER BY last_updated DESC NULLS LAST
        LIMIT 1
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def get_all_summaries(self) -> List[Dict]:
        """Get all company signal summaries."""
        sql = """
        SELECT company_id, ticker, technology_hiring_score, innovation_activity_score,
               digital_presence_score, leadership_signals_score, composite_score,
               signal_count, last_updated
        FROM company_signal_summaries
        ORDER BY ticker
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def upsert_summary(
        self,
        company_id: str,
        ticker: str,
        leadership_score: Optional[float] = None,
        hiring_score: Optional[float] = None,
        innovation_score: Optional[float] = None,
        digital_score: Optional[float] = None
    ) -> Dict:
        """Insert or update company signal summary."""
        
        # Get actual signal count from external_signals table
        signal_count = self._get_signal_count(company_id)
        
        # Check if exists
        existing = self.get_summary(company_id)
        
        if existing:
            # Update only the provided scores
            updates = []
            params = []
            
            if leadership_score is not None:
                updates.append("leadership_signals_score = %s")
                params.append(leadership_score)
            if hiring_score is not None:
                updates.append("technology_hiring_score = %s")
                params.append(hiring_score)
            if innovation_score is not None:
                updates.append("innovation_activity_score = %s")
                params.append(innovation_score)
            if digital_score is not None:
                updates.append("digital_presence_score = %s")
                params.append(digital_score)
            
            if updates:
                updates.append("last_updated = CURRENT_TIMESTAMP()")
                updates.append("signal_count = %s")
                params.append(signal_count)
                params.append(company_id)
                
                sql = f"UPDATE company_signal_summaries SET {', '.join(updates)} WHERE company_id = %s"
                
                with self.get_connection() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute(sql, tuple(params))
                        conn.commit()
                    finally:
                        cur.close()
        else:
            # Insert new record
            sql = """
            INSERT INTO company_signal_summaries (
                company_id, ticker, leadership_signals_score, technology_hiring_score,
                innovation_activity_score, digital_presence_score, signal_count, last_updated
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
            """
            
            with self.get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute(sql, (
                        company_id, ticker, leadership_score, hiring_score,
                        innovation_score, digital_score, signal_count
                    ))
                    conn.commit()
                finally:
                    cur.close()
        
        # Recalculate composite if all scores present
        self._update_composite(company_id)
        
        return self.get_summary(company_id)

    def _get_signal_count(self, company_id: str) -> int:
        """Get actual count of signals for a company."""
        sql = "SELECT COUNT(*) FROM external_signals WHERE company_id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                cur.close()

    def get_total_signal_count(self) -> int:
        """Get total count of all signals across all companies."""
        sql = "SELECT COUNT(*) FROM external_signals"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                cur.close()

    def _update_composite(self, company_id: str):
        """Recalculate composite score if all 4 signals exist."""
        sql = """
        UPDATE company_signal_summaries
        SET composite_score = (
            0.30 * technology_hiring_score +
            0.25 * innovation_activity_score +
            0.25 * digital_presence_score +
            0.20 * leadership_signals_score
        )
        WHERE company_id = %s
        AND technology_hiring_score IS NOT NULL
        AND innovation_activity_score IS NOT NULL
        AND digital_presence_score IS NOT NULL
        AND leadership_signals_score IS NOT NULL
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                conn.commit()
            finally:
                cur.close()

    def get_category_breakdown(self) -> List[Dict]:
        """Get signal count, avg score, and avg confidence per category."""
        sql = """
        SELECT
            category,
            COUNT(*) as count,
            AVG(normalized_score) as avg_score,
            AVG(confidence) as avg_confidence
        FROM external_signals
        GROUP BY category
        ORDER BY category
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def get_signal_categories_for_ticker(self, ticker: str) -> List[str]:
        """Return distinct signal categories that exist for a ticker."""
        sql = """
        SELECT DISTINCT s.category
        FROM external_signals s
        JOIN companies c ON s.company_id = c.id
        WHERE UPPER(c.ticker) = %s
          AND s.category IS NOT NULL
        ORDER BY s.category
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker.upper(),))
                return [row[0] for row in cur.fetchall()]
            finally:
                cur.close()

    def delete_summary(self, company_id: str) -> bool:
        """Delete signal summary for a company."""
        sql = "DELETE FROM company_signal_summaries WHERE company_id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (company_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                cur.close()


