"""Grade leak guard (2026-11-18): new research sources, competitions and stat names never reach a forecast.

Factor rule: data moves grades only after a walk-forward test. expansion_forecasts.FORECAST_INPUTS is the frozen
(competition, source, stat) allowlist of what 53dad0b wrote; everything else is shown in research at weight 0.
"""
from contextlib import closing
import json
from types import SimpleNamespace

import pytest

from sports import expansion_board as boards, expansion_forecasts as model, expansion_research as research, store
from sports.contracts import Event, Player, SourceId, StatObservation
from sports.expansion import isolated_database
from sports.expansion_history import atomic_json

NOW = '2026-11-17T23:00:00Z'
START = '2026-11-18T18:00:00Z'


def _facts(comp, source, stat='shots'):
    games = [{'event_id': f'soccer:old:{i}', 'date': f'2026-11-{i + 1:02}T12:00:00Z',
              'known_at': f'2026-11-{i + 1:02}T18:00:00Z', 'value': v, 'source': source}
             for i, v in enumerate([5, 2, 4, 3, 5, 2, 4, 1])]
    ident = {'event_id': 'soccer:event:1', 'player_id': 'soccer:player:1', 'competition': comp}
    return ({'sport': 'soccer', 'platform': 'prizepicks', 'offer_id': 'o', 'revision': 'r', 'stat': stat,
             'scope': 'full_game', 'variant': 'standard', 'line': 2.5, 'start': START, 'sides': {'over': {}, 'under': {}}},
            {'identity': ident, 'scope': 'regulation', 'context': {}, 'games': games,
             'event': {'event_id': ident['event_id'], 'status': 'scheduled', 'start': START}})


@pytest.mark.parametrize('comp,source,stat,allowed', [
    ('por.1', 'espn', 'shots', True),            # 53dad0b input
    ('eng.1', 'fotmob', 'tackles', True),        # 53dad0b input (FotMob's original six leagues)
    ('por.1', 'fotmob', 'tackles', False),       # FotMob extended to an already-regulation league: weight 0
    ('eng.2', 'espn', 'shots', False),           # a new ESPN competition: weight 0, any scope
    ('eng.1', 'football-data', 'shots', False),  # never a canonical source, but refused anyway
])
def test_only_frozen_inputs_feed_the_forecast(comp, source, stat, allowed):
    offer, facts = _facts(comp, source, stat)
    if comp == 'eng.2':
        offer['scope'] = 'first_half'            # passes the regulation-league check; the guard still holds
        facts['scope'] = 'first_half'
    f, reason = model.fit(offer, facts, NOW)
    assert (f is not None) is allowed, reason
    if not allowed:
        assert reason in ('more_exact_history_needed', 'competition_mismatch')


def test_the_allowlist_is_frozen_not_derived_from_growing_tables():
    import inspect
    src = inspect.getsource(model._inputs)
    for mutable in ('COMPETITIONS', 'LEAGUES', 'FIELDS', 'STATS', 'COUNT_STATS', 'SOCCER_LEAGUE_IDS'):
        assert mutable not in src, mutable
    assert len(model.FORECAST_INPUTS) == 24 * 9 + 6 * 13 + 2 * 5 + 2 * 5 + 2 * 13


def _new_stat_names():
    """(competition, source) -> stat names each collector can write into canonical.db today."""
    from sports import expansion, expansion_challenger as ch, expansion_soccer as es, expansion_soccer_detail as fm
    from sports import expansion_tennis as tn
    out = {}
    for comp, (sport, _) in expansion.COMPETITIONS.items():
        if sport == 'soccer':
            out[(comp, 'espn')] = set(es.STATS.values())
    for comp in set(fm.LEAGUES.values()):
        out[(comp, 'fotmob')] = {stat for stat, _f, _u in fm.FIELDS.values()}
    for tour in ('atp', 'wta'):
        out[(tour, 'espn')] = {'games_won', 'games_played', 'sets_won', 'sets_played', 'tie_breakers_played'}
        out[(tour, 'thescore')] = set(tn.COUNT_STATS) | {s for s, _u in getattr(tn, 'EXTRA_STATS', {}).values()}
    for comp in ('atp_challenger', 'wta125'):
        out[(comp, '365scores')] = ({'games_won', 'games_played', 'sets_won', 'sets_played', 'tie_breakers_played'}
                                    | set(ch.COUNTS.values()) | set(ch.FRACTIONS.values()))
    return out


