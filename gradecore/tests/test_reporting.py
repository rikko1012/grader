import sqlite3
import pytest
from gradecore.capture import capture
from gradecore.reporting import comparisons
from gradecore.release import readiness
from gradecore.tests.test_capture import source


def test_comparisons_are_bounded_readonly_and_sport_isolated(tmp_path, monkeypatch):
    path = tmp_path / 'capture.db'
    assert comparisons(path, 'nba')['state'] == 'awaiting_capture'
    assert not path.exists()
    monkeypatch.setenv('SCOUT_GRADECORE_SHADOW', '1')
    with sqlite3.connect(path) as db:
        for sport in ('cs2', 'lol', 'nba', 'nfl'):
            kind, args = source(sport)
            assert capture(db, kind, *args) == 'captured'
    before = path.read_bytes()
    result = comparisons(path, 'nba', limit=1)
    assert len(result['rows']) == 1
    assert result['release_allowed'] is False
    assert comparisons(path, 'nba', before=result['next_before'])['rows'] == []
    assert path.read_bytes() == before
    with pytest.raises(ValueError): comparisons(path, 'other')


def test_release_requires_all_evidence_and_actual_seven_days():
    historical = {'historical_gate_pass': True, 'baseline_stage': 'published', 'candidate': {'reliability': [{'gap': .01}]}}
    prospective = {'started_at': '2026-11-01T00:00:00Z', 'unique_forecasts': 500,
                   'delta_ci': [-.03, -.001], 'side_balance_pass': True}
    assert not readiness(historical, prospective, '2026-11-07T23:59:59Z')['eligible']
    assert readiness(historical, prospective, '2026-11-08T00:00:00Z')['eligible']
    for key, value in [('unique_forecasts', 499), ('delta_ci', [-.03, 0]), ('side_balance_pass', False)]:
        assert not readiness(historical, dict(prospective, **{key: value}), '2026-11-09T00:00:00Z')['eligible']
    historical['candidate']['reliability'][0]['gap'] = float('nan')
    assert not readiness(historical, prospective, '2026-11-09T00:00:00Z')['eligible']


def test_one_sided_success_cannot_release_both_sides():
    from gradecore.containment import verdict
    from gradecore.tests.test_phase1 import _rows
    rows = _rows(300, .64, .64, be=.5345)
    assert verdict(rows)['verdict'] == 'release'
    for r in rows: r['side'] = 'over'
    assert verdict(rows)['side_balance_pass'] is False
    assert verdict(rows)['verdict'] == 'contain'


def test_opposite_calibration_errors_cannot_cancel_each_other():
    from gradecore.containment import verdict
    from gradecore.tests.test_phase1 import _rows
    rows = _rows(300, .64, .64, be=.5345)
    for r in rows: r['p'] += .08 if r['side'] == 'over' else -.08
    result = verdict(rows)
    assert abs(result['gap']) < .025
    assert result['verdict'] == 'contain' and result['side_balance_pass'] is False
