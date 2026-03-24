"""
Cache Service Singleton - PE Org-AI-R Platform
app/services/cache.py

Provides a singleton Redis cache instance with TTL constants.
Gracefully handles Redis unavailability.
"""
import time
try:
    import redis  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    redis = None
from typing import Any, Callable, Optional, Tuple, Type

from pydantic import BaseModel, Field
from datetime import datetime, timezone

from app.services.redis_cache import RedisCache
from app.core.settings import settings

# TTL constants from requirements (in seconds)
TTL_COMPANY = 300              # 5 minutes
TTL_ASSESSMENT = 120           # 2 minutes
TTL_INDUSTRY = 3600            # 1 hour
TTL_DIMENSION_WEIGHTS = 86400  # 24 hours

# Singleton instance
_cache: Optional[RedisCache] = None


def get_cache() -> Optional[RedisCache]:
    """
    Get or create Redis cache instance.

    Returns:
        RedisCache instance if Redis is available, None otherwise.

    Note:
        Returns None if Redis is unavailable, allowing the application
        to continue functioning without caching (graceful degradation).
    """
    global _cache
    if _cache is None:
        if redis is None:
            return None
        try:
            _cache = RedisCache()
            _cache.client.ping()  # Test connection
        except (redis.RedisError, ConnectionError, OSError):
            _cache = None
    return _cache


def reset_cache() -> None:
    """
    Reset the cache singleton.

    Useful for testing or when Redis connection needs to be re-established.
    """
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# CacheInfo model (shared by companies and industries routers)
# ---------------------------------------------------------------------------

class CacheInfo(BaseModel):
    """Cache metadata for debugging — shows if Redis is working."""
    hit: bool
    source: str
    key: str
    latency_ms: float
    ttl_seconds: int
    message: str


def create_cache_info(hit: bool, key: str, latency_ms: float, ttl: int) -> CacheInfo:
    """Build a CacheInfo object with a human-readable status message."""
    if hit:
        return CacheInfo(
            hit=True,
            source="redis",
            key=key,
            latency_ms=round(latency_ms, 3),
            ttl_seconds=ttl,
            message=f"✅ Cache HIT - Data served from Redis in {latency_ms:.3f}ms",
        )
    return CacheInfo(
        hit=False,
        source="database",
        key=key,
        latency_ms=round(latency_ms, 3),
        ttl_seconds=ttl,
        message=f"❌ Cache MISS - Data fetched from database in {latency_ms:.3f}ms, now cached for {ttl}s",
    )


# ---------------------------------------------------------------------------
# cached_query helper (used by companies router for 4 identical cache blocks)
# ---------------------------------------------------------------------------

def cached_query(
    key: str,
    ttl: int,
    model_type: Type,
    fallback_fn: Callable[[], Any],
) -> Tuple[Any, bool, float]:
    """
    Try Redis cache; on miss call fallback_fn(), cache result, return it.

    Returns:
        (result, cache_hit, latency_ms)
        - result: the cached or freshly fetched object
        - cache_hit: True if served from Redis
        - latency_ms: wall-clock time from start of this call
    """
    cache = get_cache()
    start = time.time()

    if cache:
        try:
            hit = cache.get(key, model_type)
            if hit is not None:
                latency = (time.time() - start) * 1000
                return hit, True, latency
        except Exception:
            pass

    result = fallback_fn()
    latency = (time.time() - start) * 1000

    if cache and result is not None:
        try:
            cache.set(key, result, ttl)
        except Exception:
            pass

    return result, False, latency
