"""
Tests for monitoring/latency_tracker.py — LatencyTracker.
"""

import time

from app.monitoring.latency_tracker import (
    LatencyStats,
    LatencyTrace,
    LatencyTracker,
)


class TestLatencyTrace:
    def test_basic_trace(self):
        trace = LatencyTrace(trace_id='t1')
        assert trace.started_ns > 0
        assert not trace.is_finished

    def test_checkpoint(self):
        trace = LatencyTrace(trace_id='t1')
        trace.checkpoint('supervisor')
        trace.checkpoint('risk')
        assert len(trace.checkpoints) == 2
        assert trace.checkpoints[0].stage == 'supervisor'

    def test_finish(self):
        trace = LatencyTrace(trace_id='t1')
        trace.finish()
        assert trace.is_finished
        assert trace.finished_ns > trace.started_ns

    def test_total_ms(self):
        trace = LatencyTrace(trace_id='t1')
        time.sleep(0.005)
        trace.finish()
        assert trace.total_ms >= 4  # at least 4ms (allowing clock jitter)

    def test_stage_latencies(self):
        trace = LatencyTrace(trace_id='t1')
        trace.checkpoint('a')
        trace.checkpoint('b')
        trace.finish()
        stages = trace.stage_latencies_ms()
        assert 'start\u2192a' in stages
        assert 'a\u2192b' in stages

    def test_to_dict(self):
        trace = LatencyTrace(trace_id='t1')
        trace.checkpoint('x')
        trace.finish()
        d = trace.to_dict()
        assert d['trace_id'] == 't1'
        assert 'total_ms' in d
        assert 'stages' in d

    def test_metadata(self):
        trace = LatencyTrace(trace_id='t1', metadata={'mode': 'scalp'})
        d = trace.to_dict()
        assert d['metadata']['mode'] == 'scalp'


class TestLatencyStats:
    def test_empty(self):
        s = LatencyStats(name='total')
        assert s.count == 0
        assert s.mean_ms == 0
        assert s.p50 == 0

    def test_single_value(self):
        s = LatencyStats(name='total')
        s.record(5.0)
        assert s.count == 1
        assert s.mean_ms == 5.0
        assert s.min_ms == 5.0
        assert s.max_ms == 5.0
        assert s.p50 == 5.0

    def test_multiple_values(self):
        s = LatencyStats(name='total')
        for v in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
            s.record(float(v))
        assert s.count == 10
        assert s.mean_ms == 5.5
        assert s.min_ms == 1.0
        assert s.max_ms == 10.0
        assert s.p50 == 5.0
        assert s.p95 == 10.0

    def test_to_dict(self):
        s = LatencyStats(name='stage_a')
        s.record(3.0)
        d = s.to_dict()
        assert d['name'] == 'stage_a'
        assert d['count'] == 1
        assert 'p50_ms' in d

    def test_rolling_window(self):
        s = LatencyStats(name='x')
        # maxlen=1000, add 1001 items
        for i in range(1001):
            s.record(float(i))
        assert len(s._values) == 1000


class TestLatencyTracker:
    def test_start_and_finish(self):
        tracker = LatencyTracker()
        trace = tracker.start_trace('s1')
        trace.checkpoint('supervisor')
        tracker.finish_trace(trace)
        assert tracker.trace_count == 1

    def test_stats(self):
        tracker = LatencyTracker()
        trace = tracker.start_trace('s1')
        trace.checkpoint('supervisor')
        trace.checkpoint('risk')
        tracker.finish_trace(trace)
        stats = tracker.stats()
        assert stats['total']['count'] == 1
        assert 'start\u2192supervisor' in stats['stages']
        assert 'supervisor\u2192risk' in stats['stages']

    def test_multiple_traces(self):
        tracker = LatencyTracker()
        for i in range(5):
            trace = tracker.start_trace(f's{i}')
            trace.checkpoint('a')
            tracker.finish_trace(trace)
        assert tracker.trace_count == 5
        assert tracker.stats()['total']['count'] == 5

    def test_recent_traces(self):
        tracker = LatencyTracker()
        for i in range(10):
            trace = tracker.start_trace(f's{i}')
            tracker.finish_trace(trace)
        recent = tracker.recent_traces(limit=3)
        assert len(recent) == 3
        assert recent[-1].trace_id == 's9'

    def test_stage_stats(self):
        tracker = LatencyTracker()
        trace = tracker.start_trace('s1')
        trace.checkpoint('supervisor')
        tracker.finish_trace(trace)
        s = tracker.stage_stats('start\u2192supervisor')
        assert s is not None
        assert s.count == 1

    def test_stage_stats_missing(self):
        tracker = LatencyTracker()
        assert tracker.stage_stats('nonexistent') is None

    def test_reset(self):
        tracker = LatencyTracker()
        trace = tracker.start_trace('s1')
        tracker.finish_trace(trace)
        tracker.reset()
        assert tracker.trace_count == 0
        assert tracker.stats()['total']['count'] == 0

    def test_max_traces_eviction(self):
        tracker = LatencyTracker(max_traces=5)
        for i in range(10):
            trace = tracker.start_trace(f's{i}')
            tracker.finish_trace(trace)
        assert tracker.trace_count == 5
        recent = tracker.recent_traces(limit=10)
        assert recent[0].trace_id == 's5'

    def test_metadata_passthrough(self):
        tracker = LatencyTracker()
        trace = tracker.start_trace('s1', mode='scalp', symbol='NIFTY')
        tracker.finish_trace(trace)
        recent = tracker.recent_traces(1)
        assert recent[0].metadata['mode'] == 'scalp'
