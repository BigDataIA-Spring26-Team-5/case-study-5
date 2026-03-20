"""
Health Repository - PE Org-AI-R Platform
app/repositories/health_repository.py

Snowflake queries needed by the health router. All connections go through
BaseRepository.get_connection() -> get_snowflake_connection() (single gateway).
"""
from typing import Dict, List, Tuple

from app.repositories.base import BaseRepository

PLATFORM_TABLES: List[str] = [
    "INDUSTRIES", "COMPANIES", "ASSESSMENTS", "DIMENSION_SCORES",
    "DOCUMENTS", "DOCUMENT_CHUNKS", "EXTERNAL_SIGNALS",
    "COMPANY_SIGNAL_SUMMARIES", "SIGNAL_SCORES",
    "SIGNAL_DIMENSION_MAPPING", "EVIDENCE_DIMENSION_SCORES",
]


class HealthRepository(BaseRepository):

    def ping(self) -> Tuple[str, str]:
        """Execute SELECT CURRENT_USER(), CURRENT_ROLE() and return both values."""
        with self.get_cursor(dict_cursor=False) as cur:
            cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE()")
            row = cur.fetchone()
        return row[0], row[1]

    def get_table_counts(
        self, tables: List[str] = PLATFORM_TABLES
    ) -> Dict[str, int]:
        """Return COUNT(*) for each table. Returns 0 on per-table error, -1 if connection fails."""
        counts: Dict[str, int] = {}
        with self.get_connection() as conn:
            cur = conn.cursor()
            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    counts[t] = cur.fetchone()[0]
                except Exception:
                    counts[t] = 0
            cur.close()
        return counts


