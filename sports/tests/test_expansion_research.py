"""Materialized member data uses real canonical fixtures, not simulated grades."""
import json
from types import SimpleNamespace

from sports import expansion_research as research, expansion_board as boards
from sports.expansion_history import atomic_json
from sports.tests.test_expansion_learning import setup


def test_worker_publishes_exact_stat_and_period_without_grades(setup, monkeypatch):
    root, path, match, offer = setup
    offer.update(player_id='native', event_id='native', sport='tennis')
    atomic_json(path.parent/'matches.json', {match['event_id']: match})
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': [offer]}))
    result = research.publish('tennis')
    assert result['with_history'] == 1
    saved = research.saved('tennis')['players'][research.key(offer)]
    assert [g['value'] for g in saved['games']] == [10, 8, 6, 4, 2]
    assert saved['stats'][0]['average'] == 6
    assert 'projection' not in saved and 'grade' not in saved
    # Changed scope is never served another period's history.
    other = dict(offer, scope='set1')
    assert not research.attach('tennis', {'offers': [other]})['research']


def test_missing_identity_never_becomes_zero_history(setup, monkeypatch):
    root, path, match, offer = setup
    offer.update(player_id='native', event_id='native', sport='tennis', player='Different Player')
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': [offer]}))
    result = research.publish('tennis')
    assert result['with_history'] == 0
    saved = research.saved('tennis')['players'][research.key(offer)]
    assert saved['identity'] is None and saved['games'] == []


def test_read_is_file_only_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    root = tmp_path/'soccer';root.mkdir()
    atomic_json(root/'research.json', {'sport':'tennis', 'players':{'wrong':{}}})
    assert research.saved('soccer') == {}
    (root/'research.json').write_text(' ' * 4_000_001)
    assert research.saved('soccer') == {}


def test_research_fit_keeps_earliest_starts_under_the_read_limit():
    from sports import expansion_research as research
    players = {k: {'games': ['x' * 50]} for k in 'abc'}
    starts = {'a': '2026-11-20', 'b': '2026-11-18', 'c': '2026-11-19'}
    assert research.fit(players, starts) is players
    import json
    one = len(json.dumps(players['a'], separators=(',', ':'))) + 40
    assert list(research.fit(players, starts, limit=2 * one)) == ['b', 'c']
