"""sg-stats 2026-11-20: component models for the soccer/tennis stat types the count model does not cover."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import copy
import json

import pytest

from sports import expansion_forecasts as model, expansion_offers as offers, expansion_stats as stats, store
from sports.contracts import Event, StatObservation
from sports.expansion import isolated_database
from sports.tests.test_expansion_forecasts import fixture, NOW
from sports.tests.test_expansion_offers import pp, pp_tennis, NOW as PNOW


def facts_with(values, sources, scope='regulation', comp='eng.1'):
    offer, facts = fixture()
    facts['identity']['competition'] = comp
    facts['scope'] = scope
    facts['games'] = []
    for i, v in enumerate(values):
        stamp = (datetime(2026, 11, 16, tzinfo=timezone.utc) - timedelta(days=i)).isoformat()
        facts['games'].append({'event_id': f'soccer:old:{i}', 'date': stamp, 'known_at': stamp, 'value': v,
                               'source': sources[0][0], 'sources': stats.pack_sources(sources)})
    return offer, facts


# ---------------------------------------------------------------- parsers

@pytest.mark.parametrize('label,stat', [('Goal + Assist', 'goals_assists'), ('Outfield Fantasy Score', 'fantasy_outfield'),
                                        ('Goalie Fantasy Score', 'fantasy_goalie')])
def test_prizepicks_soccer_component_labels(label, stat):
    b = pp(); b['data'][0]['attributes']['stat_type'] = label
    assert offers.prizepicks(b, 'soccer', PNOW)[0]['stat'] == stat


def test_prizepicks_tennis_set_labels_need_their_own_period():
    row = offers.prizepicks(pp_tennis('1st Set Total Games Won', duration='1st Set'), 'tennis', PNOW)[0]
    assert (row['stat'], row['scope']) == ('games_won', 'set1')
    row = offers.prizepicks(pp_tennis('2nd Set Total Games', duration='2nd Set'), 'tennis', PNOW)[0]
    assert (row['stat'], row['scope']) == ('games_played', 'set2')
    assert offers.prizepicks(pp_tennis('2nd Set Total Games', duration='Full'), 'tennis', PNOW) == []
    assert offers.prizepicks(pp_tennis('1st Set Total Games Won', duration='Full'), 'tennis', PNOW) == []
    b = pp(); b['included'][1]['attributes']['name'] = '2nd Set'
    assert offers.prizepicks(b, 'soccer', PNOW) == []
    assert offers.prizepicks(pp_tennis('Matches Won'), 'tennis', PNOW) == []    # tournament-long: not one match


def test_existing_rows_keep_their_revision():
    """A label that was already mapped parses to the same row (and revision) as before."""
    assert offers.prizepicks(pp(), 'soccer', PNOW)[0]['revision'] == offers.prizepicks(pp(), 'soccer', PNOW)[0]['revision']
    b = pp(); row = offers.prizepicks(b, 'soccer', PNOW)[0]
    assert row['stat'] == 'shots' and row['scope'] == 'full_game' and 'component_model' not in row


# ---------------------------------------------------------------- routing / byte identity

def test_count_model_offers_take_the_unchanged_path():
    offer, facts = fixture()
    assert stats.model_of(offer) is None
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['units'] is None and f['stake_reason'] == 'Stakes are not assigned by this new model.'
    assert 'component_model' not in f and 'stake_tier' not in f


def test_count_model_golden_payload_is_byte_identical():
    """Pinned payload of a count-model forecast as computed at origin/main e6e155d: sg-stats must not move it."""
    offer, facts = fixture()
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['id'] == GOLDEN_ID
    assert model.encode({k: v for k, v in f.items() if k != 'inputs'}) == model.encode(GOLDEN)


# ---------------------------------------------------------------- component models

ESPN_GA = [['espn', 'assists'], ['espn', 'goals']]


def test_goal_plus_assist_is_a_count_on_the_summed_series():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA)
    offer.update(stat='goals_assists', line=.5)
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['component_model'] == 'goals_assists'
    assert f['distribution'] == 'overdispersed-count-predictive' and f['sample'] == 8
    assert f['units'] == 0.0 and f['stake_reason'] == stats.STAKE_REASONS[stats.STAKE_TIERS['goals_assists']]
    assert 0 < f['p_over'] < 1 and f['grade'] is not None


def test_component_outside_forecast_inputs_is_weight_zero():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], ESPN_GA, comp='aus.1')
    offer.update(stat='goals_assists', line=.5)
    assert model.fit(offer, facts, NOW)[1] in ('extra_time_scope_unverified', 'more_exact_history_needed')
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1], [['espn', 'goals'], ['espn', 'xg']])
    offer.update(stat='goals_assists', line=.5)
    f = model.fit(offer, facts, NOW)[0]       # every game weight 0: thin-2's population prior alone (n = 0)
    assert f['thin_model']['games'] == 0 and f['sample'] == 0 and f['inputs'] == []


def test_first_half_goals_thin_the_regulation_predictive():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1, 1, 0], [['espn', 'goals']])
    offer.update(stat='goals', scope='first_half', line=.5, sides={'over': {}})
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['distribution'] == 'regulation-count-thinned-first-half'
    support, mass = stats.prior_count_predictive([g['value'] for g in facts['games']], 'first_half_goals')
    regulation = sum(k * p for k, p in zip(support, mass))
    assert f['projection'] == pytest.approx(regulation * stats.FIRST_HALF_GOAL_SHARE)
    assert f['p_over'] < sum(p for k, p in zip(support, mass) if k > .5)
    assert f['stake_reason'] == stats.STAKE_REASONS['passed'] and f['units'] == 0.0


def test_population_prior_shrinks_rare_event_rates():
    """The tuned prior pulls an all-zero Goal + Assist history toward the population rate, not to a fixed half count."""
    zeros = [0] * 10
    s0, m0 = stats.count_predictive(zeros)
    s1, m1 = stats.prior_count_predictive(zeros, 'goals_assists')
    p_count, p_prior = sum(m0[1:]), sum(m1[1:])
    assert 0 < p_count and 0 < p_prior
    mean, k = stats.COUNT_PRIOR['goals_assists']
    assert sum(x * p for x, p in zip(s1, m1)) == pytest.approx(k * mean / (10 + k), rel=1e-6)


def test_first_half_goals_read_model_games_not_the_member_list():
    offer, facts = facts_with([1, 0, 2, 0, 1, 0, 0, 1, 1, 0], [['espn', 'goals']])
    offer.update(stat='goals', scope='first_half', line=.5, sides={'over': {}})
    facts['model_games'], facts['games'] = facts['games'], []
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['sample'] == 10


def test_sources_pack_round_trip():
    pairs = [['fotmob', 'minutes'], ['espn', 'goals'], ['espn', 'assists'], ['fotmob', 'crosses']]
    text = stats.pack_sources(pairs)
    assert text == 'espn:assists+goals;fotmob:crosses+minutes'
    assert sorted(stats.unpack_sources(text)) == sorted(pairs)
    assert stats.unpack_sources(None) == [] and stats.unpack_sources([['espn', 'goals']]) == []


def test_research_fit_drops_component_entries_before_any_count_entry():
    from sports import expansion_research as research
    count = {f'c{i}': {'games': [{'v': 'x' * 400}]} for i in range(5)}
    comp = {f'k{i}': {'games': [{'v': 'x' * 400}], 'component_model': 'fantasy_outfield'} for i in range(5)}
    starts = {**{k: '2026-11-21' for k in count}, **{k: '2026-11-20' for k in comp}}   # components start earlier
    one = len(json.dumps(count['c0'], separators=(',', ':'))) + 40
    kept = research.fit({**comp, **count}, starts, limit=one * 5 + 10)
    assert set(count) <= set(kept)
    # a document of count entries only keeps the exact e6e155d order (latest start dropped first)
    only = research.fit(count, {k: f'2026-11-2{i}' for i, k in enumerate(count)}, limit=one * 3 + 5)
    assert set(only) == {'c0', 'c1', 'c2'}


def test_cards_above_half_line_are_not_graded():
    offer, facts = facts_with([0, 1, 0, 0, 1, 0, 0, 0], [['espn', 'red_cards'], ['espn', 'yellow_cards']])
    offer.update(stat='cards', line=.5)
    assert model.fit(offer, facts, NOW)[0]['component_model'] == 'cards'
    offer['line'] = 1.5
    assert model.fit(offer, facts, NOW)[1] == 'cards_rule_unverified'


def test_fantasy_score_is_a_continuous_predictive():
    src = [['espn', 'goals'], ['fotmob', 'minutes'], ['fotmob', 'passes_attempted']]
    vals = [8.35, 12.1, 5.6, 22.4, 9.05, 7.5, 11.0, 6.2]
    offer, facts = facts_with(vals, src)
    offer.update(stat='fantasy_outfield', line=9.5)
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['distribution'] in ('score-kernel-mixture', 'score-student-t') and f['p_push'] == 0
    m0, lam = stats.SCORE_SHRINK['fantasy_outfield']
    assert f['projection'] == pytest.approx(sum(vals) / len(vals) + lam * (m0 - sum(vals) / len(vals)))
    assert f['p_over'] + f['p_under'] == pytest.approx(1)
    assert f['range80'][0] < f['projection'] < f['range80'][1]
    offer['line'] = 30.5
    assert model.fit(offer, facts, NOW)[0]['p_over'] < .1


def test_t_cdf_matches_known_values():
    assert stats._t_cdf(0, 5) == pytest.approx(.5)
    assert stats._t_cdf(2.015, 5) == pytest.approx(.95, abs=1e-3)
    assert stats._t_cdf(-1.833, 9) == pytest.approx(.05, abs=1e-3)


def test_tennis_fantasy_needs_the_match_format():
    offer, facts = fixture('tennis')
    offer.update(stat='fantasy_tennis', scope='full_game', line=20.5)
    facts['scope'] = 'full_game'
    assert model.fit(offer, facts, NOW)[1] == 'match_format_missing'


# ---------------------------------------------------------------- per-game series from the stores

def _soccer_store(tmp_path, rows_by_game):
    path = isolated_database(tmp_path, 'eng.1')
    pid = 'soccer:player:espn:9'
    rows = []
    for i, comps in enumerate(rows_by_game):
        start = f'2026-11-{i + 1:02}T12:00:00Z'
        known = f'2026-11-{i + 1:02}T18:00:00Z'
        eid = f'soccer:espn:eng.1:{i}:{i}'
        rows.append(Event(eid, 'soccer', 2026, 'unknown', None, None, start, 'espn', status='final',
                          observed_at=known, known_at=known))
        for (src, stat), v in comps.items():
            rows.append(StatObservation(eid, 'soccer', pid, 'player', 'regulation', 'no', stat, 'count', v, 'played',
                                        src, str(i), True, known, known))
    with closing(store.connect(path)) as c:
        res = store.put_many(c, rows)
        assert not res["invalid"], c.execute("SELECT reason FROM quarantine LIMIT 1").fetchone()[0]
    return path, pid


def test_fotmob_absent_field_is_zero_only_when_fotmob_reported_the_match(tmp_path):
    base = {('espn', s): 0 for s in ('goals', 'assists', 'shots', 'shots_on_target', 'yellow_cards', 'red_cards', 'fouls')}
    game0 = {**base, ('espn', 'shots'): 2, ('fotmob', 'minutes'): 90, ('fotmob', 'passes_attempted'): 40,
             ('fotmob', 'clearances'): 1, ('fotmob', 'tackles'): 2, ('fotmob', 'chances_created'): 1}
    game1 = dict(base)                                    # FotMob never reported this match: not used
    path, pid = _soccer_store(tmp_path, [game0, game1])
    offer = {'sport': 'soccer', 'stat': 'fantasy_outfield', 'scope': 'full_game'}
    with closing(store.connect(path, read_only=True)) as con:
        games = stats.games(con, store, offer, pid, '2026-11-30T00:00:00Z')
    assert len(games) == 1
    # 2 shots + 40*0.05 passes + 1 clearance + 2 tackles + 0.5 shot assisted; crosses / dribbles absent = 0
    assert games[0]['value'] == pytest.approx(2 + 2 + 1 + 2 + .5)
    assert ['fotmob', 'minutes'] in stats.unpack_sources(games[0]['sources'])
    with closing(store.connect(path, read_only=True)) as con:
        ga = stats.games(con, store, dict(offer, stat='goals_assists'), pid, '2026-11-30T00:00:00Z')
    assert len(ga) == 2 and all(g['value'] == 0 for g in ga)


def test_goalie_score_uses_saves_and_the_catalog_score(tmp_path):
    path, pid = _soccer_store(tmp_path, [{('espn', 'saves'): 3}, {('espn', 'saves'): 1}])
    catalog = {'soccer:espn:eng.1:0:0': {'players': [{'id': pid, 'team_id': 't1'}], 'scores': {'t1': 2, 't2': 0}},
               'soccer:espn:eng.1:1:1': {'players': [{'id': pid, 'team_id': 't1'}], 'scores': {'t1': 1, 't2': 2}}}
    offer = {'sport': 'soccer', 'stat': 'fantasy_goalie', 'scope': 'full_game'}
    with closing(store.connect(path, read_only=True)) as con:
        games = stats.games(con, store, offer, pid, '2026-11-30T00:00:00Z', catalog)
    assert sorted(g['value'] for g in games) == [5 + 2 - 4, 5 + 6 + 5]


def test_component_settlement_never_turns_missing_into_zero():
    class O:
        def __init__(self, v, stat):
            self.value, self.final, self.participation, self.stat = v, True, 'played', stat
    f = {'component_model': 'goals_assists', 'identity': {'player_id': 'p'}, 'history_scope': 'regulation'}
    by_key = {('p', 'goals', 'regulation'): [({'record_id': 'a'}, O(1, 'goals'))]}
    assert stats.actual(f, by_key) == (None, [])
    by_key[('p', 'assists', 'regulation')] = [({'record_id': 'b'}, O(1, 'assists'))]
    value, used = stats.actual(f, by_key)
    assert value == 2 and len(used) == 2


def test_publish_runs_count_model_offers_first(tmp_path, monkeypatch):
    from sports import expansion_board as boards, expansion_research as research
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    offer, facts = fixture()
    late = copy.deepcopy(offer); late.update(offer_id='o2', revision='r2', stat='goals_assists', line=.5,
                                             start='2026-11-18T01:00:00Z')
    lf = copy.deepcopy(facts)
    for g in lf['games']:
        g['sources'] = stats.pack_sources(ESPN_GA)
    seen = []
    real = model.make_forecast
    monkeypatch.setattr(model, 'make_forecast', lambda o, fa, now: (seen.append(o['offer_id']), real(o, fa, now))[1])
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': [late, offer]}))
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(offer): facts, research.key(late): lf}})
    out = model.publish('soccer', NOW)
    assert seen == ['o1', 'o2'] and out['published'] == 2


# Computed by running this fixture against origin/main e6e155d (stats/scripts/golden.py).
GOLDEN_ID = 'ed69ca7b58557eff00c2c8af692e18deb07ba36a22597530b385ab8f9a49c89c'
GOLDEN = {'model_version': 'expansion-count-predictive-v1.1', 'distribution': 'overdispersed-count-predictive', 'sample': 10, 'projection': 3.3499999993692033, 'range80': [1, 6], 'p_over': 0.6378598280537311, 'p_under': 0.3621401719462689, 'p_push': 0, 'history_scope': 'regulation', 'context_used': {}, 'calibrated': False, 'identity': {'event_id': 'soccer:event:1', 'player_id': 'soccer:player:1', 'competition': 'eng.1'}, 'sport': 'soccer', 'stat': 'shots', 'scope': 'full_game', 'line': 2.5, 'platform': 'prizepicks', 'offer_id': 'o1', 'offer_revision': 'r1', 'start': '2026-11-18T18:00:00Z', 'made_at': '2026-11-17T23:00:00Z', 'side': 'over', 'p_side': 0.6378598280537311, 'grade': 'A-', 'grade_policy': 'gp-3', 'grade_reason': [], 'units': None, 'stake_reason': 'Stakes are not assigned by this new model.', 'probability_basis': 'Conditional on a completed, non-push result; model estimate, not calibrated.', 'id': 'ed69ca7b58557eff00c2c8af692e18deb07ba36a22597530b385ab8f9a49c89c'}
