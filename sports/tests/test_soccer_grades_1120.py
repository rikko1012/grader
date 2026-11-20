"""soccer-grades-1120: integration of sg-stability + sg-scope + sg-stats, and every offered prop gets a letter.

- A line the policy grades keeps its exact payload (no new key, same id).
- A line the policy cannot letter (payout of a demon / goblin line not published)
  gets the band of the model's own chance at 0 units with a definite reason (owner 2026-11-20; display gd-1).
- Component models use the sg-scope extra-time rule and tennis tour format exactly as the count path does.
"""
import copy
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from gradecore import policy

from sports import expansion_forecasts as model, expansion_stats as stats
from sports.tests.test_expansion_forecasts import fixture, NOW
from sports.tests.test_expansion_stats_1120 import facts_with, ESPN_GA


def test_policy_graded_line_has_no_new_key():
    offer, facts = fixture()
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['grade'] and 'letter_basis' not in f and f['units'] is None


@pytest.mark.parametrize('variant', ['goblin', 'demon'])
def test_alt_line_gets_chance_letter_at_zero_units(variant):
    offer, facts = fixture()
    offer.update(variant=variant)
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['grade'] == policy.letter_for(f['p_side'])
    assert f['units'] == 0.0 and f['letter_basis'] == 'chance_payout_unknown'
    assert f['grade_reason'][0] == policy.ALT_LINE            # the policy's own reason is kept first
    assert variant in f['stake_reason'] and f['stake_reason'].startswith('0U')


def test_low_chance_alt_line_is_a_lean_never_a_band():
    offer, facts = fixture()
    offer.update(variant='goblin', line=3.5)
    f, _ = model.make_forecast(offer, facts, NOW)
    if f['p_side'] < policy.LETTER_CUTS[-1][1]:
        assert f['grade'] == policy.LEAN


def test_one_sided_line_grades_the_offered_side_with_its_own_chance():
    # decision 1 (2026-11-20): never a letter for a side the line does not offer - the offered side, its own chance
    offer, facts = fixture()
    offer.update(line=8.5, sides={'over': {}})
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['side'] == 'over' and f['p_side'] < .5 and f['p_side'] == pytest.approx(f['p_over'] / (1 - f['p_push']))
    assert f['grade'] == policy.LEAN and 'letter_basis' not in f
    two = dict(offer, sides={'over': {}, 'under': {}})
    g, _ = model.make_forecast(two, facts, NOW)
    assert g['side'] == 'under' and g['grade'] == policy.grade(g['p_side'], 'prizepicks', 'standard').letter


def test_component_model_letter_basis_keeps_the_tier_stake_reason():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA)
    offer.update(stat='goals_assists', line=0.5, variant='goblin')
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['grade'] and f['units'] == 0.0
    assert f['stake_reason'] == stats.STAKE_REASONS[f['stake_tier']]
    assert f['letter_basis'] == 'chance_payout_unknown'


@pytest.mark.parametrize('platform,extra_time,expect_scopes', [
    ('prizepicks', 'none', None), ('underdog', 'possible', None),
    ('prizepicks', 'possible', ['full_game', 'regulation'])])
def test_component_model_uses_the_scope_rule(platform, extra_time, expect_scopes):
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA, comp='mex.1')
    offer.update(stat='goals_assists', line=0.5, platform=platform)
    facts['scope_rule'] = {'version': 'sg-scope-1', 'extra_time': extra_time, 'basis': 'test', 'p_extra_time': 0.2}
    fitted, reason = model.fit(offer, facts, NOW)
    assert reason is None
    assert fitted['scope_rule']['extra_time'] == extra_time
    assert fitted.get('settle_scopes') == expect_scopes


def test_component_model_without_platform_rule_stays_unverified():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA, comp='mex.1')
    offer.update(stat='goals_assists', line=0.5, platform='otherbook')
    facts['scope_rule'] = {'extra_time': 'possible'}
    assert model.fit(offer, facts, NOW)[1] == 'extra_time_scope_unverified'


def test_regulation_league_component_has_no_scope_fields():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA)
    offer.update(stat='goals_assists', line=0.5)
    fitted, _ = model.fit(offer, facts, NOW)
    assert 'scope_rule' not in fitted and 'settle_scopes' not in fitted


def test_tennis_fantasy_reads_the_tour_format_rule():
    offer, facts = fixture('tennis')
    offer.update(stat='fantasy_tennis', scope='full_game', line=20.5)
    facts['identity']['competition'] = 'atp_challenger'
    facts['scope'] = 'full_game'
    assert model.fit(offer, facts, NOW)[1] == 'match_format_missing'
    facts['format_rule'] = {'best_of': 3, 'basis': 'test'}
    fitted, reason = model.fit(offer, facts, NOW)
    # the format is now known; whatever the history decides, it is never the format gate again
    assert reason != 'match_format_missing'
    if fitted:
        assert fitted['format_rule'] == {'best_of': 3, 'basis': 'test'}


