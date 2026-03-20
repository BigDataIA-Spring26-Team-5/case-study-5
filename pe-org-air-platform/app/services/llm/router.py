# app/services/llm/router.py
"""LLM Router — LiteLLM multi-provider router.

Budget strategy ($5 Anthropic credit):
  - Groq (FREE): keyword matching, dimension detection, subdomain suggestion,
    governance extraction, evidence extraction, hyde generation
  - Claude Haiku ($0.25/$1.25 per MTok): chatbot answers, justifications, IC summaries
  - DeepSeek: fallback only if both Groq and Claude fail

Estimated costs:
  - Chatbot question (Haiku): ~$0.002 per question
  - $5 budget ≈ 2,000+ chatbot answers
"""
from __future__ import annotations

import os
import time
import structlog
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Dict, Any, Optional

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from app.core.errors import ExternalServiceError

logger = structlog.get_logger()

try:
    import litellm
    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Task Routing — Groq for cheap tasks, Claude Haiku for quality tasks
# ---------------------------------------------------------------------------

_TASK_ROUTING: Dict[str, tuple[str, str]] = {
    # FREE tasks — Groq primary, DeepSeek fallback
    "evidence_extraction":          ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "keyword_matching":             ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "hyde_generation":              ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "subdomain_suggestion":         ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "governance_pattern_extraction":("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "governance_extraction":        ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),
    "tech_stack_fallback":          ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat"),

    # QUALITY tasks — Claude Haiku primary, Groq fallback
    "justification_generation":     ("claude-haiku-4-5-20251001", "groq/llama-3.3-70b-versatile"),
    "ic_summary":                   ("claude-haiku-4-5-20251001", "groq/llama-3.3-70b-versatile"),
    "chat_response":                ("claude-haiku-4-5-20251001", "groq/llama-3.3-70b-versatile"),
}


# ---------------------------------------------------------------------------
# Model Configs
# ---------------------------------------------------------------------------

_MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "groq/llama-3.1-8b-instant": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "api_key_env": "GROQ_API_KEY",
    },
    "groq/llama-3.3-70b-versatile": {
        "max_tokens": 2048,
        "temperature": 0.3,
        "api_key_env": "GROQ_API_KEY",
    },
    "deepseek/deepseek-chat": {
        "max_tokens": 2048,
        "temperature": 0.4,
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "claude-haiku-4-5-20251001": {
        "max_tokens": 1500,
        "temperature": 0.4,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
}

_MODEL_COST_PER_1K: Dict[str, float] = {
    "groq/llama-3.1-8b-instant":    0.00005,   # essentially free
    "groq/llama-3.3-70b-versatile": 0.00059,   # still very cheap
    "deepseek/deepseek-chat":       0.00014,
    "claude-haiku-4-5-20251001":    0.00125,    # $0.25 input + $1.25 output per MTok
}


# ---------------------------------------------------------------------------
# Daily Budget
# ---------------------------------------------------------------------------

@dataclass
class DailyBudget:
    limit_usd: float
    _spend: float = field(default=0.0, init=False)
    _reset_ts: float = field(default_factory=time.time, init=False)

    def _maybe_reset(self) -> None:
        now = time.time()
        if now - self._reset_ts > 86400:
            self._spend = 0.0
            self._reset_ts = now

    def record(self, tokens: int, model: str) -> None:
        self._maybe_reset()
        cost = (tokens / 1000) * _MODEL_COST_PER_1K.get(model, 0.0001)
        self._spend += cost

    def is_over_limit(self) -> bool:
        self._maybe_reset()
        return self._spend >= self.limit_usd

    @property
    def spend(self) -> float:
        self._maybe_reset()
        return self._spend


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------

class ModelRouter:
    def __init__(self, daily_limit_usd: float = 5.0):
        self.budget = DailyBudget(limit_usd=daily_limit_usd)
        if _LITELLM_AVAILABLE:
            litellm.set_verbose = False

    async def complete(
        self,
        task: str,
        messages: List[Dict[str, str]],
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Async completion. Tries primary model, then fallback."""
        if self.budget.is_over_limit():
            logger.warning("llm_budget_exceeded", task=task,
                           budget=self.budget.limit_usd, spent=round(self.budget.spend, 4))
            raise ExternalServiceError("llm_router", f"Daily budget of ${self.budget.limit_usd} exceeded (spent ${self.budget.spend:.4f}).")

        primary, fallback = _TASK_ROUTING.get(
            task, ("groq/llama-3.1-8b-instant", "deepseek/deepseek-chat")
        )

        start = time.perf_counter()
        logger.info("llm_call_started", task=task, model=primary)

        last_exc: Exception = RuntimeError("No models tried.")
        model_used = primary
        for model in (primary, fallback):
            try:
                if stream:
                    chunks = []
                    config = _MODEL_CONFIGS.get(model, {})
                    async for chunk in self._stream_complete(
                        model=model,
                        messages=messages,
                        config=config,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ):
                        chunks.append(chunk)
                    result = "".join(chunks)
                else:
                    result = await self._call_model(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                model_used = model
                duration = time.perf_counter() - start
                logger.info("llm_call_completed", task=task, model=model_used,
                            duration_seconds=round(duration, 3))
                return result
            except Exception as exc:
                logger.warning("llm_model_failed", model=model, error=str(exc),
                               fallback=fallback if model == primary else None)
                last_exc = exc
                continue

        raise ExternalServiceError("llm_router", f"Both models failed for task '{task}': {last_exc}")

    async def _call_model(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        config = _MODEL_CONFIGS.get(model, {})
        api_key_env = config.get("api_key_env", "")
        api_key = os.getenv(api_key_env) if api_key_env else None

        if not _LITELLM_AVAILABLE:
            return self._fallback_stub(model, messages)

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=max_tokens if max_tokens is not None else config.get("max_tokens", 1024),
            temperature=temperature if temperature is not None else config.get("temperature", 0.3),
            api_key=api_key,
            stream=False,
        )

        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", 500) if usage else 500
        self.budget.record(tokens, model)

        return response.choices[0].message.content or ""

    async def _stream_complete(
        self,
        model: str,
        messages: List[Dict[str, str]],
        config: Dict[str, Any],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        api_key_env = config.get("api_key_env", "")
        api_key = os.getenv(api_key_env) if api_key_env else None

        if not _LITELLM_AVAILABLE:
            yield self._fallback_stub(model, messages)
            return

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=max_tokens if max_tokens is not None else config.get("max_tokens", 1024),
            temperature=temperature if temperature is not None else config.get("temperature", 0.3),
            api_key=api_key,
            stream=True,
        )

        async for chunk in response:
            delta = getattr(chunk.choices[0].delta, "content", "") or ""
            if delta:
                yield delta

    def complete_sync(
        self,
        task: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Synchronous wrapper for services that can't use async."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.complete(task, messages, temperature=temperature, max_tokens=max_tokens)
                    )
                    return future.result()
            else:
                return asyncio.run(self.complete(task, messages, temperature=temperature, max_tokens=max_tokens))
        except RuntimeError:
            return asyncio.run(self.complete(task, messages, temperature=temperature, max_tokens=max_tokens))

    @staticmethod
    def _fallback_stub(model: str, messages: List[Dict[str, str]]) -> str:
        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        return f"[{model} stub] Response to: {user_msg[:100]}..."


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_router_instance = None

def get_llm_router():
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance