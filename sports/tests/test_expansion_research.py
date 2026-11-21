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


# ---------------------------------------------------------------- sg-fair (soccer-grades-1120): research shared fairly

def _fair_setup(tmp_path, monkeypatch, n_count=10, n_comp=5):
    from sports import expansion_history as history
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    (tmp_path / 'soccer').mkdir()
    offers = [{'sport': 'soccer', 'platform': 'prizepicks', 'event_id': f'e{i}', 'player_id': f'p{i}', 'player': f'P{i}',
               'stat': 'shots' if i < n_count else 'fantasy_outfield', 'scope': 'full_game',
               'start': '2026-11-21T19:00:00Z'} for i in range(n_count + n_comp)]
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': offers}))
    clock, order = [0.0], []

    def slow_resolve(offer, matches, links=None):       # every research read costs 1 s of the budget
        clock[0] += 1.0
        order.append(offer['player'])
        return None
    monkeypatch.setattr(history, 'resolve', slow_resolve)
    monkeypatch.setattr(research.time, 'monotonic', lambda: clock[0])
    return offers, order


def test_component_offers_get_their_share_of_a_cut_budget(tmp_path, monkeypatch):
    offers, order = _fair_setup(tmp_path, monkeypatch)
    research.publish('soccer', budget_s=6)
    comp = [p for p in order if int(p[1:]) >= 10]
    assert len(order) == 6 and 2 <= len(comp) <= 3          # before: 6 count offers, 0 component (starved for good)


def test_rotation_reaches_every_offer_least_recently_checked_first(tmp_path, monkeypatch):
    offers, order = _fair_setup(tmp_path, monkeypatch)
    runs = []
    for _ in range(3):
        order.clear()
        research.publish('soccer', budget_s=6)
        runs.append(set(order))
    assert not runs[0] & runs[1]                              # run 2 reads only offers run 1 did not reach
    assert set.union(*runs) == {o['player'] for o in offers}  # 18 reads over 3 runs reach all 15 offers


def test_fresh_resolved_entries_are_carried_and_unresolved_ones_retried(tmp_path, monkeypatch):
    from sports.contracts import now_utc
    offers, order = _fair_setup(tmp_path, monkeypatch, n_count=3, n_comp=0)
    prev = {research.key(offers[0]): {'identity': {'competition': 'usa.1', 'event_id': 'x', 'player_id': 'y'},
                                      'games': [], 'checked_at': now_utc(), 'scope': 'regulation'},
            research.key(offers[1]): {'identity': {'competition': 'usa.1', 'event_id': 'x', 'player_id': 'z'},
                                      'games': [], 'checked_at': now_utc(), 'scope': 'regulation',
                                      'error': 'history_unavailable'},
            research.key(offers[2]): {'identity': {'competition': 'usa.1', 'event_id': 'x', 'player_id': 'w'},
                                      'games': [], 'checked_at': '2026-01-01T00:00:00Z', 'scope': 'regulation'}}
    atomic_json(tmp_path / 'soccer' / 'research.json', {'sport': 'soccer', 'players': prev})
    same = '-|-|-|-'                                          # usa.1 store, its WAL, matches.json, stages.json: absent
    atomic_json(tmp_path / 'soccer' / 'research-carry.json', {research.key(o): same for o in offers})
    research.publish('soccer', budget_s=60)
    assert order == ['P2', 'P1']                              # old first, then the failed one; the fresh one carried
    assert research.saved('soccer')['players'][research.key(offers[0])] == prev[research.key(offers[0])]
    # new inputs (stage evidence written): the fresh entry is read again at once
    atomic_json(tmp_path / 'soccer' / 'research.json', {'sport': 'soccer', 'players': prev})
    atomic_json(tmp_path / 'soccer' / 'research-carry.json', {research.key(o): same for o in offers})
    (tmp_path / 'soccer' / 'stages.json').write_text('{}', encoding='utf-8')
    order.clear()
    research.publish('soccer', budget_s=60)
    assert 'P0' in order


def test_event_lookup_is_memoised_per_store_signature_with_identical_answers(monkeypatch):
    from sports import expansion_history as history
    calls = []

    def any_event(con, event_id, before):
        calls.append((event_id, before))
        return {'event': event_id, 'before': before}
    monkeypatch.setattr(history.store, '_any_event', any_event)
    history._EVENTS.clear()
    history._event(None, 'p', (1, 2), 'e1', 'T')                  # off (member requests): no memo
    history._event(None, 'p', (1, 2), 'e1', 'T')
    assert len(calls) == 2 and not history._EVENTS
    calls.clear()
    monkeypatch.setattr(history, '_EVENTS_ON', [True])
    a = history._event(None, 'p', (1, 2), 'e1', 'T')
    b = history._event(None, 'p', (1, 2), 'e1', 'T')
    assert a == b == {'event': 'e1', 'before': 'T'} and calls == [('e1', 'T')]
    history._event(None, 'p', (1, 3), 'e1', 'T')                  # the store file changed: asked again
    history._event(None, 'p', (1, 3), 'e1', 'U')                  # another cut-off: asked again
    assert len(calls) == 3
    from pathlib import Path
    memo = research._MemoStore(history, Path('p'))
    assert memo._latest_rows is history.store._latest_rows        # everything else is the store itself


def test_publish_drops_the_event_memo_when_it_ends(setup, monkeypatch):
    from sports import expansion_history as history
    root, path, match, offer = setup
    offer.update(player_id='native', event_id='native', sport='tennis')
    atomic_json(path.parent/'matches.json', {match['event_id']: match})
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': [offer]}))
    seen = []
    real = history._event

    def spy(*a):
        seen.append(history._EVENTS_ON[0])
        return real(*a)
    monkeypatch.setattr(history, '_event', spy)
    assert research.publish('tennis')['with_history'] == 1
    assert seen and all(seen) and history._EVENTS_ON[0] is False and history._EVENTS == {}
