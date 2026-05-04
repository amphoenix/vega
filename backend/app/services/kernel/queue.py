import itertools
import queue as _queue
import threading
from typing import Any, Optional

HIGH   = 0
NORMAL = 1

_counter = itertools.count()


class PriorityQueue:
    """
    Priority queue for agent dispatch.

    Items with HIGH priority (0) are dequeued before NORMAL (1).
    Within the same priority level items are served FIFO via a
    monotonically increasing sequence counter.
    """

    def __init__(self):
        self._q: _queue.PriorityQueue = _queue.PriorityQueue()

    def push(self, item: Any, priority: int = NORMAL) -> None:
        seq = next(_counter)
        self._q.put((priority, seq, item))

    def pop(self, timeout: Optional[float] = None) -> Any:
        priority, seq, item = self._q.get(timeout=timeout)
        return item

    def size(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()


priority_queue = PriorityQueue()
