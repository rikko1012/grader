import threading
import time

from health_snapshot import HealthSnapshot


def test_blocked_diagnostics_do_not_block_probes_or_spawn_more_workers():
    snapshot = HealthSnapshot(ttl=0)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def collect():
        calls.append(1)
        entered.set()
        release.wait(5)
        finished.set()
        return {'memory_mb': 123}

    try:
        assert snapshot.read(collect)['diagnostics']['pending']
        assert entered.wait(1)
        start = time.monotonic()
        for _ in range(100):
            assert snapshot.read(collect)['diagnostics']['pending']
        assert time.monotonic() - start < 1
        assert calls == [1]
    finally:
        release.set()
        assert finished.wait(1)


def test_refresh_failure_keeps_last_diagnostics_and_reports_staleness():
    snapshot = HealthSnapshot()
    snapshot._refresh(lambda: {'memory_mb': 123})
    def fail():
        raise RuntimeError('private database details')
    snapshot._refresh(fail)
    snapshot.attempted = time.monotonic()
    out = snapshot.read(fail)
    assert out['memory_mb'] == 123
    assert out['diagnostics']['error'] == 'RuntimeError'
    assert out['diagnostics']['age_s'] is not None
    assert 'private' not in str(out)


def test_health_route_returns_while_diagnostics_are_blocked(monkeypatch):
    import server
    from tests.test_audit13_site import get
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        release.wait(5)
        return {}
    monkeypatch.setattr(server, '_HEALTH_SNAPSHOT', HealthSnapshot())
    monkeypatch.setattr(server, '_health_diagnostics', blocked)
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    try:
        response = get('/health')
        assert entered.wait(1)
        assert response.code == 200
        assert response.body()['ok'] is True
        assert response.body()['commit'] == 'a' * 40
        assert response.body()['diagnostics']['pending'] is True
    finally:
        release.set()