def test_first_half_goal_history_keeps_the_count_path():
    offer, facts = fixture()
    offer.update(stat='goals', scope='first_half', line=0.5)
    facts['scope'] = 'first_half'
    for g in facts['games']:
        g['value'] = min(g['value'], 2)
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and 'component_model' not in f


def _obs(value, scope, known='2026-11-18T22:00:00Z'):
    return SimpleNamespace(final=True, participation='played', value=value, known_at=known, scope=scope)


def test_component_settlement_prefers_extra_time_box_totals():
    f = {'component_model': 'goals_assists', 'identity': {'player_id': 'p'}, 'history_scope': 'regulation',
         'settle_scopes': ['full_game', 'regulation']}
    by_key = {('p', 'goals', 'full_game'): [({'record_id': 'a'}, _obs(2, 'full_game'))],
              ('p', 'assists', 'full_game'): [({'record_id': 'b'}, _obs(1, 'full_game'))],
              ('p', 'goals', 'regulation'): [({'record_id': 'c'}, _obs(1, 'regulation'))],
              ('p', 'assists', 'regulation'): [({'record_id': 'd'}, _obs(0, 'regulation'))]}
    assert stats.actual(f, by_key)[0] == 3
    del by_key[('p', 'goals', 'full_game')]
    assert stats.actual(f, by_key)[0] == 1          # no extra-time box total: the 90 minutes
    f.pop('settle_scopes')
    assert stats.actual(f, by_key)[0] == 1


# ---------------------------------------------------------------- thin-1 (fewer than 5 exact games)

def test_thin_history_gets_a_population_prior_grade():
    offer, facts = fixture()
    facts['games'] = facts['games'][:3]
    offer['role'] = 'Forward'
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['distribution'] == 'thin-history-population-prior'
    assert f['thin_model']['group'] == 'F' and f['thin_model']['games'] == 3
    assert f['grade'] and f['units'] == 0.0 and f['stake_reason'] == model.THIN_REASONS['passed']


def test_thin_history_with_no_game_uses_the_prior_alone():
    offer, facts = fixture()
    facts['games'] = []
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['thin_model']['group'] == 'U' and f['sample'] == 0
    m0 = model.THIN_PRIOR['shots']['U'][0]
    assert f['projection'] == pytest.approx(m0, rel=1e-6)


def test_thin_never_replaces_the_count_model_and_not_for_untested_inputs():
    offer, facts = fixture()
    f, _ = model.make_forecast(offer, facts, NOW)
    assert 'thin_model' not in f                                  # 10 games: the unchanged count path
    offer, facts = fixture()
    offer['stat'] = 'passes_attempted'                            # FotMob stat: thin-1 was not tested on it
    facts['games'] = []
    assert model.fit(offer, facts, NOW)[0]['thin_model']['version'] == 'thin-2'      # its own prior (thin-2)
    offer['stat'] = 'shots'                                       # a non-input (FotMob shots) game: weight 0
    facts['games'] = [dict(fixture()[1]['games'][0], source='fotmob')]
    f = model.fit(offer, facts, NOW)[0]
    assert f['thin_model']['version'] == 'thin-1' and f['thin_model']['games'] == 0
    offer, facts = fixture('tennis')
    facts['games'] = facts['games'][:2]
    # thin-1 is soccer only; a tennis finite-score line under 5 matches takes thin-t1 (tests below), never thin-1
    f, reason = model.fit(offer, facts, NOW)
    assert reason is None and f['thin_model']['version'] == 'thin-t1'


@pytest.mark.parametrize('role,group', [('Goalkeeper', 'G'), ('Defender', 'D'), ('Center Back', 'D'),
                                        ('Midfielder', 'M'), ('Forward', 'F'), (None, 'U'), ('Coach', 'U')])
def test_position_groups(role, group):
    assert model.position_group(role) == group


