import threading
from typing import Any


class Blackboard:
    """
    Shared key-value store for inter-agent communication.

    Agents write computed results here so downstream agents can consume
    the data without repeating expensive fetches or computations.
    Thread-safe; all reads and writes are atomic under an RLock.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict = {}

    def write(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value

    def read(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.get(key, default)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._store)


blackboard = Blackboard()
