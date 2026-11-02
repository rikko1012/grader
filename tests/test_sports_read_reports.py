import json
import urllib.error

from scripts.sports_read_reports import collect, API, SCOUT, REPORTS


def test_read_reports_uses_fixed_origins_and_does_not_save_credentials(tmp_path):
    calls = []
    def fetch(url, headers):
        calls.append((url, headers))
        if url == SCOUT + '/api/sports/token':
            return {'token': 'st1.private-example'}
        return {'recorded_at': '2026-11-01', 'results': []}
    report = collect(tmp_path, 'private-admin-example', fetch)
    assert all(r['ok'] for r in report['reports'].values())
    assert len(calls) == len(REPORTS) + 1
    assert all(url.startswith(API + '/') for url, _ in calls[1:])
    assert all('X-Admin-Token' not in headers for _, headers in calls[1:])
    assert 'private-' not in ''.join(p.read_text() for p in tmp_path.iterdir())


def test_missing_snapshot_is_not_reported_as_success(tmp_path):
    def fetch(url, headers):
        if url.endswith('/token'):
            return {'token': 'st1.private-example'}
        raise urllib.error.HTTPError(url, 503, 'sensitive response', {}, None)
    report = collect(tmp_path, 'admin', fetch)
    assert all(not r['ok'] for r in report['reports'].values())
    assert 'sensitive response' not in json.dumps(report)
    assert all(r['http_status'] == 503 for r in report['reports'].values())
