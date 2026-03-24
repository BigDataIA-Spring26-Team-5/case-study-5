"""Mem0 semantic memory for PE Org-AI-R agents.

Provides cross-session recall so agents remember prior assessments.
Requires:  mem0ai
"""
from __future__ import annotations

import logging
import os
import inspect
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class AgentMemory:
    """Wraps Mem0 for agent cross-session recall.

    Falls back gracefully if mem0ai is not installed.
    """

    def __init__(self) -> None:
        # Lazy-init Mem0 so importing the FastAPI app never blocks on network/config.
        self.memory = None
        self._available = False
        self._memory_cls = None
        self._memory_kind: str = "unknown"  # "memory" | "client"
        try:
            try:
                from mem0ai import Memory  # type: ignore[import]
                self._memory_kind = "memory"
            except ImportError:
                # Prefer the documented hosted API client if available.
                try:
                    from mem0 import MemoryClient as Memory  # type: ignore[import]
                    self._memory_kind = "client"
                except Exception:
                    from mem0 import Memory  # type: ignore[import]
                    self._memory_kind = "memory"
            self._memory_cls = Memory
            self._available = True
        except ImportError:
            logger.warning("mem0ai not installed — AgentMemory will no-op. poetry add mem0ai")
        except Exception as e:
            logger.warning("Mem0 import failed — AgentMemory will no-op: %s", e)

        # Resolve project-root `.env` without importing app.config (which may
        # validate required settings and fail in partial environments).
        # app/agents/memory.py → app/agents/ → app/ → project_root/
        self._env_file = (
            Path(__file__).resolve().parents[2] / ".env"
        )
        self._provider: str = "uninitialized"
        self._last_error: str = ""
        self._last_init_error: str = ""
        self._last_init_attempt_ts: float = 0.0

    def _maybe_load_dotenv_keys(self) -> None:
        """Load only the keys Mem0 needs from `.env` into `os.environ`.

        This keeps API keys reusable without requiring users to export them
        in every shell session.
        """
        try:
            if not self._env_file.exists():
                return
            needed = {
                "ANTHROPIC_API_KEY",
                "GROQ_API_KEY",
                "GROQ_API_URL",
                # Some mem0 deployments require their own API key / base URL.
                "MEM0_API_KEY",
                "MEM0_BASE_URL",
            }
            if all(os.getenv(k) for k in needed if k != "GROQ_API_URL"):
                return

            for raw_line in self._env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in needed:
                    continue
                value = value.strip().strip("'").strip('"')
                if value and not os.getenv(key):
                    os.environ[key] = value
        except Exception as exc:
            logger.debug("dotenv_load_skipped", error=str(exc))

    @staticmethod
    def _groq_base_url() -> str:
        url = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
        return url.rsplit("/chat/completions", 1)[0]

    def _build_config(self, provider: str) -> Dict[str, Any]:
        mem0_api_key = os.getenv("MEM0_API_KEY", "")
        mem0_base_url = os.getenv("MEM0_BASE_URL", "")

        mem0_cfg: Dict[str, Any] = {}
        if mem0_api_key:
            mem0_cfg["api_key"] = mem0_api_key
        if mem0_base_url:
            mem0_cfg["base_url"] = mem0_base_url

        if provider == "anthropic":
            return {
                **mem0_cfg,
                "llm": {
                    "provider": "anthropic",
                    "config": {
                        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                    },
                }
            }
        if provider == "groq":
            return {
                **mem0_cfg,
                "llm": {
                    "provider": "groq",
                    "config": {
                        "api_key": os.getenv("GROQ_API_KEY", ""),
                        "base_url": self._groq_base_url(),
                    },
                }
            }
        return mem0_cfg

    def _try_init(self, cfg: Dict[str, Any]) -> Any:
        """Best-effort init across mem0ai versions without hard dependency on a specific signature."""
        if not self._memory_cls:
            raise RuntimeError("Mem0 Memory class not available")

        Memory = self._memory_cls

        # Newer versions sometimes expose a helper constructor.
        if hasattr(Memory, "from_config") and callable(getattr(Memory, "from_config")):
            return Memory.from_config(cfg)  # type: ignore[attr-defined]

        # If we're using the hosted client, initialise with api_key (and optional base_url).
        if self._memory_kind == "client":
            mem0_api_key = os.getenv("MEM0_API_KEY", "")
            if not mem0_api_key:
                raise RuntimeError("MEM0_API_KEY is required for Mem0 hosted client")
            mem0_base_url = os.getenv("MEM0_BASE_URL", "")
            try:
                sig = inspect.signature(Memory.__init__)
                params = set(sig.parameters.keys())
            except Exception:
                params = set()

            kwargs: Dict[str, Any] = {}
            if "api_key" in params:
                kwargs["api_key"] = mem0_api_key
            if mem0_base_url and "base_url" in params:
                kwargs["base_url"] = mem0_base_url
            # Some versions might accept the api key positionally.
            if kwargs:
                return Memory(**kwargs)
            return Memory(mem0_api_key)

        # Prefer explicit config kwarg when available.
        try:
            sig = inspect.signature(Memory.__init__)
            params = set(sig.parameters.keys())
        except Exception:
            params = set()

        init_kwargs: Dict[str, Any] = {}
        mem0_api_key = os.getenv("MEM0_API_KEY", "")
        mem0_base_url = os.getenv("MEM0_BASE_URL", "")
        if mem0_api_key and "api_key" in params:
            init_kwargs["api_key"] = mem0_api_key
        if mem0_base_url and "base_url" in params:
            init_kwargs["base_url"] = mem0_base_url

        if "config" in params:
            return Memory(config=cfg, **init_kwargs)
        if "settings" in params:
            return Memory(settings=cfg, **init_kwargs)
        if "llm" in params and isinstance(cfg.get("llm"), dict):
            return Memory(llm=cfg["llm"], **init_kwargs)

        # Fallbacks for older/untyped constructors.
        try:
            return Memory(cfg, **init_kwargs)
        except TypeError:
            if init_kwargs:
                try:
                    return Memory(**init_kwargs)
                except Exception:
                    pass
            return Memory()

    def _ensure(self) -> bool:
        """Ensure Mem0 is initialised. Returns True when usable."""
        if not self._available:
            return False
        if self.memory is not None:
            return True

        # Avoid spamming init attempts on every request when configuration is missing.
        now = time.time()
        if self._last_init_error and (now - self._last_init_attempt_ts) < 15.0:
            return False

        # Prefer Anthropic (if configured), fall back to Groq, then let mem0 default.
        self._maybe_load_dotenv_keys()
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        groq_key = os.getenv("GROQ_API_KEY", "")
        attempts: List[tuple[str, Dict[str, Any]]] = []
        if anthropic_key:
            attempts.append(("anthropic", self._build_config("anthropic")))
        if groq_key:
            attempts.append(("groq", self._build_config("groq")))
        attempts.append(("default", {}))

        last_exc: Exception | None = None
        for provider, cfg in attempts:
            try:
                self.memory = self._try_init(cfg)
                if self.memory is not None:
                    logger.info("Mem0 semantic memory initialized (provider=%s)", provider)
                    self._provider = provider
                    self._last_init_error = ""
                    self._last_init_attempt_ts = now
                    return True
            except Exception as exc:
                last_exc = exc
                continue

        self.memory = None
        self._provider = "unavailable"
        self._last_init_error = str(last_exc or "unknown error")
        self._last_init_attempt_ts = now
        logger.warning(
            "Mem0 init failed — AgentMemory will no-op: %s",
            last_exc or "unknown error",
        )
        return False

    def debug_status(self) -> Dict[str, Any]:
        env_has_line = False
        try:
            if self._env_file.exists():
                env_has_line = any(
                    l.strip().startswith("MEM0_API_KEY=")
                    for l in self._env_file.read_text(encoding="utf-8", errors="replace").splitlines()
                )
        except Exception:
            env_has_line = False
        return {
            "available": bool(self._available),
            "initialized": self.memory is not None,
            "provider": self._provider,
            "env_file": str(self._env_file),
            "env_file_exists": bool(self._env_file.exists()),
            "env_file_has_mem0_api_key_line": env_has_line,
            "env_has_mem0_api_key": bool(os.getenv("MEM0_API_KEY")),
            "last_error": self._last_error,
            "last_init_error": self._last_init_error,
        }

    def remember_assessment(self, company_id: str, result: Dict[str, Any]) -> None:
        """Store key findings from a due-diligence run."""
        if not self._ensure():
            return
        narrative = (result.get("narrative") or "").strip()
        if len(narrative) > 500:
            narrative = narrative[:500] + "…"
        gaps = result.get("top_gaps") or result.get("gaps") or []
        if isinstance(gaps, list):
            gaps_txt = ", ".join(str(g) for g in gaps[:3])
        else:
            gaps_txt = ""

        summary = (
            f"{company_id} assessed: "
            f"Org-AI-R={float(result.get('org_air', 0) or 0.0):.1f}, "
            f"VR={float(result.get('vr_score', 0) or 0.0):.1f}, "
            f"HR={float(result.get('hr_score', 0) or 0.0):.1f}. "
            f"HITL={'triggered' if result.get('requires_approval') else 'not triggered'}. "
            f"{('Top gaps: ' + gaps_txt + '. ') if gaps_txt else ''}"
            f"{('Narrative: ' + narrative) if narrative else ''}"
        ).strip()

        # mem0ai APIs vary by version; try common calling conventions.
        add_attempts = [
            # Hosted client (docs): add(messages, user_id=...)
            (([{"role": "user", "content": summary}],), {"user_id": company_id}),
            (([{"role": "user", "content": summary}],), {"filters": {"user_id": company_id}}),
            ((), {"user_id": company_id, "text": summary}),
            ((summary,), {"user_id": company_id}),
            ((summary,), {"user_id": company_id, "metadata": {"source": "dd"}}),
            ((summary,), {"user": company_id}),
            ((summary,), {"user": company_id, "metadata": {"source": "dd"}}),
            ((), {"user_id": company_id, "messages": [{"role": "user", "content": summary}]}),
            (([summary],), {"user_id": company_id}),
            (({"text": summary, "user_id": company_id},), {}),
        ]
        last_exc: Exception | None = None
        for args, kwargs in add_attempts:
            try:
                self.memory.add(*args, **kwargs)
                logger.debug("Memory stored for %s", company_id)
                self._last_error = ""
                return
            except Exception as exc:
                last_exc = exc
                continue
        self._last_error = f"add_failed: {last_exc}"
        logger.warning("Memory storage failed for %s: %s", company_id, last_exc)

    def recall(self, company_id: str, query: str) -> List[Dict[str, Any]]:
        """Search memory for prior context about a company.

        Returns list of memory items (dicts with 'memory' key).
        """
        if not self._ensure():
            return []
        try:
            search_attempts = [
                # Hosted client (docs): search(query, filters={"user_id": ...})
                ((query,), {"filters": {"user_id": company_id}}),
                ((query,), {"user_id": company_id}),
                ((), {"query": query, "user_id": company_id}),
                ((query,), {"user": company_id}),
                ((), {"query": query, "user": company_id}),
                ((query,), {}),
                ((), {"query": query}),
            ]
            raw = None
            last_exc: Exception | None = None
            for args, kwargs in search_attempts:
                try:
                    raw = self.memory.search(*args, **kwargs)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    continue
            if last_exc is not None:
                self._last_error = f"search_failed: {last_exc}"
                raise last_exc
            if raw is None:
                return []
            if isinstance(raw, list):
                self._last_error = ""
                return raw
            if isinstance(raw, dict):
                for key in ("results", "memories", "items", "data"):
                    val = raw.get(key)
                    if isinstance(val, list):
                        self._last_error = ""
                        return val
                return []
            # Some versions return a string or custom object; best-effort.
            self._last_error = ""
            return [{"memory": str(raw)}]
        except Exception as exc:
            logger.warning("Memory recall failed for %s: %s", company_id, exc)
            return []

    def recall_as_text(self, company_id: str, query: str) -> str:
        """Return prior memories as a formatted string for LLM context."""
        items = self.recall(company_id, query)
        if not items:
            return ""
        lines: List[str] = []
        for item in items[:5]:
            if isinstance(item, str):
                lines.append(f"- {item}")
            elif isinstance(item, dict):
                lines.append(f"- {item.get('memory') or item.get('text') or str(item)}")
            else:
                lines.append(f"- {str(item)}")
        return "Prior assessment context:\n" + "\n".join(lines)


# Module-level singleton
agent_memory = AgentMemory()
