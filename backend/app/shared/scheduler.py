"""
Lightweight scheduler — cron-like daily hooks + interval timers.

Runs in a single daemon thread. No APScheduler, no Celery.

Usage:
    sched = Scheduler()
    sched.daily("00:00", reset_all_daily_state)
    sched.daily("09:10", pre_warm_caches)
    sched.daily("15:10", force_exit_all_positions)
    sched.every(30, check_day_rollover)
    sched.start()
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from .logger import get_logger
from .time import IST, now_ist, datetime, dt_time, monotonic, sleep

logger = get_logger('scheduler')


@dataclass
class _DailyJob:
    run_at: dt_time          # IST time to fire
    func: Callable[[], None]
    name: str
    last_fired: str = ''     # ISO date of last fire, to avoid double-fire


@dataclass
class _IntervalJob:
    interval_sec: int
    func: Callable[[], None]
    name: str
    last_run: float = field(default_factory=monotonic)


class Scheduler:
    """Single-thread scheduler for trading hooks."""

    def __init__(self) -> None:
        self._daily_jobs: list[_DailyJob] = []
        self._interval_jobs: list[_IntervalJob] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def daily(self, time_str: str, func: Callable[[], None], name: str = '') -> None:
        """Register a function to run once per day at the given IST time.

        Args:
            time_str: "HH:MM" in IST (e.g. "00:00", "09:10", "15:10")
            func: zero-arg callable
            name: optional label for logging
        """
        h, m = time_str.split(':')
        run_at = dt_time(int(h), int(m))
        self._daily_jobs.append(_DailyJob(
            run_at=run_at,
            func=func,
            name=name or func.__name__,
        ))

    def every(self, seconds: int, func: Callable[[], None], name: str = '') -> None:
        """Register a function to run every N seconds."""
        self._interval_jobs.append(_IntervalJob(
            interval_sec=seconds,
            func=func,
            name=name or func.__name__,
        ))

    def start(self) -> None:
        """Start the scheduler loop in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='vega-scheduler')
        self._thread.start()
        logger.info(
            'Scheduler started: %d daily jobs, %d interval jobs',
            len(self._daily_jobs), len(self._interval_jobs),
        )

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = now_ist()
            today = now.date().isoformat()

            # Check daily jobs
            for job in self._daily_jobs:
                if job.last_fired == today:
                    continue
                if now.time() >= job.run_at:
                    try:
                        logger.info('Scheduler firing daily job: %s', job.name)
                        job.func()
                        job.last_fired = today
                    except Exception:
                        logger.exception('Daily job %s failed', job.name)

            # Check interval jobs
            mono = monotonic()
            for job in self._interval_jobs:
                if mono - job.last_run >= job.interval_sec:
                    try:
                        job.func()
                        job.last_run = mono
                    except Exception:
                        logger.exception('Interval job %s failed', job.name)

            # Sleep 1s between checks
            self._stop_event.wait(1.0)
