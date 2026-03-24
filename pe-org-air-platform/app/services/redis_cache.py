try:
    import redis  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    redis = None
from typing import Optional, TypeVar, Type
from pydantic import BaseModel
from app.core.settings import settings

T = TypeVar("T", bound=BaseModel)


class RedisCache:
    def __init__(self):
        if redis is None:
            raise RuntimeError("redis package not installed")
        self.client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
        )


    def get(self, key: str, model: Type[T]) -> Optional[T]:
        """Get cached item and deserialize to Pydantic model."""
        data = self.client.get(key)
        if data:
            return model.model_validate_json(data)
        return None

    def set(self, key: str, value: BaseModel, ttl_seconds: int) -> None:
        """Cache Pydantic model with TTL."""
        self.client.setex(
            key,
            ttl_seconds,
            value.model_dump_json(),
        )

    def delete(self, key: str) -> None:
        """Invalidate single cache entry."""
        self.client.delete(key)

    def delete_pattern(self, pattern: str) -> None:
        """Invalidate all keys matching pattern."""
        for key in self.client.scan_iter(match=pattern):
            self.client.delete(key)
