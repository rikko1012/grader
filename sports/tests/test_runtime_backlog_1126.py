import sqlite3
import time

import pytest

from sports import expansion_collect as phases, net
from sports.model.shadow import _event_ids


def test_event_enumeration_seeks_over_duplicate_revisions():
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE shadow_forecasts(event_id TEXT)')
    con.execute('CREATE INDEX shadow_event ON shadow_forecasts(event_id)')
    con.executemany('INSERT INTO shadow_forecasts VALUES (?)',
                    [(event,) for event in ('nba:a', 'nba:b', 'nfl:c') for _ in range(10000)])
    # A full DISTINCT scan takes >90,000 VM steps; indexed seeks must remain
    # bounded by events, not the number of historical forecast revisions.
    steps = [0]
    def budget():
        steps[0] += 100
        return steps[0] > 3000
    con.set_progress_handler(budget, 100)
    assert _event_ids(con) == {'nba:a', 'nba:b', 'nfl:c'}
    con.close()


@pytest.mark.parametrize('status,retry_after,expected', [
    (None, None, 2), (502, None, 2), (503, None, 2),
    (429, None, 1), (401, None, 1), (403, None, 1), (503, 9999999999, 1)])
def test_retry_charges_original_budget_and_respects_provider_hold(monkeypatch, tmp_path, status, retry_after, expected):
    calls = []
    def fetch(*a, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise net.HttpError(status, 'https://example.test', not_before=retry_after)
        return {}
    monkeypatch.setattr('sports.expansion_sources.get_json', fetch)
    monkeypatch.setattr(phases, 'PHASES', {'tennis': (('exchanges', 2, 15),)})
    monkeypatch.setattr(phases, '_phase', lambda name, sport, cats, persist, directory, get, text:
                        get('https://example.test', {}))
    out = phases.run('tennis', {}, lambda *a: None, tmp_path, time.monotonic() + 15)['exchanges']
    assert len(calls) == out['requests'] == expected
    assert out['ok'] == (1 if expected == 2 else 0)
    assert all(c['tries'] == 1 for c in calls)


def test_transient_failure_does_not_exceed_one_request_cap(monkeypatch, tmp_path):
    calls = []
    def fetch(*a, **kw):
        calls.append(1)
        raise net.HttpError(502, 'https://example.test')
    monkeypatch.setattr('sports.expansion_sources.get_json', fetch)
    monkeypatch.setattr(phases, 'PHASES', {'tennis': (('exchanges', 1, 15),)})
    monkeypatch.setattr(phases, '_phase', lambda name, sport, cats, persist, directory, get, text:
                        get('https://example.test', {}))
    out = phases.run('tennis', {}, lambda *a: None, tmp_path, time.monotonic() + 15)['exchanges']
    assert calls == [1] and out['requests'] == 1 and out['state'] == 'failed'
