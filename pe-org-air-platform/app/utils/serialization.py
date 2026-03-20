# app/utils/serialization.py
"""JSON serialization helpers for Snowflake row data."""

from datetime import datetime
from decimal import Decimal
from typing import Dict


def serialize_row(row: Dict) -> Dict:
    """Convert Decimal/datetime types to JSON-safe types."""
    clean = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            clean[k] = float(v)
        elif isinstance(v, datetime):
            clean[k] = v.isoformat()
        else:
            clean[k] = v
    return clean