def test_no_two_sources_write_one_stat_name_in_one_competition():
    """_history drops a game when sources disagree: an overlapping new source would silently remove inputs."""
    names = _new_stat_names()
    for (comp, a), sa in names.items():
        for (comp2, b), sb in names.items():
            if comp == comp2 and a < b:
                assert not sa & sb, (comp, a, b, sa & sb)


# ------------------------------------------------------------------------------------------- byte-identity replay
def _store(tmp_path, comp, extra):
    """One scheduled por.1 match with an offered player, 8 completed ESPN games; `extra` adds research-only rows."""
    pid, tid, oid = 'soccer:player:espn:9', 'soccer:team:espn:1', 'soccer:team:espn:2'
    match = {'event_id': f'soccer:espn:{comp}:500:500', 'competition': comp, 'start': START, 'status': 'scheduled',
             'participants': [{'id': tid, 'name': 'Home FC'}, {'id': oid, 'name': 'Away FC'}],
             'players': [{'id': pid, 'name': 'Ana Silva', 'team_id': tid}]}
    root = tmp_path / 'soccer'
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / 'current-matches.json', [match])
    path = isolated_database(tmp_path, comp)
    rows = [Player(pid, 'soccer', 'Ana Silva', source_ids=(SourceId('espn', '9'),), observed_at=NOW, known_at=NOW),
            Event(match['event_id'], 'soccer', 2026, 'unknown', None, None, START, 'espn', status='scheduled',
                  observed_at=NOW, known_at=NOW)]
    for i, v in enumerate([3, 1, 2, 4, 0, 2, 3, 1]):
        eid, start, known = f'soccer:espn:{comp}:{i}:{i}', f'2026-11-{i + 1:02}T12:00:00Z', f'2026-11-{i + 1:02}T18:00:00Z'
        rows.append(Event(eid, 'soccer', 2026, 'unknown', None, None, start, 'espn', status='final',
                          observed_at=known, known_at=known))
        rows.append(StatObservation(eid, 'soccer', pid, 'player', 'regulation', 'no', 'shots', 'count', v, 'played',
                                    'espn', str(i), True, known, known))
        for source, stat, value in extra:
            rows.append(StatObservation(eid, 'soccer', pid, 'player', 'regulation', 'no', stat,
                                        'percent' if stat.endswith('_pct') else 'count', value + i % 3, 'played',
                                        source, f'x{i}', True, known, known))
    with closing(store.connect(path)) as c:
        assert not store.put_many(c, rows).get('quarantined', 0)
    atomic_json(path.parent / 'matches.json', {match['event_id']: match})
    offers = [{'sport': 'soccer', 'platform': 'prizepicks', 'offer_id': f'o-{stat}', 'revision': 'r1', 'player': 'Ana Silva',
               'team': 'Home FC', 'teams': ['Home FC', 'Away FC'], 'player_id': 'pp:9', 'event_id': 'pp:e', 'stat': stat,
               'scope': 'full_game', 'variant': 'standard', 'line': 1.5, 'start': START, 'observed_at': NOW,
               'sides': {'over': {}, 'under': {}}} for stat in ('shots', 'tackles', 'passes_attempted')]
    return root, offers


@pytest.mark.parametrize('comp', ['por.1', 'sco.1', 'tur.1'])
def test_forecasts_json_is_byte_identical_with_and_without_new_source_rows(tmp_path, monkeypatch, comp):
    out = []
    for i, extra in enumerate(([], [('fotmob', 'tackles', 2), ('fotmob', 'passes_attempted', 30),
                                    ('fotmob', 'xg', 0), ('fotmob', 'minutes', 80)])):
        base = tmp_path / str(i)
        monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(base))
        root, offers = _store(base, comp, extra)
        monkeypatch.setattr(boards, 'board', lambda req, offers=offers: (200, {'offers': offers}))
        research._read.cache_clear()
        assert research.publish('soccer')['with_history'] == (1 if not extra else 3)
        model.publish('soccer', NOW)
        out.append((root / 'forecasts.json').read_bytes())
    doc = json.loads(out[0])
    assert len(doc['forecasts']) == 1 and next(iter(doc['forecasts'].values()))['stat'] == 'shots'
    assert out[0] == out[1]
    # Sensitivity: without the guard the same FotMob rows WOULD add letters (so the identity above is the guard's doing).
    monkeypatch.setattr(model, 'FORECAST_INPUTS', model.FORECAST_INPUTS | {(comp, 'fotmob', 'tackles'),
                                                                           (comp, 'fotmob', 'passes_attempted')})
    model.publish('soccer', '2026-11-17T23:05:00Z')
    assert len(json.loads((root / 'forecasts.json').read_bytes())['forecasts']) == 3
