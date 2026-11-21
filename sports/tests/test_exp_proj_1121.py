"""exp-proj-1121: fm-zero-1 (FotMob omitted crosses / dribbles = 0), sa-inputs-1 (Shots Assisted inputs in the four
promoted competitions), tt-cap-1 (tennis failed thin letters capped at Lean), tp-shrink-1 (tennis count pmf shrunk
to the tour population). Flags off = byte-identical payloads and ids."""
from contextlib import closing
import json

import pytest
from gradecore import policy

from sports import expansion_forecasts as model, expansion_history as history, expansion_stats as stats, store
from sports import expansion_thin_tennis as tt
from sports.contracts import Event, StatObservation
from sports.expansion import isolated_database
from sports.tests.test_expansion_forecasts import fixture, NOW
from sports.tests.test_expansion_stats_1120 import facts_with


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for k in ('SOCCER_FM_ZERO_FILL', 'SOCCER_SA_INPUTS', 'TENNIS_THIN_LEAN_CAP', 'TENNIS_POP_SHRINK'):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- fm-zero-1

def _m(eid, date, value=90.0, source='fotmob'):
    return {'event_id': eid, 'date': date, 'value': value, 'source': source, 'known_at': date}


def test_zero_fill_adds_a_zero_for_every_fotmob_minutes_game_without_the_stat():
    games = [_m('e3', '2026-11-03T00:00:00Z', 4.0), _m('e1', '2026-11-01T00:00:00Z', 2.0)]
    minutes = [_m('e4', '2026-11-04T00:00:00Z'), _m('e3', '2026-11-03T00:00:00Z'), _m('e2', '2026-11-02T00:00:00Z', 0),
               _m('e1', '2026-11-01T00:00:00Z'), _m('e0', '2026-10-31T00:00:00Z', 90.0, 'espn')]
    out = history.zero_fill(games, minutes)
    assert [g['event_id'] for g in out] == ['e4', 'e3', 'e1']          # 0 minutes / another source: never a 0 game
    assert out[0]['value'] == 0.0 and out[0]['zero_filled'] and out[0]['source'] == 'fotmob'
    assert history.zero_fill(games, []) is games
    many = [_m(f'z{i}', f'2026-10-{i + 1:02}T00:00:00Z') for i in range(25)]
    assert len(history.zero_fill(games, many)) == 20


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    path = isolated_database(tmp_path, 'eng.1')
    rows = []
    for i in range(6):
        start, known = f'2026-11-0{i + 1}T15:00:00Z', f'2026-11-0{i + 1}T20:00:00Z'
        eid = f'soccer:espn:eng.1:{i}'
        rows.append(Event(eid, 'soccer', 2026, 'unknown', None, None, start, 'espn', status='final',
                          observed_at=known, known_at=known))
        rows.append(StatObservation(eid, 'soccer', 'pl', 'player', 'regulation', 'no', 'minutes', 'minutes', 90.0,
                                    'played', 'fotmob', str(i), True, known, known))
        if i % 2 == 0:
            rows.append(StatObservation(eid, 'soccer', 'pl', 'player', 'regulation', 'no', 'crosses', 'count', 3.0,
                                        'played', 'fotmob', str(i), True, known, known))
    with closing(store.connect(path)) as c:
        assert not store.put_many(c, rows).get('quarantined', 0)
    return path


