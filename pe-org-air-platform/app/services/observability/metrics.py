"""Prometheus metrics and instrumentation decorators for CS5.

Metrics are pre-initialized with all known label combinations so they
appear in /metrics immediately (not just after the first call).
"""
from __future__ import annotations

import functools
import time
import logging
from typing import Callable, Any

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Known label combinations for pre-initialization
_MCP_TOOLS = [
    "calculate_org_air_score", "get_company_evidence", "generate_justification",
    "project_ebitda_impact", "run_gap_analysis", "get_portfolio_summary",
]
_AGENTS = ["sec_analyst", "scorer", "evidence_agent", "value_creator", "supervisor"]
_CS_SERVICES = [("cs1", "/portfolio"), ("cs2", "/evidence"), ("cs3", "/scores"), ("cs4", "/justify")]

# ── MCP Tool Metrics ─────────────────────────────────────────────────────────

mcp_tool_calls_total = Counter(
    "mcp_tool_calls_total",
    "Total MCP tool invocations",
    ["tool_name", "status"],
)

mcp_tool_duration_seconds = Histogram(
    "mcp_tool_duration_seconds",
    "MCP tool call duration in seconds",
    ["tool_name"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── Agent Metrics ────────────────────────────────────────────────────────────

agent_invocations_total = Counter(
    "agent_invocations_total",
    "Total agent node invocations",
    ["agent_name", "status"],
)

agent_duration_seconds = Histogram(
    "agent_duration_seconds",
    "Agent node execution duration in seconds",
    ["agent_name"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ── HITL Metrics ─────────────────────────────────────────────────────────────

hitl_approvals_total = Counter(
    "hitl_approvals_total",
    "Total HITL approval decisions",
    ["reason", "decision"],
)

# ── CS Client Metrics ────────────────────────────────────────────────────────

cs_client_calls_total = Counter(
    "cs_client_calls_total",
    "Total CS client service calls",
    ["service", "endpoint", "status"],
)


# ── Redis-backed persistence for Prometheus counters ─────────────────────
# Counters survive server restarts by storing accumulated values in Redis.

def _get_redis():
    """Best-effort Redis connection for metric persistence."""
    try:
        import redis as _redis
        import os
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return _redis.from_url(url, decode_responses=True)
    except Exception:
        return None


def _load_counter(key: str) -> float:
    """Load a counter value from Redis."""
    try:
        r = _get_redis()
        if r:
            val = r.get(f"prom:{key}")
            return float(val) if val else 0.0
    except Exception:
        pass
    return 0.0


def _save_counter(key: str, value: float):
    """Save a counter value to Redis (no expiry — persists forever)."""
    try:
        r = _get_redis()
        if r:
            r.set(f"prom:{key}", str(value))
    except Exception:
        pass


def _init_metrics():
    """Load persisted counter values from Redis on startup."""
    for tool in _MCP_TOOLS:
        for status in ("success", "error"):
            val = _load_counter(f"mcp_tool:{tool}:{status}")
            if val > 0:
                mcp_tool_calls_total.labels(tool_name=tool, status=status).inc(val)
            else:
                mcp_tool_calls_total.labels(tool_name=tool, status=status)
        mcp_tool_duration_seconds.labels(tool_name=tool)
    for agent in _AGENTS:
        for status in ("success", "error"):
            val = _load_counter(f"agent:{agent}:{status}")
            if val > 0:
                agent_invocations_total.labels(agent_name=agent, status=status).inc(val)
            else:
                agent_invocations_total.labels(agent_name=agent, status=status)
        agent_duration_seconds.labels(agent_name=agent)
    for decision in ("approved", "rejected"):
        val = _load_counter(f"hitl:{decision}")
        if val > 0:
            hitl_approvals_total.labels(reason="score_change", decision=decision).inc(val)
        else:
            hitl_approvals_total.labels(reason="score_change", decision=decision)
    for svc, ep in _CS_SERVICES:
        for status in ("success", "error"):
            val = _load_counter(f"cs:{svc}:{ep}:{status}")
            if val > 0:
                cs_client_calls_total.labels(service=svc, endpoint=ep, status=status).inc(val)
            else:
                cs_client_calls_total.labels(service=svc, endpoint=ep, status=status)

_init_metrics()


# ── Decorators ───────────────────────────────────────────────────────────────

def _inc_mcp(tool_name: str, status: str):
    """Increment MCP counter and persist to Redis."""
    mcp_tool_calls_total.labels(tool_name=tool_name, status=status).inc()
    cur = mcp_tool_calls_total.labels(tool_name=tool_name, status=status)._value.get()
    _save_counter(f"mcp_tool:{tool_name}:{status}", cur)


def _inc_agent(agent_name: str, status: str):
    """Increment agent counter and persist to Redis."""
    agent_invocations_total.labels(agent_name=agent_name, status=status).inc()
    cur = agent_invocations_total.labels(agent_name=agent_name, status=status)._value.get()
    _save_counter(f"agent:{agent_name}:{status}", cur)


def _inc_cs(service: str, endpoint: str, status: str):
    """Increment CS client counter and persist to Redis."""
    cs_client_calls_total.labels(service=service, endpoint=endpoint, status=status).inc()
    cur = cs_client_calls_total.labels(service=service, endpoint=endpoint, status=status)._value.get()
    _save_counter(f"cs:{service}:{endpoint}:{status}", cur)


def _inc_hitl(decision: str):
    """Increment HITL counter and persist to Redis."""
    hitl_approvals_total.labels(reason="score_change", decision=decision).inc()
    cur = hitl_approvals_total.labels(reason="score_change", decision=decision)._value.get()
    _save_counter(f"hitl:{decision}", cur)


def track_mcp_tool(tool_name: str) -> Callable:
    """Decorator to instrument async MCP tool functions."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                _inc_mcp(tool_name, "success")
                return result
            except Exception as e:
                _inc_mcp(tool_name, "error")
                raise
            finally:
                duration = time.time() - start
                mcp_tool_duration_seconds.labels(tool_name=tool_name).observe(duration)
        return wrapper
    return decorator


def track_agent(agent_name: str) -> Callable:
    """Decorator to instrument agent node functions."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                _inc_agent(agent_name, "success")
                return result
            except Exception as e:
                _inc_agent(agent_name, "error")
                raise
            finally:
                duration = time.time() - start
                agent_duration_seconds.labels(agent_name=agent_name).observe(duration)
        return wrapper
    return decorator


def track_cs_client(service: str, endpoint: str) -> Callable:
    """Decorator to instrument CS client calls."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                _inc_cs(service, endpoint, "success")
                return result
            except Exception as e:
                _inc_cs(service, endpoint, "error")
                raise
        return wrapper
    return decorator
