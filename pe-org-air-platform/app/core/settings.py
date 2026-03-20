"""
Canonical settings import path.
CS5 and all new code should import from here, not from app.config directly.
"""
from app.config import Settings, get_settings, settings  # noqa: F401

__all__ = ["Settings", "get_settings", "settings"]