def test_checked_book_team_labels_resolve_exactly():
    from sports import expansion_history as h
    match = {'event_id': 'soccer:espn:aus.1:1:1', 'competition': 'aus.1', 'start': '2026-11-21T06:00:00Z',
             'participants': [{'id': 'soccer:team:espn:5325', 'name': 'Central Coast Mariners FC',
                               'short_name': 'CC Mariners', 'abbreviation': 'CCM'},
                              {'id': 'soccer:team:espn:5322', 'name': 'Perth Glory FC', 'short_name': 'Perth Glory',
                               'abbreviation': 'PER'}],
             'players': [{'id': 'soccer:player:espn:7', 'team_id': 'soccer:team:espn:5322', 'name': 'Adam Taggart'}]}
    offer = {'sport': 'soccer', 'start': '2026-11-21T06:00:00Z', 'teams': ['Perth', 'Central Coast'], 'team': 'Perth',
             'player': 'Adam Taggart'}
    assert h.resolve(offer, [match])['player_id'] == 'soccer:player:espn:7'
    assert h.resolve(dict(offer, teams=['Pert', 'Central Coast'], team='Pert'), [match]) is None   # never a prefix
    assert 'perth' not in h.participant_names({'id': 'soccer:team:espn:999', 'name': 'Perth Glory FC'})
    # close-1120: Liga MX labels (ESPN lists 'Santos' / 'América' only)
    mx = {'event_id': 'soccer:espn:mex.1:2:2', 'competition': 'mex.1', 'start': '2026-11-21T23:00:00Z',
          'participants': [{'id': 'soccer:team:espn:225', 'name': 'Santos', 'short_name': 'Santos', 'abbreviation': 'SAN'},
                           {'id': 'soccer:team:espn:228', 'name': 'León', 'short_name': 'León', 'abbreviation': 'LEO'}],
          'players': [{'id': 'soccer:player:espn:8', 'team_id': 'soccer:team:espn:225', 'name': 'Franco Pardo'}]}
    o = {'sport': 'soccer', 'start': '2026-11-21T23:00:00Z', 'teams': ['León', 'Santos Laguna'], 'team': 'Santos Laguna',
         'player': 'Franco Pardo'}
    assert h.resolve(o, [mx])['player_id'] == 'soccer:player:espn:8'
    assert h.resolve(dict(o, teams=['León', 'Laguna'], team='Laguna'), [mx]) is None
    assert 'club america' in h.participant_names({'id': 'soccer:team:espn:227', 'name': 'América'})
    assert 'santos laguna' not in h.participant_names({'id': 'soccer:team:espn:2', 'name': 'Santos'})   # Brazil's Santos: no


def test_thin_prior_alone_where_history_is_not_an_input():
    offer, facts = fixture()
    offer['stat'] = 'saves'
    facts['identity']['competition'] = 'aus.1'                   # aus.1 ESPN saves: not promoted (bias)
    facts['scope_rule'] = {'extra_time': 'none', 'basis': 'test'}   # A-League regular season
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['sample'] == 0 and f['inputs'] == []
    assert f['stake_reason'] == model.THIN_REASONS['failed']


# --- tennis / Underdog board carry on a failed read (fault injection 2026-11-20: a 429 storm emptied tennis 253 -> 0)

def _board_row(platform, offer_id, observed, start):
    return {'sport': 'tennis', 'platform': platform, 'offer_id': offer_id, 'revision': 'r' + offer_id,
            'observed_at': observed, 'start': start, 'variant': 'standard', 'sides': {'over': {'multiplier': None}}}


def test_failed_source_carries_last_good_rows_inside_max_age_only(monkeypatch, tmp_path):
    import json as _json
    from sports import expansion_board as board, expansion_odds, net
    from sports.contracts import now_utc, parse_utc
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(expansion_odds, 'collect', lambda sport, get, directory: ([], {}))
    now = parse_utc(now_utc())
    iso = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    fresh, stale = iso(now - timedelta(seconds=60)), iso(now - timedelta(seconds=board.MAX_AGE + 60))
    later, gone = iso(now + timedelta(hours=3)), iso(now - timedelta(minutes=5))
    d = board.root('tennis'); d.mkdir(parents=True)
    prev = [_board_row('prizepicks', '1', fresh, later), _board_row('prizepicks', '2', stale, later),
            _board_row('prizepicks', '3', fresh, gone), _board_row('underdog', '4', fresh, later)]
    (d / 'board.json').write_text(_json.dumps({'sport': 'tennis', 'built_at': fresh, 'sources': {}, 'offers': prev}))

    def get_json(sport, url, params=None, **kw):
        if 'prizepicks' in url:
            raise net.HttpError(429, url)
        return {}                                          # Underdog answers (empty)
    monkeypatch.setattr('sports.expansion_sources.get_json', get_json)
    snap = board.collect('tennis')
    kept = [(r['platform'], r['offer_id']) for r in snap['offers']]
    assert kept == [('prizepicks', '1')]                   # stale and started rows drop; Underdog read fine -> its own rows
    assert snap['offers'][0]['observed_at'] == fresh       # never re-stamped
    assert snap['sources']['prizepicks'] == {**snap['sources']['prizepicks'], 'ok': False, 'carried': 1}
    # the member snapshot never re-stamps carried rows with the failed read's checked_at
    path = d / 'board.json'
    data = board._prepared(path, path.stat(), datetime.now(timezone.utc), 'tennis')
    hit = next(v for v in board._PREPARED.values() if v['data'] is data)
    assert hit['restamp'] is False and [r['offer_id'] for r in data['offers']] == ['1']


