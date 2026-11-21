"""doubles-1 (soccer-grades-1120 fair): tennis doubles fixtures, exact pair matching, population model at 0U."""
import json

import pytest

from sports import expansion_doubles as doubles, expansion_forecasts as model

NOW = '2026-11-21T05:00:00Z'


def _side(ids, names, games=None, order=1):
    s = {'id': '-'.join(ids), 'type': 'team', 'order': order,
         'roster': {'displayName': ' / '.join(names), 'athletes': [{'displayName': n} for n in names]}}
    if games is not None:
        s['linescores'] = [{'value': g} for g in games]
    return s


def _comp(cid, start, a, b, state='pre', name='STATUS_SCHEDULED', completed=False, slug='mens-doubles'):
    return {'id': cid, 'date': start, 'startDate': start, 'type': {'slug': slug},
            'status': {'type': {'name': name, 'state': state, 'completed': completed}}, 'competitors': [a, b]}


def _body(*comps, slug='mens-doubles', major=False):
    return {'events': [{'id': '850', 'name': 'Nitto ATP Finals', 'major': major,
                        'groupings': [{'grouping': {'slug': slug}, 'competitions': list(comps)},
                                      {'grouping': {'slug': 'mens-singles'}, 'competitions': [
                                          {'id': '9', 'competitors': [{'type': 'athlete', 'id': '1'}]}]}]}]}


HS = (['2293', '2699'], ['Christian Harrison', 'Neal Skupski'])
AP = (['1343', '1790'], ['Marcelo Arevalo', 'Mate Pavic'])


def _fixtures(*extra):
    body = _body(_comp('187530', '2026-11-21T11:00Z', _side(*HS, order=2), _side(*AP)),
                 _comp('187517', '2026-11-15T10:40Z', _side(['2725', '2434'], ['Orlando Luz', 'Rafael Matos'], [3, 7, 0]),
                       _side(['3540', '10685'], ['Harri Heliovaara', 'Henry Patten'], [6, 5, 1]), 'post', 'STATUS_FINAL',
                       True),
                 _comp('187999', '2026-11-22T05:00Z', _side(['-1', '-2'], ['TBD', 'TBD']), _side(*AP)), *extra)
    return {r['event_id']: r for r in doubles.parse(body, 'atp', NOW)}


def test_parse_keeps_doubles_only_with_both_players_and_counts_a_match_tiebreak_as_one_game():
    fx = _fixtures()
    assert sorted(fx) == ['atp:espn:atp_doubles:850:187517', 'atp:espn:atp_doubles:850:187530']   # TBD + singles: no
    up = fx['atp:espn:atp_doubles:850:187530']
    assert up['status'] == 'scheduled' and up['start'] == '2026-11-21T11:00:00Z'
    assert up['sides'][0]['id'] == 'atp:pair:espn:2293+2699' and len(up['sides'][0]['players']) == 2
    fin = fx['atp:espn:atp_doubles:850:187517']
    assert fin['status'] == 'final' and fin['games_played'] == 3 + 6 + 7 + 5 + 1 and fin['sets_played'] == 3
    assert doubles._games([7, 6], [6, 4], True) == (23, 2)
    assert doubles._games([6, 3, 10], [4, 6, 8], False) == (20, 3)                    # tie-break points: one game
    assert doubles.parse(_body(_comp('1', '2026-11-21T11:00Z', _side(*HS), _side(*AP), slug='mixed-doubles'),
                               slug='mixed-doubles'), 'atp', NOW) == []


def test_absorb_never_turns_a_final_back_and_load_reads_it(tmp_path):
    fx = list(_fixtures().values())
    doubles.absorb(tmp_path, fx, now=doubles.parse_utc(NOW))
    later = [dict(r, status='scheduled') for r in fx]
    doubles.absorb(tmp_path, later, now=doubles.parse_utc(NOW))
    got = doubles.load(tmp_path)
    assert got['atp:espn:atp_doubles:850:187517']['status'] == 'final'
    assert got['atp:espn:atp_doubles:850:187530']['status'] == 'scheduled'


def _offer(player='Harrison C / Skupski N', other='Arevalo M / Pavic M', stat='games_played', line=22.0):
    return {'sport': 'tennis', 'platform': 'prizepicks', 'offer_id': '1', 'revision': 'r', 'player': player,
            'teams': [player, other], 'stat': stat, 'scope': 'full_game', 'line': line, 'variant': 'standard',
            'start': '2026-11-21T11:00:00Z', 'sides': {'over': {}, 'under': {}}}


def test_book_pair_resolves_only_when_all_four_names_identify_one_fixture():
    fx = _fixtures()
    want = {'competition': 'atp', 'event_id': 'atp:espn:atp_doubles:850:187530', 'player_id': 'atp:pair:espn:2293+2699'}
    assert doubles.resolve(_offer(), fx) == want
    assert doubles.resolve(_offer('Arevalo M / Pavic M', 'Harrison C / Skupski N'), fx)['player_id'] == \
        'atp:pair:espn:1343+1790'
    assert doubles.resolve(_offer('C. Harrison / N. Skupski', 'M. Arevalo / M. Pavic'), fx) == want
    assert doubles.resolve(_offer('Christian Harrison / Neal Skupski', 'Marcelo Arevalo / Mate Pavic'), fx) == want
    assert doubles.resolve(_offer('Harrison X / Skupski N'), fx) is None                  # wrong initial
    assert doubles.resolve(_offer('Harrison C / Pavic M'), fx) is None                    # names on two sides
    assert doubles.resolve(_offer(other='Arevalo M / Zeballos H'), fx) is None            # opponent pair must match
    assert doubles.resolve(_offer(other=''), fx) is None
    assert doubles.resolve(dict(_offer(), start='2026-11-21T15:00:00Z'), fx) is None       # start > 2 h away
    assert doubles.resolve(dict(_offer(), sport='soccer'), fx) is None


