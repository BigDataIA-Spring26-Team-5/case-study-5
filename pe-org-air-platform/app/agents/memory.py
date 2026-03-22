"""Mem0 semantic memory for PE Org-AI-R agents.

Provides cross-session recall so agents remember prior assessments.
Requires:  mem0ai>=0.1.0  (add to requirements.txt)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class AgentMemory:
    """Wraps Mem0 for agent cross-session recall.

    Falls back gracefully if mem0ai is not installed.
    """

    def __init__(self) -> None:
        try:
            from mem0 import Memory  # type: ignore[import]
            self.memory = Memory()
            self._available = True
            logger.info("Mem0 semantic memory initialized")
        except ImportError:
            self.memory = None
            self._available = False
            logger.warning("mem0ai not installed — AgentMemory will no-op. pip install mem0ai")
        except Exception as e:
            self.memory = None
            self._available = False
            logger.warning("Mem0 init failed (missing API key?) — AgentMemory will no-op: %s", e)

    def remember_assessment(self, company_id: str, result: Dict[str, Any]) -> None:
        """Store key findings from a due-diligence run."""
        if not self._available:
            return
        summary = (
            f"{company_id} assessed: "
            f"Org-AI-R={result.get('org_air', 0):.1f}, "
            f"VR={result.get('vr_score', 0):.1f}, "
            f"HR={result.get('hr_score', 0):.1f}. "
            f"HITL={'triggered' if result.get('requires_approval') else 'not triggered'}."
        )
        try:
            self.memory.add(summary, user_id=company_id)
            logger.debug("Memory stored for %s", company_id)
        except Exception as exc:
            logger.warning("Memory storage failed for %s: %s", company_id, exc)

    def recall(self, company_id: str, query: str) -> List[Dict[str, Any]]:
        """Search memory for prior context about a company.

        Returns list of memory items (dicts with 'memory' key).
        """
        if not self._available:
            return []
        try:
            return self.memory.search(query, user_id=company_id) or []
        except Exception as exc:
            logger.warning("Memory recall failed for %s: %s", company_id, exc)
            return []

    def recall_as_text(self, company_id: str, query: str) -> str:
        """Return prior memories as a formatted string for LLM context."""
        items = self.recall(company_id, query)
        if not items:
            return ""
        lines = [f"- {item.get('memory', str(item))}" for item in items[:5]]
        return "Prior assessment context:\n" + "\n".join(lines)


# Module-level singleton
agent_memory = AgentMemory()