def test_successful_reads_never_carry(monkeypatch, tmp_path):
    import json as _json
    from sports import expansion_board as board, expansion_odds
    from sports.contracts import now_utc, parse_utc
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(expansion_odds, 'collect', lambda sport, get, directory: ([], {}))
    now = parse_utc(now_utc())
    iso = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    d = board.root('tennis'); d.mkdir(parents=True)
    prev = [_board_row('prizepicks', '1', iso(now - timedelta(seconds=30)), iso(now + timedelta(hours=3)))]
    (d / 'board.json').write_text(_json.dumps({'sport': 'tennis', 'built_at': prev[0]['observed_at'], 'sources': {},
                                               'offers': prev}))
    monkeypatch.setattr('sports.expansion_sources.get_json', lambda sport, url, params=None, **kw: {})
    snap = board.collect('tennis')
    assert snap['offers'] == [] and all('carried' not in s for s in snap['sources'].values())


# --- thin-t1: tennis finite-score lines under 5 exact matches (walk-forward failed -> 0U, letter + chance kept)

def test_tennis_thin_population_prior_grade_at_0u():
    from sports import expansion_thin_tennis as tt
    offer, facts = fixture('tennis')                     # atp_challenger, set-1 games won 4.5
    facts['games'] = facts['games'][:3]
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['distribution'] == 'thin-history-population-prior-finite'
    assert f['thin_model']['version'] == tt.VERSION and f['thin_model']['games'] == 3 and f['sample'] == 3
    assert f['grade'] and f['units'] == 0.0 and f['stake_reason'] == model.THIN_REASONS['failed']
    assert sum(f['p_' + s] for s in ('over', 'under', 'push')) == pytest.approx(1)


def test_tennis_thin_never_replaces_the_count_model_or_runs_without_a_prior():
    offer, facts = fixture('tennis')
    f, _ = model.make_forecast(offer, facts, NOW)
    assert 'thin_model' not in f and f['distribution'] == 'finite-score-dirichlet'      # 10 matches: unchanged
    offer, facts = fixture('tennis')
    facts['games'] = facts['games'][:2]
    facts['identity']['competition'] = 'itf_men'                                         # no tested tour prior
    assert model.make_forecast(offer, facts, NOW)[1] in ('more_exact_history_needed', 'competition_mismatch')
    offer, facts = fixture('tennis')
    facts['games'] = facts['games'][:2]
    offer.update(stat='aces', scope='full_game', line=5.5)                               # not finite-score: no thin-t1
    facts['scope'] = 'full_game'
    facts['context'] = {'best_of': 3}
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['thin_model']['version'] == 'thin-t2'                                     # gaps: the serve-stat prior


def test_tennis_thin_prior_tables_are_distributions_on_the_fit_support():
    from sports import expansion_thin_tennis as tt
    for (g, bo, scope, stat), (n, pop) in tt.PRIOR.items():
        assert n > 0 and abs(sum(pop) - 1) < 1e-3 and all(p > 0 for p in pop)
        assert tt.TIERS[f'{scope}|{stat}'] == 'failed'


# --- never-loaded roster slots go to the next kickoff first (not slate order)

def test_never_loaded_rosters_take_slots_by_next_kickoff(tmp_path):
    import re
    from sports import expansion_history as history
    from sports.tests.test_expansion_killswitch_1118 import _slate, _getter
    catalogs = _slate(tmp_path, [1, 2, 3, 4], [])
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    later = (now + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = catalogs['bra.1']
    rows['0']['start'], rows['2']['start'] = later, soon        # teams 3 / 4 play first, listed second
    import json as _json
    board = _json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))
    for o in board['offers']:
        o['start'] = soon if o['team'] in ('Team3', 'Team4') else later
    history.atomic_json(tmp_path / 'board.json', board)
    calls = []
    history.collect_soccer_teams(catalogs, _getter(calls), lambda *a: True, tmp_path, 'base')
    assert [re.search(r'/teams/(\d+)/', u)[1] for u in calls[::2]] == ['3', '4']


def test_due_refresh_still_after_never_loaded_rosters(tmp_path):
    import re
    from sports import expansion_history as history
    from sports.tests.test_expansion_killswitch_1118 import _slate, _getter
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    state = {f'bra.1:soccer:team:espn:{t}': {'checked_at': old, 'schedule_checked_at': old} for t in (1, 2)}
    catalogs = _slate(tmp_path, [1, 2, 3, 4], [], state)
    calls = []
    history.collect_soccer_teams(catalogs, _getter(calls), lambda *a: True, tmp_path, 'base')
    assert [re.search(r'/teams/(\d+)/', u)[1] for u in calls[::2]] == ['3', '4']


# --- orphan temp files of hard-killed atomic writes (fault injection: 13 kills left 13 files / 6.2 MB) are swept

