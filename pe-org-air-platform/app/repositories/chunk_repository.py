from typing import List, Dict, Optional
from uuid import uuid4
import structlog
from app.repositories.base import BaseRepository

logger = structlog.get_logger()


class ChunkRepository(BaseRepository):
    """Repository for document chunk METADATA in Snowflake (content stored in S3)"""

    def create(
        self,
        document_id: str,
        chunk_index: int,
        section: Optional[str],
        start_char: int,
        end_char: int,
        word_count: int,
        s3_key: str
    ) -> Dict:
        """Create a single chunk metadata record"""
        chunk_id = str(uuid4())

        sql = """
        INSERT INTO document_chunks (
            id, document_id, chunk_index, section,
            start_char, end_char, word_count, s3_key, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (
                    chunk_id, document_id, chunk_index, section,
                    start_char, end_char, word_count, s3_key
                ))
                conn.commit()
                return {"id": chunk_id, "chunk_index": chunk_index}
            except Exception as e:
                logger.error(f"Failed to save chunk metadata: {e}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def create_batch(
        self,
        document_id: str,
        chunks: list,
        s3_key: str
    ) -> int:
        """Batch insert multiple chunk metadata records (MUCH FASTER)"""
        if not chunks:
            return 0

        sql = """
        INSERT INTO document_chunks (
            id, document_id, chunk_index, section,
            start_char, end_char, word_count, s3_key, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP())
        """

        # Prepare batch data
        batch_data = []
        for chunk in chunks:
            chunk_id = str(uuid4())
            batch_data.append((
                chunk_id,
                document_id,
                chunk.chunk_index,
                chunk.section,
                chunk.start_char,
                chunk.end_char,
                chunk.word_count,
                s3_key
            ))

        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(sql, batch_data)
                conn.commit()
                return len(batch_data)
            except Exception as e:
                logger.error(f"Failed to batch insert chunks: {e}")
                conn.rollback()
                raise
            finally:
                cur.close()

    def get_by_document_id(self, document_id: str) -> List[Dict]:
        """Get all chunk metadata for a document"""
        sql = """
        SELECT id, document_id, chunk_index, section,
               start_char, end_char, word_count, s3_key, created_at
        FROM document_chunks
        WHERE document_id = %s
        ORDER BY chunk_index
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (document_id,))
                columns = [col[0].lower() for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                cur.close()

    def get_by_id(self, chunk_id: str) -> Optional[Dict]:
        """Get a chunk by ID"""
        sql = """
        SELECT id, document_id, chunk_index, section,
               start_char, end_char, word_count, s3_key, created_at
        FROM document_chunks
        WHERE id = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (chunk_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [col[0].lower() for col in cur.description]
                return dict(zip(columns, row))
            finally:
                cur.close()

    def delete_by_document_id(self, document_id: str) -> int:
        """Delete all chunk metadata for a document"""
        sql = "DELETE FROM document_chunks WHERE document_id = %s"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (document_id,))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    def delete_by_ticker(self, ticker: str) -> int:
        """Delete all chunk metadata for a ticker"""
        sql = """
        DELETE FROM document_chunks
        WHERE document_id IN (SELECT id FROM documents WHERE ticker = %s)
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    def count_by_ticker(self, ticker: str) -> int:
        """Get total chunk count for a ticker"""
        sql = """
        SELECT COUNT(*)
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.ticker = %s
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                cur.close()

    def get_stats_by_ticker(self, ticker: str) -> Dict:
        """Get chunk statistics for a ticker"""
        sql = """
        SELECT
            d.filing_type,
            COUNT(dc.id) as chunk_count,
            SUM(dc.word_count) as total_words
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.ticker = %s
        GROUP BY d.filing_type
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (ticker,))
                results = {}
                for row in cur.fetchall():
                    results[row[0]] = {
                        "chunk_count": row[1],
                        "total_words": row[2] or 0
                    }
                return results
            finally:
                cur.close()

    def get_total_chunks(self) -> int:
        """Get total number of chunks across all documents"""
        sql = "SELECT COUNT(*) FROM document_chunks"
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                cur.close()

    def get_s3_keys_by_sections(
        self,
        ticker: str,
        sections: List[str],
        filing_type: str = "10-K",
    ) -> List[str]:
        """
        Return distinct S3 keys for chunks whose section matches one of the
        given names (case-insensitive) for a specific ticker and filing type.
        """
        if not sections:
            return []
        placeholders = ", ".join(["%s"] * len(sections))
        sql = f"""
        SELECT DISTINCT dc.s3_key
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.ticker = %s
        AND d.filing_type = %s
        AND LOWER(dc.section) IN ({placeholders})
        AND d.status IN ('chunked', 'indexed', 'parsed')
        AND dc.s3_key IS NOT NULL
        """
        params = [ticker.upper(), filing_type] + [s.lower() for s in sections]
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                return [row[0] for row in cur.fetchall() if row[0]]
            finally:
                cur.close()

    def get_s3_keys_for_section_map(
        self,
        ticker: str,
        section_map: Dict[str, List[str]],
        filing_type: str = "10-K",
    ) -> Dict[str, List[str]]:
        """
        Return {section_map_key: [s3_keys]} for all section groups in one query.

        Args:
            ticker: Company ticker symbol
            section_map: e.g. {"sec_item_1": ["business", "item_1_business", ...], ...}
            filing_type: Document filing type filter

        Returns:
            Dict mapping each section_map key to its list of distinct S3 keys
        """
        # Build reverse lookup: section_name_lower -> [map_keys]
        reverse: Dict[str, List[str]] = {}
        for map_key, section_names in section_map.items():
            for name in section_names:
                reverse.setdefault(name.lower(), []).append(map_key)

        all_sections = list(reverse.keys())
        if not all_sections:
            return {k: [] for k in section_map}

        placeholders = ", ".join(["%s"] * len(all_sections))
        sql = f"""
        SELECT DISTINCT LOWER(dc.section), dc.s3_key
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.ticker = %s
        AND d.filing_type = %s
        AND LOWER(dc.section) IN ({placeholders})
        AND d.status IN ('chunked', 'indexed', 'parsed')
        AND dc.s3_key IS NOT NULL
        """
        params = [ticker.upper(), filing_type] + all_sections

        result: Dict[str, set] = {k: set() for k in section_map}
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                for section_lower, s3_key in cur.fetchall():
                    if not s3_key:
                        continue
                    for map_key in reverse.get(section_lower, []):
                        result[map_key].add(s3_key)
            finally:
                cur.close()

        return {k: sorted(v) for k, v in result.items()}

    def get_all_s3_keys(
        self,
        ticker: str,
        filing_type: str = "10-K",
    ) -> List[str]:
        """
        Return distinct S3 keys for all chunks for a ticker and filing type,
        regardless of section. Used as a fallback when section-specific
        extraction yields too little text.
        """
        sql = """
        SELECT DISTINCT dc.s3_key
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE d.ticker = %s
        AND d.filing_type = %s
        AND d.status IN ('chunked', 'indexed', 'parsed')
        AND dc.s3_key IS NOT NULL
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, [ticker.upper(), filing_type])
                return [row[0] for row in cur.fetchall() if row[0]]
            finally:
                cur.close()


