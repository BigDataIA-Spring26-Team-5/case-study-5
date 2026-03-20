"""
Base API client with retry, exponential backoff, and structured errors.
app/clients/base.py
"""
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.errors import ExternalServiceError, NotFoundError

logger = logging.getLogger(__name__)


class BaseAPIClient:
    """
    Async HTTP client with exponential backoff retry and structured errors.

    - 404 → NotFoundError (no retry)
    - Other 4xx → ExternalServiceError (no retry)
    - 5xx / connection errors → retry with exponential backoff up to max_retries
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        last_exc: Exception = ExternalServiceError(self.service_name, "No attempts made.")

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(
                    method, path, params=params, json=json_body, **kwargs
                )

                if response.status_code == 404:
                    raise NotFoundError(resource=self.service_name, identifier=path)

                if 400 <= response.status_code < 500:
                    body = response.json() if response.content else {}
                    raise ExternalServiceError(
                        service=self.service_name,
                        message=body.get("message", f"HTTP {response.status_code}"),
                    )

                if response.status_code >= 500:
                    last_exc = ExternalServiceError(
                        service=self.service_name,
                        message=f"HTTP {response.status_code}",
                    )
                    logger.warning(
                        "api_client_retry service=%s path=%s attempt=%d status=%d",
                        self.service_name, path, attempt + 1, response.status_code,
                    )
                    await self._backoff(attempt)
                    continue

                return response.json()

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = ExternalServiceError(
                    service=self.service_name,
                    message=f"Connection error: {exc}",
                )
                logger.warning(
                    "api_client_retry service=%s path=%s attempt=%d error=%s",
                    self.service_name, path, attempt + 1, exc,
                )
                await self._backoff(attempt)

        raise last_exc

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(self.retry_backoff_base * (2 ** attempt))

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        return await self._request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json_body: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        return await self._request("POST", path, json_body=json_body, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BaseAPIClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
