from app.auth.ratelimit import FailedAuthLimiter, RequestLimiter


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def test_lockout_after_max_failures_then_expires():
    c = Clock()
    lim = FailedAuthLimiter(max_failures=3, window_sec=60, lockout_sec=600, clock=c)
    assert lim.record_failure('1.1.1.1') == 0 and lim.record_failure('1.1.1.1') == 0
    assert lim.retry_after('1.1.1.1') == 0
    assert lim.record_failure('1.1.1.1') == 600          # third strike locks
    assert 595 <= lim.retry_after('1.1.1.1') <= 601
    assert lim.retry_after('2.2.2.2') == 0                 # other clients unaffected
    c.t += 601
    assert lim.retry_after('1.1.1.1') == 0                 # lockout ended, counters cleared
    assert lim.record_failure('1.1.1.1') == 0


def test_failures_outside_the_window_do_not_add_up():
    c = Clock()
    lim = FailedAuthLimiter(max_failures=3, window_sec=60, lockout_sec=600, clock=c)
    lim.record_failure('a'); lim.record_failure('a')
    c.t += 61
    assert lim.record_failure('a') == 0 and lim.retry_after('a') == 0


def test_memory_is_bounded():
    c = Clock()
    lim = FailedAuthLimiter(max_failures=5, clock=c)
    for i in range(25_000):
        lim.record_failure(f'ip{i}')
    assert len(lim._failures) <= 10_000


def test_request_limit_per_identity_sliding_window():
    c = Clock()
    lim = RequestLimiter(limit_per_min=3, clock=c)
    assert [lim.allow('alice') for _ in range(3)] == [0, 0, 0]
    wait = lim.allow('alice')
    assert 1 <= wait <= 61                                  # 4th in the same minute is refused
    assert lim.allow('bob') == 0                      # separate budget per person
    c.t += 61
    assert lim.allow('alice') == 0                           # window slid


def test_zero_disables_the_request_limit():
    lim = RequestLimiter(limit_per_min=0)
    assert all(lim.allow('x') == 0 for _ in range(10_000))