def test_same_surname_or_same_initial_and_surname_on_both_sides_refuses():
    brothers = _fixtures(_comp('187600', '2026-11-21T11:30Z',
                               _side(['1', '2'], ['Neal Skupski', 'Joe Salisbury']),
                               _side(['3', '4'], ['Ken Skupski', 'Jamie Murray'])))
    # 'Skupski N' is unique (Neal); a 'Skupski K' line resolves to the other pair of THAT fixture only
    o = _offer('Skupski N / Salisbury J', 'Skupski K / Murray J')
    o['start'] = '2026-11-21T11:30:00Z'
    assert doubles.resolve(o, brothers)['player_id'] == 'atp:pair:espn:1+2'
    twins = _fixtures(_comp('187601', '2026-11-21T11:30Z',
                            _side(['5', '6'], ['Jamie Murray', 'John Peers']),
                            _side(['7', '8'], ['Jack Murray', 'Joe Salisbury'])))
    o = _offer('Murray J / Peers J', 'Murray J / Salisbury J')                          # 'Murray J' = 2 of the 4
    o['start'] = '2026-11-21T11:30:00Z'
    assert doubles.resolve(o, twins) is None
    same = _fixtures(_comp('187531', '2026-11-21T11:10Z', _side(['2293', '2699'], ['Christian Harrison', 'Neal Skupski']),
                           _side(['1343', '1790'], ['Marcelo Arevalo', 'Mate Pavic'])))
    assert doubles.resolve(_offer(), same) is None                                       # two fixtures: never guess


def _facts(fx, offer):
    return doubles.facts(offer, fx, NOW)


def test_doubles_forecast_letters_at_0u_with_its_tier_reason():
    fx = _fixtures()
    offer = _offer()
    f, reason = model.make_forecast(offer, _facts(fx, offer), NOW)
    assert reason is None and f['distribution'] == 'doubles-population-prior' and f['units'] == 0.0
    assert f['grade'] and f['doubles_model']['group'] == 'atp|0' and f['identity']['player_id'] == 'atp:pair:espn:2293+2699'
    assert doubles.DOUBLES_TIERS['atp|0']['games_played'] == 'failed'                 # failed test: capped at Lean
    assert f['grade'] == 'Lean' and f['lean_cap']['model_letter'] not in (None, 'Lean')
    assert f['stake_reason'] == doubles.REASONS[doubles.DOUBLES_TIERS['atp|0']['games_played']]
    assert abs(f['p_over'] + f['p_under'] + f['p_push'] - 1) < 1e-9 and f['p_push'] > 0      # 22.0 can push
    s, _ = model.make_forecast(_offer(stat='sets_played', line=2.5), _facts(fx, _offer(stat='sets_played', line=2.5)), NOW)
    assert s['stake_reason'] == doubles.REASONS['passed'] and s['p_push'] == 0 and 'lean_cap' not in s
    for stat in ('fantasy_tennis', 'breakpoints_won'):
        o = _offer(stat=stat, line=13.5)
        assert model.make_forecast(o, _facts(fx, o), NOW) == (None, 'doubles_stat_not_modelled')
    o = _offer('Nobody N / Pavic M')
    assert model.make_forecast(o, _facts(fx, o), NOW)[1] == 'identity_or_start'


def test_doubles_priors_are_distributions():
    for k, pmf in doubles.DOUBLES_PRIOR.items():
        assert abs(sum(pmf.values()) - 1) < 1e-3 and all(v > 0 for v in pmf.values()), k


def test_settlement_value_from_the_stored_final(tmp_path):
    doubles.absorb(tmp_path, list(_fixtures().values()), now=doubles.parse_utc(NOW))
    f = {'identity': {'event_id': 'atp:espn:atp_doubles:850:187517'}, 'stat': 'games_played'}
    assert doubles.actual(f, tmp_path) == (22, ['atp:espn:atp_doubles:850:187517'])
    assert doubles.actual(dict(f, identity={'event_id': 'atp:espn:atp_doubles:850:187530'}), tmp_path) == (None, [])


def test_settle_reviews_a_doubles_forecast_from_the_stored_final(tmp_path, monkeypatch):
    from contextlib import closing
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    root = tmp_path / 'tennis'
    root.mkdir()
    fx = _fixtures()
    offer = dict(_offer('Luz O / Matos R', 'Heliovaara H / Patten H'), start='2026-11-15T10:40:00Z')
    entry = doubles.facts(offer, fx, '2026-11-15T09:00:00Z')
    entry['event']['status'] = 'scheduled'                                           # as it was before the match
    f, reason = model.make_forecast(offer, entry, '2026-11-15T09:00:00Z')
    assert reason is None and f['identity']['event_id'] == 'atp:espn:atp_doubles:850:187517'
    with closing(model.connect('tennis')) as c:
        c.execute('INSERT INTO forecasts VALUES (?,?,?,?,?,?)', (f['id'], model.offer_key(offer), 'atp',
                  f['identity']['event_id'], offer['start'], model.encode(f)))
        c.commit()
    assert model.settle('tennis', now='2026-11-16T00:00:00Z')['added'] == 0           # no stored final yet
    doubles.absorb(root, list(fx.values()), now=doubles.parse_utc(NOW))
    with closing(model.connect('tennis')) as c:
        c.execute('DELETE FROM checked')
        c.commit()
    assert model.settle('tennis', now='2026-11-16T00:00:00Z')['added'] == 1
    with closing(model.connect('tennis')) as c:
        review = json.loads(c.execute('SELECT payload FROM reviews').fetchone()[0])
    assert review['actual'] == 22 and review['outcome'] == 'push' and review['won'] is None
