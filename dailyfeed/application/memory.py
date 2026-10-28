from __future__ import annotations

from typing import Any

from ..domain.ports import MemoryStore


class ThreeTierMemory:
    """三层记忆：全局向量记忆、RingBuffer 短期记忆、任务专属状态。"""

    def __init__(self, store: MemoryStore, stm_size: int = 8) -> None:
        self._store = store
        self._stm_size = stm_size

    def remember(self, task_id: str, text: str, vector: list[float], meta: dict[str, Any] | None = None) -> None:
        self._store.add_global(task_id, text, vector, meta or {})

    def similar(
        self, vector: list[float], k: int = 1, exclude_task_id: str | None = None
    ) -> list[tuple[float, dict[str, Any]]]:
        return self._store.search_global(vector, k, exclude_task_id)

    def note(self, task_id: str, text: str) -> None:
        self._store.push_short(task_id, text, self._stm_size)

    def recent(self, task_id: str, n: int = 5) -> list[str]:
        return self._store.recent_short(task_id, n)

    def task_get(self, task_id: str, key: str, default: Any = None) -> Any:
        return self._store.get_task_state(task_id, key, default)

    def task_set(self, task_id: str, key: str, value: Any) -> None:
        self._store.set_task_state(task_id, key, value)
