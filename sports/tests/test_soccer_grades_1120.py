"""soccer-grades-1120: integration of sg-stability + sg-scope + sg-stats, and every offered prop gets a letter.

- A line the policy grades keeps its exact payload (no new key, same id).
- A line the policy cannot letter (payout of a demon / goblin line not published, or the model's side not offered)
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


def test_model_side_not_offered_keeps_direction_and_gets_letter():
    offer, facts = fixture()
    offer.update(line=8.5, sides={'over': {}})
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['side'] == 'under' and f['p_side'] > .5
    assert f['grade'] == policy.letter_for(f['p_side']) and f['units'] == 0.0
    assert f['letter_basis'] == 'model_side_not_offered' and 'Under' in f['stake_reason']


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


def test_member_card_accepts_the_model_side_letter():
    import workspace_expansion as wx
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    made = (now - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
    o = {'sport': 'soccer', 'platform': 'underdog', 'offer_id': 'x', 'revision': 'r', 'stat': 'shots',
         'scope': 'full_game', 'line': 1.5, 'start': start, 'sides': {'over': {}}}
    ident = {'event_id': 'e', 'player_id': 'p', 'competition': 'eng.1'}
    key = __import__('hashlib').sha256(__import__('json').dumps([o[k] for k in ('sport', 'platform', 'offer_id',
        'revision')], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    f = {'identity': ident, **{k: o[k] for k in ('sport', 'stat', 'scope', 'line', 'platform', 'offer_id', 'start')},
         'offer_revision': 'r', 'side': 'under', 'grade': 'B', 'made_at': made, 'p_over': .4, 'p_under': .6,
         'p_push': 0.0, 'p_side': .6, 'projection': 1.0, 'letter_basis': 'model_side_not_offered'}
    assert wx._forecast(o, {'identity': ident}, {key: f}) is f
    g = dict(f, letter_basis=None)
    assert wx._forecast(o, {'identity': ident}, {key: g}) is None     # any other letter on an unoffered side: refused


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
    assert model.fit(offer, facts, NOW)[1] == 'more_exact_history_needed'
    offer, facts = fixture('tennis')
    facts['games'] = facts['games'][:2]
    assert model.fit(offer, facts, NOW)[1] == 'more_exact_history_needed'


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