def test_orphan_temps_older_than_15_min_are_swept_only(tmp_path):
    import os
    from sports import expansion_history as history
    (tmp_path / 'eng.1').mkdir()
    (tmp_path / 'archive' / 'quotes').mkdir(parents=True)
    old = [tmp_path / 'history-a.tmp', tmp_path / 'eng.1' / 'history-b.tmp', tmp_path / 'board-c.tmp']
    keep = [tmp_path / 'history-fresh.tmp', tmp_path / 'archive' / 'quotes' / 'history-x.tmp',
            tmp_path / 'other-d.tmp', tmp_path / 'history-state.json']
    for p in old + keep:
        p.write_text('{}')
    t = time.time()
    for p in old + keep[1:]:
        os.utime(p, (t - 3600, t - 3600))
    assert history.sweep_orphan_temps(tmp_path, now=t) == 3
    assert not any(p.exists() for p in old) and all(p.exists() for p in keep)


# ---------------------------------------------------------------- thin-2 (gaps: FotMob counts + component models)

@pytest.mark.parametrize('stat,role,group', [('passes_attempted', 'Defender', 'D'), ('clearances', 'Goalkeeper', 'G'),
                                             ('crosses', None, 'U'), ('attempted_dribbles', 'Forward', 'F')])
def test_thin2_fotmob_count_lines_get_a_population_prior_grade(stat, role, group):
    offer, facts = facts_with([30, 41], [['fotmob', stat]])
    offer.update(stat=stat, line=34.5, sides={'over': {}})
    if role:
        offer['role'] = role
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['thin_model']['version'] == 'thin-2' and f['thin_model']['group'] == group
    assert f['thin_model']['games'] == 2 and f['units'] == 0.0 and f['side'] == 'over' and f['grade']
    assert f['stake_tier'] == model.THIN2_TIERS[stat] and f['stake_reason'] == model.THIN_REASONS[f['stake_tier']]
    facts['games'] = []
    g, _ = model.make_forecast(offer, facts, NOW)                  # no FotMob game at all: the prior alone
    assert g['thin_model']['games'] == 0 and g['projection'] == pytest.approx(model.THIN2_PRIOR[stat][group][0], rel=1e-3)


def test_thin2_never_replaces_the_count_model_with_five_games():
    offer, facts = facts_with([30, 41, 35, 28, 33], [['fotmob', 'passes_attempted']])
    offer.update(stat='passes_attempted', line=34.5)
    f, _ = model.make_forecast(offer, facts, NOW)
    assert 'thin_model' not in f and f['distribution'] == 'overdispersed-count-predictive'


@pytest.mark.parametrize('stat,sources,line,role', [
    ('fantasy_outfield', [['fotmob', 'passes_attempted'], ['espn', 'goals']], 9.5, 'Midfielder'),
    ('fantasy_goalie', [['espn', 'saves'], ['catalog', 'conceded']], 9.0, 'Goalkeeper'),
    ('shots_assisted', [['fotmob', 'chances_created']], 0.5, 'Forward'),
    ('goals_assists', ESPN_GA, 0.5, 'Forward')])
def test_thin2_component_models_under_five_games(stat, sources, line, role):
    values = [7.0, 12.5] if stat.startswith('fantasy') else [1, 0]
    offer, facts = facts_with(values, sources)
    offer.update(stat=stat, line=line, role=role, sides={'over': {}})
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['component_model'] == stat and f['thin_model']['version'] == 'thin-2'
    assert f['thin_model']['games'] == 2 and f['units'] == 0.0 and f['grade'] and f['side'] == 'over'
    assert f['stake_reason'] == model.THIN_REASONS[stats.THIN_COMPONENT[stat]['tier']]
    assert 0 < f['p_over'] < 1 and abs(f['p_over'] + f['p_under'] + f['p_push'] - 1) < 1e-9


# --- first rosters: never-loaded teams of offered fixtures (board or league-tab rows), roster only, own allowance

def test_first_rosters_load_never_loaded_teams_by_kickoff_roster_only(tmp_path):
    import re
    import json as _json
    from sports import expansion_history as history
    from sports.tests.test_expansion_killswitch_1118 import _slate, _getter
    catalogs = _slate(tmp_path, [1, 2, 3, 4, 5, 6], [])
    now = datetime.now(timezone.utc)
    rows = catalogs['bra.1']
    rows['0']['start'] = (now + timedelta(hours=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows['4']['start'] = (now + timedelta(days=5)).strftime('%Y-%m-%dT%H:%M:%SZ')     # beyond FIRST_ROSTER_HOURS
    board = _json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))
    tabbed = [o for o in board['offers'] if o['team'] in ('Team1', 'Team2')]
    board['offers'] = [o for o in board['offers'] if o['team'] not in ('Team1', 'Team2')]   # tab not on the board yet
    for o in tabbed:
        o['start'] = rows['0']['start']
    history.atomic_json(tmp_path / 'board.json', board)
    history.atomic_json(tmp_path / 'pp-soccer-state.json', {'per': {'14': {'rows': tabbed}}})
    calls = []
    history.first_rosters(catalogs, _getter(calls), tmp_path)
    assert all(u.endswith('/roster') for u in calls)                      # roster only, never a schedule
    assert [re.search(r'/teams/(\d+)/', u)[1] for u in calls] == ['3', '4', '1', '2']   # by kickoff, tab rows count
    state = _json.loads((tmp_path / 'team-history-state.json').read_text(encoding='utf-8'))
    assert state['bra.1:soccer:team:espn:1']['checked_at'] and 'schedule_checked_at' not in state['bra.1:soccer:team:espn:1']
    calls.clear()
    history.first_rosters(catalogs, _getter(calls), tmp_path)
    assert calls == []                                                     # loaded once: the regular slots refresh it


