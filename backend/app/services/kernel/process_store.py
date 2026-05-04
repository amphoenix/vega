import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_PERSIST_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'phoenixtrade-processes.json')


@dataclass
class ProcessEntry:
    execution_id: str
    agent_id:     str
    ticker:       str            = ''
    status:       str            = 'running'
    started_at:   str            = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at:     Optional[str]  = None
    duration_ms:  Optional[int]  = None
    result:       Optional[Any]  = None
    error:        str            = ''

    def to_dict(self) -> dict:
        return asdict(self)


class ProcessStore:
    """Thread-safe in-memory + disk-persisted agent execution tracker."""

    def __init__(self, persist_path: str = _PERSIST_PATH):
        self._lock    = threading.RLock()
        self._entries: Dict[str, ProcessEntry] = {}
        self._path    = persist_path
        self._load()

    def register(self, execution_id: str, agent_id: str, ticker: str = '') -> ProcessEntry:
        entry = ProcessEntry(execution_id=execution_id, agent_id=agent_id, ticker=ticker)
        with self._lock:
            self._entries[execution_id] = entry
        return entry

    def complete(self, execution_id: str, result: Any = None) -> None:
        with self._lock:
            e = self._entries.get(execution_id)
            if e:
                now = datetime.now(timezone.utc)
                e.status      = 'completed'
                e.ended_at    = now.isoformat()
                started       = datetime.fromisoformat(e.started_at)
                e.duration_ms = int((now - started).total_seconds() * 1000)
                e.result      = result
        self._save()

    def fail(self, execution_id: str, error: str) -> None:
        with self._lock:
            e = self._entries.get(execution_id)
            if e:
                now = datetime.now(timezone.utc)
                e.status      = 'failed'
                e.ended_at    = now.isoformat()
                started       = datetime.fromisoformat(e.started_at)
                e.duration_ms = int((now - started).total_seconds() * 1000)
                e.error       = error
        self._save()

    def get(self, execution_id: str) -> Optional[ProcessEntry]:
        with self._lock:
            return self._entries.get(execution_id)

    def all(self) -> Dict[str, dict]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._entries.items()}

    def recent(self, n: int = 50) -> list:
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda e: e.started_at, reverse=True)
        return [e.to_dict() for e in entries[:n]]

    def clear_old(self, keep: int = 500) -> None:
        with self._lock:
            done = sorted(
                [(k, v) for k, v in self._entries.items() if v.status != 'running'],
                key=lambda x: x[1].started_at,
            )
            for k, _ in done[:-keep]:
                del self._entries[k]

    def _save(self) -> None:
        try:
            with self._lock:
                snapshot = {k: v.to_dict() for k, v in self._entries.items()
                            if v.status in ('completed', 'failed')}
            with open(self._path, 'w') as f:
                json.dump(snapshot, f)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path) as f:
                data = json.load(f)
            with self._lock:
                for k, v in data.items():
                    self._entries[k] = ProcessEntry(**{
                        fld: v.get(fld) for fld in ProcessEntry.__dataclass_fields__
                    })
        except Exception:
            pass


process_store = ProcessStore()
