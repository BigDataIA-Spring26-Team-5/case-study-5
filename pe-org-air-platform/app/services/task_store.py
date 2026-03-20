"""
Task Store — PE Org-AI-R Platform
app/services/task_store.py

Stores background task state. Uses Redis when available, falls back to
in-memory dict when Redis is unavailable (tasks won't survive restarts).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TASK_TTL = 86400  # 24 hours


class TaskStore:
    """Task store with Redis backend and in-memory fallback."""

    def __init__(self, redis_client=None):
        self.client = redis_client
        self._memory: Dict[str, dict] = {}
        if redis_client is None:
            logger.warning("TaskStore: Redis unavailable, using in-memory fallback")

    def _key(self, task_id: str) -> str:
        return f"task:{task_id}"

    def create_task(self, task_id: str, metadata: Optional[Dict[str, Any]] = None) -> dict:
        """Create a new task entry with status='queued'."""
        task = {
            "task_id": task_id,
            "status": "queued",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "progress": metadata.get("progress") if metadata else None,
            "result": None,
            "error": None,
        }
        if metadata:
            task.update({k: v for k, v in metadata.items() if k not in task})
        if self.client:
            try:
                self.client.setex(self._key(task_id), TASK_TTL, json.dumps(task))
            except Exception:
                self._memory[task_id] = task
        else:
            self._memory[task_id] = task
        return task

    def update_status(self, task_id: str, **updates) -> Optional[dict]:
        """Update an existing task. Returns updated dict or None if not found."""
        task = self.get_task(task_id)
        if task is None:
            return None
        task.update(updates)
        if self.client:
            try:
                self.client.setex(self._key(task_id), TASK_TTL, json.dumps(task))
            except Exception:
                self._memory[task_id] = task
        else:
            self._memory[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[dict]:
        """Retrieve task state. Returns None if expired or missing."""
        if self.client:
            try:
                data = self.client.get(self._key(task_id))
                if data is not None:
                    return json.loads(data)
            except Exception:
                pass
        return self._memory.get(task_id)