def test_checked_book_player_label_resolves_exactly(tmp_path):
    from sports import expansion_history as history
    m = {'event_id': 'soccer:espn:mex.1:1:1', 'competition': 'mex.1', 'start': '2026-11-21T03:00:00Z',
         'participants': [{'id': 'soccer:team:espn:216', 'name': 'Atlas'}, {'id': 'soccer:team:espn:2', 'name': 'Juarez'}],
         'players': [{'id': 'soccer:player:espn:263224', 'name': 'Juanjo Purata', 'team_id': 'soccer:team:espn:216'},
                     {'id': 'soccer:player:espn:9', 'name': 'Juan Perez', 'team_id': 'soccer:team:espn:216'}]}
    o = {'sport': 'soccer', 'teams': ['Atlas', 'Juarez'], 'team': 'Atlas', 'player': 'Juan Purata',
         'start': '2026-11-21T03:00:00Z'}
    assert history.resolve(o, [m])['player_id'] == 'soccer:player:espn:263224'
    for name in ('Juan Purat', 'J. Purata', 'Purata', 'Juan Jose Purata'):           # exact labels only
        assert history.resolve(dict(o, player=name), [m]) is None
    assert history.resolve(dict(o, team='Juarez'), [m]) is None                       # the other team's roster: no


# --- tennis identity: checked labels, tour vs lower-level duplicate listing, PrizePicks set-line opponents

def _tennis_match(comp, eid, names, ids, start='2026-11-20T22:00:00Z'):
    return {'competition': comp, 'event_id': eid, 'start': start, 'status': 'scheduled',
            'participants': [{'id': i, 'name': n} for i, n in zip(ids, names)]}


def test_tennis_checked_labels_resolve_exactly():
    from sports import expansion_history as history
    m = _tennis_match('atp_challenger', 'e1', ['Felipe Meligeni', 'Matheus Almeida'],
                      ['atp:player:365scores:47450', 'atp:player:365scores:50126'], '2026-11-20T18:50:00Z')
    o0 = {'sport': 'tennis', 'platform': 'underdog', 'tour': 'atp', 'competition': 'ATP Challenger Montevideo',
          'player': 'Matheus Pucinelli de Almeida', 'opponent': 'Felipe Meligeni Alves', 'start': '2026-11-20T18:50:00Z'}
    assert history.resolve(o0, [m]) is None                         # without the source's long names: no match
    m['participants'][0]['long_name'] = 'Felipe Meligeni Alves'     # 365Scores longName (allscores, 2026-11-20)
    m['participants'][1]['long_name'] = 'Matheus Pucinelli De Almeida'
    o = {'sport': 'tennis', 'platform': 'underdog', 'tour': 'atp', 'competition': 'ATP Challenger Montevideo',
         'player': 'Matheus Pucinelli de Almeida', 'opponent': 'Felipe Meligeni Alves', 'start': '2026-11-20T18:50:00Z'}
    assert history.resolve(o, [m])['player_id'] == 'atp:player:365scores:50126'
    assert history.resolve(dict(o, player='Felipe Meligeni Alves', opponent='Matheus Pucinelli de Almeida'),
                           [m])['player_id'] == 'atp:player:365scores:47450'
    assert history.resolve(dict(o, opponent='Someone Else'), [m]) is None
    assert history.resolve(dict(o, player='Matheus Pucinelli'), [m]) is None


def test_hyphenated_source_name_matches_the_same_letters_without_hyphens():
    """close-1120 live e2e: 365Scores 'Soon-Woo Kwon' (Challenger Metz, 2026-11-21 14:00Z) is 'Soonwoo Kwon' on
    PrizePicks and Underdog - same letters, same order; anything else stays unmatched."""
    from sports import expansion_history as history
    m = _tennis_match('atp_challenger', 'e9', ['Soon-Woo Kwon', 'Sascha Gueymard Wayenburg'],
                      ['atp:player:365scores:1', 'atp:player:365scores:2'], '2026-11-21T14:00:00Z')
    ud = {'sport': 'tennis', 'platform': 'underdog', 'tour': 'atp', 'competition': 'ATP Challenger Metz',
          'player': 'Soonwoo Kwon', 'opponent': 'Sascha Gueymard Wayenburg', 'start': '2026-11-21T14:00:00Z'}
    assert history.resolve(ud, [m])['player_id'] == 'atp:player:365scores:1'
    assert history.resolve(dict(ud, player='Sascha Gueymard Wayenburg', opponent='Soonwoo Kwon'),
                           [m])['player_id'] == 'atp:player:365scores:2'
    pp = {'sport': 'tennis', 'platform': 'prizepicks', 'tour': None, 'player': 'Soonwoo Kwon',
          'opponent': 'Sascha Gueymard Wayenburg', 'start': '2026-11-21T14:00:00Z'}
    assert history.resolve(pp, [m])['player_id'] == 'atp:player:365scores:1'
    for wrong in ('Soonwo Kwon', 'Soon Kwon', 'Woo Kwon', 'S Kwon', 'Kwon'):
        assert history.resolve(dict(ud, player=wrong), [m]) is None