def test_history_games_reads_the_minutes_rows_only_with_the_flag(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    args = (str(path), history._file_signature(path), 'pl', 'crosses', 'regulation', '2026-11-10T00:00:00Z')
    assert [g['value'] for g in history.history_games(*args)] == [3.0, 3.0, 3.0]
    monkeypatch.setenv('SOCCER_FM_ZERO_FILL', '1')
    assert [g['value'] for g in history.history_games(*args)] == [0.0, 3.0, 0.0, 3.0, 0.0, 3.0]
    other = (str(path), history._file_signature(path), 'pl', 'tackles', 'regulation', '2026-11-10T00:00:00Z')
    assert history.history_games(*other) == []                        # only FM_ZERO_STATS are filled


def test_zero_filled_games_feed_the_count_model_as_fotmob_inputs():
    offer, facts = fixture()
    offer.update(stat='attempted_dribbles', line=1.5)
    for g in facts['games']:
        g['source'] = 'fotmob'
    nonzero, _ = model.make_forecast(offer, facts, NOW)
    minutes = [{**g, 'value': 90.0} for g in facts['games']] + [
        {'event_id': f'soccer:zero:{i}', 'date': f'2026-11-0{i + 1}T00:00:00+00:00',
         'known_at': f'2026-11-0{i + 1}T00:00:00+00:00', 'value': 90.0, 'source': 'fotmob'} for i in range(6)]
    facts['games'] = history.zero_fill(sorted(facts['games'], key=lambda g: g['date'], reverse=True), minutes)
    filled, _ = model.make_forecast(offer, facts, NOW)
    assert filled['sample'] == 16 and filled['projection'] < nonzero['projection']
    assert filled['p_under'] > nonzero['p_under']


def test_settle_end_to_end_zero_review(tmp_path, monkeypatch):
    """settle() writes a 0 review for an omitted crosses field only with the flag (forecasts.db + a stored FotMob
    minutes row of the event)."""
    from sports import expansion_board as boards
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    path = isolated_database(tmp_path, 'eng.1')
    eid, known = 'soccer:espn:eng.1:target', '2026-11-18T20:00:00Z'
    with closing(store.connect(path)) as c:
        store.put_many(c, [Event(eid, 'soccer', 2026, 'unknown', None, None, '2026-11-18T15:00:00Z', 'espn',
                                 status='final', observed_at=known, known_at=known),
                           StatObservation(eid, 'soccer', 'pl', 'player', 'regulation', 'no', 'minutes', 'minutes',
                                           88.0, 'played', 'fotmob', '9', True, known, known)])
    f = {'id': 'f1', 'stat': 'crosses', 'history_scope': 'regulation', 'line': 0.5, 'side': 'over', 'projection': 1.2,
         'identity': {'player_id': 'pl', 'competition': 'eng.1', 'event_id': eid}}
    with closing(model.connect('soccer')) as c:
        c.execute('INSERT INTO forecasts VALUES (?,?,?,?,?,?)', ('f1', 'k', 'eng.1', eid, '2026-11-18T15:00:00Z',
                                                                json.dumps(f)))
        c.commit()
    assert model.settle('soccer', now='2026-11-19T00:00:00Z')['added'] == 0
    with closing(model.connect('soccer')) as c:
        c.execute('DELETE FROM checked')
        c.commit()
    monkeypatch.setenv('SOCCER_FM_ZERO_FILL', '1')
    assert model.settle('soccer', now='2026-11-19T00:00:01Z')['added'] == 1
    with closing(model.connect('soccer')) as c:
        r = json.loads(c.execute('SELECT payload FROM reviews').fetchone()[0])
    assert r['actual'] == 0.0 and r['outcome'] == 'under' and r['won'] is False
    assert boards.root('soccer').exists()


# ---------------------------------------------------------------- sa-inputs-1

@pytest.mark.parametrize('comp', ['usa.1', 'mex.1', 'aus.1', 'uefa.champions'])
def test_shots_assisted_reads_its_history_in_the_promoted_competitions_only_with_the_flag(comp, monkeypatch):
    values = [3, 0, 2, 4, 1, 3, 2]
    offer, facts = facts_with(values, [['fotmob', 'chances_created'], ['fotmob', 'minutes']], comp=comp)
    offer.update(stat='shots_assisted', line=1.5)
    facts['scope_rule'] = {'extra_time': 'none'}
    off, _ = model.make_forecast(offer, facts, NOW)
    assert off['thin_model']['version'] == 'thin-2' and off['sample'] == 0       # constant population prior
    monkeypatch.setenv('SOCCER_SA_INPUTS', '1')
    on, _ = model.make_forecast(offer, facts, NOW)
    assert 'thin_model' not in on and on['sample'] == 7 and on['component_model'] == 'shots_assisted'
    assert on['projection'] > off['projection'] and on['units'] == 0.0
    assert on['stake_reason'] == stats.STAKE_REASONS[stats.STAKE_TIERS['shots_assisted']]


def test_sa_flag_leaves_other_competitions_and_stats_byte_identical(monkeypatch):
    offer, facts = facts_with([3, 0, 2, 4, 1, 3], [['fotmob', 'chances_created'], ['fotmob', 'minutes']])  # eng.1
    offer.update(stat='shots_assisted', line=1.5)
    a, _ = model.make_forecast(offer, facts, NOW)
    monkeypatch.setenv('SOCCER_SA_INPUTS', '1')
    b, _ = model.make_forecast(offer, facts, NOW)
    assert a == b
    o2, f2 = fixture()                                  # an ordinary count line: same payload and id either way
    monkeypatch.delenv('SOCCER_SA_INPUTS')
    c, _ = model.make_forecast(o2, f2, NOW)
    monkeypatch.setenv('SOCCER_SA_INPUTS', '1')
    assert model.make_forecast(o2, f2, NOW)[0] == c


# ---------------------------------------------------------------- tt-cap-1

def _thin_tennis():
    offer, facts = fixture('tennis')                     # atp_challenger, set-1 games won 4.5
    facts['games'] = facts['games'][:3]
    for g in facts['games']:
        g['value'] = 6                                  # strong recent set-1 wins -> a high model chance
    offer['line'] = 3.5
    return offer, facts


def test_tennis_thin_letters_capped_at_lean_by_default(monkeypatch):
    offer, facts = _thin_tennis()
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['thin_model']['version'] == tt.VERSION and f['grade'] == policy.LEAN
    assert f['lean_cap']['version'] == model.TENNIS_CAP_VERSION and f['lean_cap']['model_letter'] not in (None, policy.LEAN)
    assert f['units'] == 0.0 and f['side'] and 0.5 < f['p_side'] < 1
    monkeypatch.setenv('TENNIS_THIN_LEAN_CAP', '0')
    g, _ = model.make_forecast(offer, facts, NOW)
    assert g['grade'] == f['lean_cap']['model_letter'] and 'lean_cap' not in g
    assert g['p_side'] == f['p_side'] and g['side'] == f['side']


def test_tennis_count_model_and_soccer_are_not_capped():
    offer, facts = fixture('tennis')
    f, _ = model.make_forecast(offer, facts, NOW)
    assert 'lean_cap' not in f and 'thin_model' not in f
    offer, facts = fixture()
    facts['games'] = facts['games'][:3]
    f, _ = model.make_forecast(offer, facts, NOW)
    assert 'lean_cap' not in f or f['lean_cap']['version'] == model.LEAN_CAP_VERSION


def test_underdog_line_offering_only_the_other_side_still_gets_a_letter():
    """audit_1121: 3-5 sided Nov 18-19 rows had no letter (model side not offered). Since decision 1 the graded side is
    the offered one, and it always carries a letter (Lean at worst)."""
    offer, facts = fixture('tennis')
    offer.update(platform='underdog', line=4.5, sides={'under': {'multiplier': 2.4}})
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['side'] == 'under' and f['grade'] in ('A+', 'A', 'A-', 'B+', 'B', policy.LEAN)


# ---------------------------------------------------------------- tp-shrink-1

def _tennis_count(stat='tie_breakers_played', values=(1, 1, 2, 1, 0, 1), scope='full_game', comp='atp'):
    offer, facts = fixture('tennis')
    offer.update(stat=stat, scope=scope, line=0.5)
    facts.update(scope=scope, context={'best_of': 3})
    facts['identity']['competition'] = comp
    facts['games'] = facts['games'][:len(values)]
    for g, v in zip(facts['games'], values):
        g.update(value=v, best_of=3, source='espn')
    return offer, facts


def test_pop_shrink_is_off_by_default_and_byte_identical():
    offer, facts = _tennis_count()
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['distribution'] == 'finite-score-dirichlet' and 'pop_shrink' not in f


def test_pop_shrink_pulls_the_minority_score_toward_the_tour(monkeypatch):
    offer, facts = _tennis_count()                        # 5 of 6 past matches with a tie-break: Over 0.5
    base, _ = model.make_forecast(offer, facts, NOW)
    monkeypatch.setenv('TENNIS_POP_SHRINK', '1')
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['distribution'] == 'finite-score-population-shrink' and f['pop_shrink'] == {'version': 'tp-shrink-1', 'k': 40}
    assert f['p_over'] < base['p_over'] and f['side'] == 'under'          # men's tour: ~33% of matches have one
    assert sum(f['p_' + s] for s in ('over', 'under', 'push')) == pytest.approx(1)
    assert f['grade'] and f['sample'] == 6


def test_pop_shrink_needs_a_tested_pmf(monkeypatch):
    monkeypatch.setenv('TENNIS_POP_SHRINK', '1')
    offer, facts = _tennis_count()
    facts['context'] = {'best_of': 5}
    for g in facts['games']:
        g['best_of'] = 5
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['distribution'] == 'finite-score-dirichlet'              # best of 5: no pmf, unchanged
    offer, facts = fixture('tennis')                                  # set-1 games won, no format on the board
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['distribution'] == 'finite-score-population-shrink'      # challenger: men's tour pmf
    for key, pop in tt.SHRINK_POP.items():
        assert abs(sum(pop) - 1) < 1e-3 and min(pop) > 0, key


# ---------------------------------------------------------------- ga-prior-1

def test_ga_prior_off_by_default_and_shrinks_a_hot_scorer_with_the_flag(monkeypatch):
    offer, facts = fixture()
    offer.update(stat='goals', line=0.5, variant='demon', sides={'over': {}}, role='Forward')
    for g, v in zip(facts['games'], [1, 1, 0, 2, 1, 0, 1, 1, 0, 1]):
        g['value'] = v
    base, _ = model.make_forecast(offer, facts, NOW)
    assert 'ga_prior' not in base
    monkeypatch.setenv('SOCCER_GA_PRIOR', '1')
    f, _ = model.make_forecast(offer, facts, NOW)
    assert f['ga_prior'] == {'version': 'ga-prior-1', 'group': 'F', 'm0': model.THIN_PRIOR['goals']['F'][0],
                             'k': model.THIN_K['goals']}
    assert f['projection'] < base['projection'] and f['side'] == 'over' and f['grade']
    offer['stat'] = 'shots'                                  # other stats untouched
    assert 'ga_prior' not in model.make_forecast(offer, facts, NOW)[0]
