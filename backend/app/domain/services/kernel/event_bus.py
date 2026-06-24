import queue
import threading
from typing import Optional, Set


class EventBus:
    """
    Thread-safe broadcast pub/sub bus.

    Publishers call publish(); subscribers receive every event via a
    per-subscriber Queue.  Slow consumers whose queues are full silently
    drop events and are evicted — they never block the publisher.
    """

    def __init__(self, maxsize: int = 256):
        self._lock    = threading.RLock()
        self._subs:   Set[queue.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self, maxsize: Optional[int] = None) -> queue.Queue:
        q = queue.Queue(maxsize=maxsize or self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> None:
        dead: Set[queue.Queue] = set()
        with self._lock:
            subs = set(self._subs)

        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.add(q)

        if dead:
            with self._lock:
                self._subs -= dead

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


def tick_event(ticker: str, price: float, source: str,
               high: Optional[float] = None, low: Optional[float] = None,
               volume: Optional[int] = None,
               change_pct: Optional[float] = None) -> dict:
    return {
        'type': 'tick', 'ticker': ticker, 'price': price, 'source': source,
        'high': high, 'low': low, 'volume': volume, 'change_pct': change_pct,
    }


def agent_submitted_event(execution_id: str, agent_id: str) -> dict:
    return {'type': 'agent_submitted', 'execution_id': execution_id, 'agent_id': agent_id}


def agent_completed_event(execution_id: str, agent_id: str, result: dict) -> dict:
    return {'type': 'agent_completed', 'execution_id': execution_id,
            'agent_id': agent_id, 'result': result}


def agent_failed_event(execution_id: str, agent_id: str, error: str) -> dict:
    return {'type': 'agent_failed', 'execution_id': execution_id,
            'agent_id': agent_id, 'error': error}


def phase_event(phase: str, msg: str, **meta) -> dict:
    return {'type': 'phase', 'phase': phase, 'msg': msg, **meta}


event_bus = EventBus()