def test_prizepicks_tennis_pair_listed_by_tour_and_125_stores_takes_the_tour_store():
    from sports import expansion_history as history
    wta = _tennis_match('wta', 'wta:espn:1', ['Laura Samson', 'Rebeka Masarova'], ['wta:player:espn:1', 'wta:player:espn:2'])
    low = _tennis_match('wta125', 'wta:365:1', ['Rebeka Masarova', 'Laura Samson'], ['wta:player:365:2', 'wta:player:365:1'])
    o = {'sport': 'tennis', 'platform': 'prizepicks', 'tour': None, 'player': 'Laura Samson',
         'opponent': 'Rebeka Masarova', 'start': '2026-11-20T22:00:00Z'}
    assert history.resolve(o, [wta, low]) == {'competition': 'wta', 'event_id': 'wta:espn:1', 'player_id': 'wta:player:espn:1'}
    later = dict(low, start='2026-11-20T23:00:00Z')
    assert history.resolve(o, [wta, later]) is None                     # different starts: not provably one match


def test_prizepicks_set_line_opponent_from_the_exact_period_suffix():
    from sports import expansion_offers as offers
    from sports.tests.test_expansion_stats_1120 import pp_tennis, PNOW
    teams = ['Frances Tiafoe', 'Daniil Medvedev']
    row = offers.prizepicks(pp_tennis('1st Set Total Games Won', duration='1st Set', teams=teams,
                                      description='Daniil Medvedev 1st Set'), 'tennis', PNOW)[0]
    assert row['opponent'] == 'Daniil Medvedev'
    row = offers.prizepicks(pp_tennis('1st Set Total Games Won', duration='1st Set', teams=teams,
                                      description='Daniil Medvedev 2nd Set'), 'tennis', PNOW)[0]
    assert row['opponent'] is None                                   # not this line's period: still refused


def test_prizepicks_tennis_fantasy_score_projection_type_is_read():
    from sports import expansion_offers as offers
    from sports.tests.test_expansion_stats_1120 import pp_tennis, PNOW
    b = pp_tennis('Fantasy Score', allowed='over', teams=['Frances Tiafoe', 'Daniil Medvedev'],
                  description='Daniil Medvedev')
    b['data'][0]['attributes']['projection_type'] = 'Fantasy Score'
    rows = offers.prizepicks(b, 'tennis', PNOW)
    assert len(rows) == 1 and rows[0]['stat'] == 'fantasy_tennis' and rows[0]['opponent'] == 'Daniil Medvedev'
    b = pp_tennis('Aces', teams=['Frances Tiafoe', 'Daniil Medvedev'])
    b['data'][0]['attributes']['projection_type'] = 'Fantasy Score'          # any other stat: still refused
    assert offers.prizepicks(b, 'tennis', PNOW) == []


# --- thin-t2: tennis serve stats and the PrizePicks tennis fantasy score under 5 exact matches

@pytest.mark.parametrize('stat,line,comp,best_of,key', [('aces', 6.5, 'atp', 3, 'atp|3'), ('aces', 9.5, 'atp', 5, 'atp|5'),
                                                        ('double_faults', 2.5, 'wta', 3, 'wta|3'),
                                                        ('breakpoints_won', 3.5, 'atp_challenger', 3, 'atp|3')])
def test_thin_t2_tennis_serve_stats(stat, line, comp, best_of, key):
    offer, facts = fixture('tennis')
    offer.update(stat=stat, scope='full_game', line=line)
    facts.update(scope='full_game', context={'best_of': best_of})
    facts['identity']['competition'] = comp
    src = 'thescore' if comp in ('atp', 'wta') else '365scores'
    facts['games'] = [dict(g, source=src, best_of=best_of) for g in facts['games'][:2]]
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['thin_model']['version'] == 'thin-t2' and f['thin_model']['group'] == key
    assert f['units'] == 0.0 and f['grade'] and f['stake_reason'] == model.THIN_REASONS[model.THIN_T2_TIERS[stat]]
    facts['context'] = {'best_of': 5}
    facts['identity']['competition'] = 'wta'
    assert model.fit(offer, facts, NOW)[1] == 'more_exact_history_needed'          # no WTA best-of-5 prior


