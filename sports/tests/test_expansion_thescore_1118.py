"""theScore tennis (2026-11-18): extra serve stats under new names, rates as percentages, games_won never written,
and pre-match context for offered upcoming matches (exact pair + 2 h + unique; known_at = fetch time)."""
import json

import pytest

from sports import expansion_tennis as tennis
from sports.contracts import now_utc
from sports.expansion import parse_scoreboard
from sports.expansion_forecasts import FORECAST_INPUTS
from sports.tests.test_expansion_tennis import scoreboard, source


def _full():
    body = source()
    body['team_1_stats'].update({'total_breakpoints': 5, 'max_points_in_a_row': 6, 'max_games_in_a_row': 3,
                                 'first_serve_points_won': 67, 'second_serve_successful': 97,
                                 'second_serve_points_won': 47, 'games_won': 12, 'games_won_percentage': 60,
                                 'first_serve_successful_percentage': 59})
    return body


def test_rates_are_percent_counts_are_counts_and_games_won_is_never_written():
    matches = parse_scoreboard(scoreboard(), 'atp', now_utc()).matches
    rows = tennis.parse_match(_full(), matches, now_utc()).records
    mine = {r.stat: (r.unit, r.value) for r in rows if r.participant_id.endswith(':1')}
    assert mine['bp_chances'] == ('count', 5) and mine['max_points_streak'] == ('count', 6)
    assert mine['max_games_streak'] == ('count', 3)
    for stat in ('first_serve_in_pct', 'first_serve_won_pct', 'second_serve_in_pct', 'second_serve_won_pct'):
        assert mine[stat][0] == 'percent'
    assert 'games_won' not in {r.stat for r in rows}
    assert not any(r.stat.endswith('_percentage') or r.stat in ('first_serve_points_won', 'second_serve_successful')
                   for r in rows)
    # none of the new names is a forecast input
    assert not {s for _c, _src, s in FORECAST_INPUTS} & {s for s, _u in tennis.EXTRA_STATS.values()}


def test_a_rate_over_100_is_refused():
    matches = parse_scoreboard(scoreboard(), 'atp', now_utc()).matches
    body = _full()
    body['team_1_stats']['first_serve_points_won'] = 167
    assert 'first_serve_won_pct' not in {r.stat for r in tennis.parse_match(body, matches, now_utc()).records}


@pytest.mark.parametrize('change', [{'is_double': True}, {'description': 'Retirement'}])
def test_retired_and_doubles_store_nothing(change):
    matches = parse_scoreboard(scoreboard(), 'atp', now_utc()).matches
    body = {**_full(), **change}
    assert not tennis.parse_match(body, matches, now_utc()).records


START = '2026-11-20T13:00:00Z'


def _catalog(*extra):
    m = {'event_id': 'atp:espn:atp:1:9', 'competition': 'atp', 'start': START, 'status': 'scheduled',
         'participants': [{'id': 'atp:player:espn:1', 'name': 'Carlos Alcaraz'},
                          {'id': 'atp:player:espn:2', 'name': 'Flavio Cobolli'}]}
    rows = {m['event_id']: m}
    for i, e in enumerate(extra):
        rows[f'x{i}'] = {**m, 'event_id': f'atp:espn:atp:1:{20 + i}', **e}
    return {'atp': rows}


def _upcoming(**kw):
    body = {'id': 171717, 'league': {'slug': 'atp'}, 'status': 'pre_game', 'is_double': False,
            'match_date': 'Fri, 20 Nov 2026 13:30:00 -0000', 'round_name': 'Semi-finals', 'court_name': 'Inalpi Arena',
            'team_1': {'player1': {'first_name': 'Carlos', 'last_name': 'Alcaraz'}},
            'team_2': {'player1': {'first_name': 'Flavio', 'last_name': 'Cobolli'}},
            'team_1_standing': {'place': 1, 'points': 11050}, 'team_2_standing': {'place': 22, 'points': 2100},
            'team_1_seed': 1, 'team_2_seed': None,
            'teams_comparison': {'team_1': {'head_to_head': '3-1', 'head_to_head_hard': '1-0',
                                            'last_10_matches_wins_losses': '8-2', 'streak': 'W4',
                                            'wins_losses': '60-9', 'wins_losses_hard': '30-5'},
                                 'team_2': {'head_to_head': '1-3', 'last_10_matches_wins_losses': '2-8',
                                            'streak': 'L4', 'wins_losses': '17-18'}},
            'odds': {'team_1': '-400'}}
    body.update(kw)
    return body


def test_prematch_context_joins_exactly_and_is_known_at_fetch_time():
    at = '2026-11-19T10:00:00Z'
    eid, entry = tennis.prematch(_upcoming(), _catalog(), at)
    assert eid == 'atp:espn:atp:1:9' and entry['known_at'] == at and entry['source'] == 'thescore'
    p1 = entry['players']['atp:player:espn:1']
    assert p1['rank'] == 1 and p1['points'] == 11050 and p1['seed'] == 1 and p1['h2h'] == '3-1'
    assert p1['h2h_surface'] == '1-0' and p1['surface'] == 'hard' and p1['last10'] == '8-2' and p1['streak'] == 'W4'
    assert 'seed' not in entry['players']['atp:player:espn:2']
    assert entry['round'] == 'Semi-finals' and 'odds' not in json.dumps(entry)


@pytest.mark.parametrize('case', ['two_candidates', 'one_side', 'far', 'final', 'doubles', 'not_scheduled'])
def test_prematch_abstains(case):
    body, catalog = _upcoming(), _catalog()
    if case == 'two_candidates':
        catalog = _catalog({'start': '2026-11-20T14:00:00Z'})
    if case == 'one_side':
        body['team_2'] = {'player1': {'first_name': 'Other', 'last_name': 'Player'}}
    if case == 'far':
        body['match_date'] = 'Fri, 20 Nov 2026 16:30:00 -0000'
    if case == 'final':
        body['status'] = 'final'
    if case == 'doubles':
        body['is_double'] = True
    if case == 'not_scheduled':
        catalog['atp']['atp:espn:atp:1:9']['status'] = 'final'
    assert tennis.prematch(body, catalog, '2026-11-19T10:00:00Z') is None


def test_context_file_is_bounded_and_drops_old_matches(tmp_path):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    rows = {f'e{i}': {'event_id': f'e{i}', 'start': (now + timedelta(minutes=i - 600)).isoformat().replace('+00:00', 'Z')}
            for i in range(tennis.CONTEXT_MAX + 700)}
    tennis.save_contexts(tmp_path, rows, now)
    saved = tennis.load_contexts(tmp_path)
    assert len(saved) == tennis.CONTEXT_MAX
    assert min(v['start'] for v in saved.values()) >= (now - timedelta(hours=6)).isoformat()[:19]


def test_a_failed_request_keeps_the_saved_context(tmp_path):
    from sports import net
    tennis.save_contexts(tmp_path, {'e': {'event_id': 'e', 'start': '2099-01-01T00:00:00Z'}},
                         __import__('datetime').datetime.now(__import__('datetime').timezone.utc))

    def get(url, params):
        raise net.HttpError(503, url, '')
    errors = tennis.collect_details({}, get, lambda *a: None, tmp_path)
    assert errors and tennis.load_contexts(tmp_path) == {'e': {'event_id': 'e', 'start': '2099-01-01T00:00:00Z'}}
