"""Session data kept in server memory only. The browser holds just an id; nothing personal touches disk.

Single user: only the latest dataset is kept. A page refresh starts a new session.
"""

from __future__ import annotations

import secrets
import threading


class SessionRegistry[T]:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: tuple[str, T] | None = None

    def put(self, value: T) -> str:
        dataset_id = secrets.token_hex(16)
        with self._lock:
            self._current = (dataset_id, value)
        return dataset_id

    def get(self, dataset_id: str | None) -> T | None:
        if not dataset_id:
            return None
        with self._lock:
            current = self._current
        if current is None or not secrets.compare_digest(current[0], dataset_id):
            return None
        return current[1]

    def clear(self) -> None:
        with self._lock:
            self._current = None