def test_thin_t2_tennis_fantasy_score():
    offer, facts = facts_with([22.0, 15.5], [['espn', 'games_won'], ['espn', 'sets_won'], ['thescore', 'aces'],
                                             ['thescore', 'double_faults']])
    offer.update(sport='tennis', stat='fantasy_tennis', scope='full_game', line=18.5, sides={'over': {}})
    facts['identity'].update(competition='atp', event_id='tennis:event:1', player_id='tennis:player:1')
    facts.update(scope='full_game', context={'best_of': 3})
    facts['event']['event_id'] = 'tennis:event:1'
    for g in facts['games']:
        g['best_of'] = 3
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['component_model'] == 'fantasy_tennis' and f['thin_model']['version'] == 'thin-t2'
    assert f['thin_model']['group'] == 'atp|3' and f['units'] == 0.0 and f['side'] == 'over' and f['grade']


def test_365scores_long_name_is_kept_only_when_it_differs():
    from sports import expansion_challenger as ch
    body = {'countries': [{'id': 301, 'name': 'Challenger'}], 'competitions': [{'id': 9, 'countryId': 301, 'sportId': 3,
            'name': 'Montevideo'}], 'games': [{'id': 1, 'competitionId': 9, 'sportId': 3, 'statusGroup': 2,
            'startTime': '2026-11-20T18:50:00+00:00', 'statusText': '',
            'homeCompetitor': {'id': 47450, 'type': 4, 'sportId': 3, 'name': 'Felipe Meligeni',
                               'longName': 'Felipe Meligeni Alves'},
            'awayCompetitor': {'id': 7, 'type': 4, 'sportId': 3, 'name': 'Liam Broady', 'longName': 'Liam Broady'}}]}
    parts = ch.parse_games(body, '2026-11-20T10:00:00Z')['atp_challenger'].matches[0]['participants']
    assert parts[0] == {'id': 'atp:player:365scores:47450', 'name': 'Felipe Meligeni', 'long_name': 'Felipe Meligeni Alves'}
    assert parts[1] == {'id': 'atp:player:365scores:7', 'name': 'Liam Broady'}


def test_short_team_label_links_by_the_players_own_full_team_name_only():
    """close-1120 live e2e (EPL tab, 2026-11-21 15:00Z): PrizePicks labels Dean Henderson's team 'Palace' while the game's
    sides are 'Crystal Palace' / 'Coventry'; the player's own team_name 'Crystal Palace' is exactly one side. Before: no
    own side -> the roster was never loaded and the line stayed blank. Rows whose label names a side are unchanged."""
    import json as _json
    from sports import expansion_offers as offers, expansion_history as history
    from sports.tests.test_pp_soccer_leagues_1119 import pp_tab, NOW, START
    body = pp_tab(14, 'EPL', [('14p', 'p9', 'Shots', 2.5, 'standard')], teams=('Crystal Palace', 'Coventry'),
                  full=(('Crystal Palace', 'Crystal Palace FC'), ('Coventry City', 'Coventry City FC')))
    plain = offers.prizepicks(_json.loads(_json.dumps(body)), 'soccer', NOW, 14)[0]
    assert 'team_names' not in plain                                   # label 'Crystal Palace' names a side: unchanged
    for x in body['included']:
        if x['type'] == 'new_player':
            x['attributes'].update(team='Palace', team_name='Crystal Palace', name='Dean Henderson')
    row = offers.prizepicks(_json.loads(_json.dumps(body)), 'soccer', NOW, 14)[0]
    assert row['team'] == 'Palace' and row['team_names'] == ['Crystal Palace']
    m = {'event_id': 'soccer:espn:eng.1:1:1', 'competition': 'eng.1', 'start': START, 'status': 'scheduled',
         'participants': [{'id': 'soccer:team:espn:388', 'name': 'Coventry City', 'abbreviation': 'COV', 'short_name': 'Coventry'},
                          {'id': 'soccer:team:espn:384', 'name': 'Crystal Palace', 'abbreviation': 'CRY', 'short_name': 'C Palace'}],
         'players': [{'id': 'soccer:player:espn:7', 'name': 'Dean Henderson', 'team_id': 'soccer:team:espn:384'},
                     {'id': 'soccer:player:espn:8', 'name': 'Dean Henderson', 'team_id': 'soccer:team:espn:388'}]}
    assert [p['id'] for p in history.soccer_teams(row, m)] == ['soccer:team:espn:384']      # the roster slate gets Palace
    assert history.resolve(row, [m])['player_id'] == 'soccer:player:espn:7'                  # own team only
    assert history.resolve({k: v for k, v in row.items() if k != 'team_names'}, [m]) is None  # before the fix
    for wrong in ('Palace', 'Crystal', 'Crystal Palace FC ', None):   # not exactly one side's name: never set
        for x in body['included']:
            if x['type'] == 'new_player':
                x['attributes']['team_name'] = wrong
        assert 'team_names' not in offers.prizepicks(_json.loads(_json.dumps(body)), 'soccer', NOW, 14)[0]
