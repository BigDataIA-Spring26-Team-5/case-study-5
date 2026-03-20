"""
Base Repository - PE Org-AI-R Platform
app/repositories/base.py

Base repository class with Snowflake connection management and common utilities.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional
from uuid import UUID

import snowflake.connector
from snowflake.connector import DictCursor
from snowflake.connector.errors import DatabaseError, InterfaceError, ProgrammingError

from app.core.settings import settings

class RepositoryException(Exception):
    """Base exception for repository-layer errors."""


class DatabaseConnectionException(RepositoryException):
    """Raised when a Snowflake connection cannot be established."""


class DuplicateEntityException(RepositoryException):
    """Raised on UNIQUE constraint violation."""


class ForeignKeyViolationException(RepositoryException):
    """Raised on FOREIGN KEY constraint violation."""


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    """
    Snowflake connection factory.
    This is the single authoritative location for creating Snowflake connections.
    """
    return snowflake.connector.connect(
        account=settings.SNOWFLAKE_ACCOUNT,
        user=settings.SNOWFLAKE_USER,
        password=settings.SNOWFLAKE_PASSWORD.get_secret_value(),
        warehouse=settings.SNOWFLAKE_WAREHOUSE,
        database=settings.SNOWFLAKE_DATABASE,
        schema=settings.SNOWFLAKE_SCHEMA,
        role=settings.SNOWFLAKE_ROLE,
    )


class BaseRepository:
    """Base repository with Snowflake connection management."""

    @contextmanager
    def get_connection(self) -> Generator[snowflake.connector.SnowflakeConnection, None, None]:
        """Context manager for Snowflake connections."""
        conn = None
        try:
            conn = get_snowflake_connection()
            yield conn
        except InterfaceError as e:
            raise DatabaseConnectionException(f"Failed to connect to Snowflake: {e}")
        finally:
            if conn:
                conn.close()

    @contextmanager
    def get_cursor(self, dict_cursor: bool = True) -> Generator[Any, None, None]:
        """Context manager for Snowflake cursors with automatic connection cleanup."""
        with self.get_connection() as conn:
            cursor = conn.cursor(DictCursor) if dict_cursor else conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def execute_query(
        self,
        sql: str,
        params: Optional[tuple] = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
        commit: bool = False,
    ) -> Optional[Any]:
        """
        Execute a SQL query with error handling.

        Args:
            sql: SQL query string
            params: Query parameters
            fetch_one: Return single row
            fetch_all: Return all rows
            commit: Commit transaction after execution

        Returns:
            Query results or None
        """
        with self.get_cursor() as cursor:
            try:
                cursor.execute(sql, params or ())

                if commit:
                    cursor.connection.commit()

                if fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()

                return cursor.rowcount

            except ProgrammingError as e:
                error_msg = str(e).upper()
                if "UNIQUE" in error_msg or "DUPLICATE" in error_msg:
                    raise DuplicateEntityException(str(e))
                elif "FOREIGN KEY" in error_msg:
                    raise ForeignKeyViolationException(str(e))
                raise RepositoryException(f"Query error: {e}")
            except DatabaseError as e:
                raise RepositoryException(f"Database error: {e}")

    def uuid_to_str(self, uuid_val: Optional[UUID]) -> Optional[str]:
        """Convert UUID to string for Snowflake storage."""
        return str(uuid_val) if uuid_val else None

    def str_to_uuid(self, uuid_str: Optional[str]) -> Optional[UUID]:
        """Convert string from Snowflake to UUID."""
        return UUID(uuid_str) if uuid_str else None

    def normalize_timestamp(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Ensure timestamp is UTC-aware."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def row_to_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Snowflake row (uppercase keys) to lowercase dict."""
        if row is None:
            return {}
        return {k.lower(): v for k, v in row.items()}

    def build_update_query(
        self,
        table_name: str,
        update_data: Dict[str, Any],
        where_column: str,
        where_value: Any,
        additional_set: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, List[Any]]:
        """
        Build a dynamic UPDATE query.

        Args:
            table_name: Name of the table
            update_data: Dictionary of column -> value to update
            where_column: Column name for WHERE clause
            where_value: Value for WHERE clause
            additional_set: Additional SET clauses (e.g., UPDATED_AT)

        Returns:
            Tuple of (sql_string, params_list)
        """
        set_clauses = []
        params = []

        for column, value in update_data.items():
            set_clauses.append(f"{column.upper()} = %s")
            params.append(value)

        if additional_set:
            for column, value in additional_set.items():
                set_clauses.append(f"{column.upper()} = %s")
                params.append(value)

        params.append(where_value)

        sql = f"""
            UPDATE {table_name}
            SET {', '.join(set_clauses)}
            WHERE {where_column} = %s
        """

        return sql, params
