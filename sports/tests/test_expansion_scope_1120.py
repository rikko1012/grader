"""sg-scope (2026-11-20): per-fixture extra-time scope, platform rules, extra-time mixture, kept extra-time history,
tennis tour format rule and deficit summaries. Graded REGULATION_LEAGUES lines keep their bytes."""
from contextlib import closing
import json

import pytest

from sports import expansion_forecasts as model, expansion_scope as scope, expansion_formats as formats
from sports import expansion_board as boards, expansion_research as research, store
from sports.contracts import Event, StatObservation
from sports.expansion import isolated_database, parse_scoreboard
from sports.expansion_history import summary_queue
from sports.tests.test_expansion_forecasts import fixture, NOW


def stored(eid, stype, code_status, scores=(1, 1), leg=None, period=2, clock="90'+4'", slug='torneo-apertura-2026'):
    m = {'event_id': f'soccer:espn:mex.1:{eid}:{eid}', 'competition': 'mex.1', 'stage_type': stype,
         'stage_slug': slug, 'source_status': code_status, 'period': period, 'clock': clock,
         'status': 'final' if code_status in ('STATUS_FULL_TIME', 'STATUS_FINAL') else 'unknown',
         'scores': {'a': scores[0], 'b': scores[1]}}
    if leg:
        m['leg'] = leg
    return m


def stages_from(*matches, tmp_path):
    scope.update_stages(tmp_path, {'mex.1': {m['event_id']: m for m in matches}})
    return scope.load_stages(tmp_path)


def test_outcome_codes():
    assert scope.outcome_code(stored(1, '1', 'STATUS_FULL_TIME')) == 'D'
    assert scope.outcome_code(stored(1, '1', 'STATUS_FULL_TIME', leg=1)) == 'L'
    assert scope.outcome_code(stored(1, '1', 'STATUS_FULL_TIME', (2, 1))) == 'R'
    assert scope.outcome_code(stored(1, '1', 'STATUS_FINAL_AET', period=4, clock="120'")) == 'E'
    assert scope.outcome_code(stored(1, '1', 'STATUS_FINAL_PEN', period=5, clock="120'")) == 'E'
    # straight to penalties after 90 minutes (no extra time played) proves the stage has no extra time
    assert scope.outcome_code(stored(1, '1', 'STATUS_FINAL_PEN', period=2, clock="90'+6'")) == 'D'
    assert scope.outcome_code({'status': 'scheduled'}) is None
    assert scope.norm_slug('2025-26-liga-mx-apertura---finals') == 'liga-mx-apertura-finals'
    assert scope.norm_slug('torneo-apertura-2026') == 'torneo-apertura'


def test_league_stage_with_draws_has_no_extra_time(tmp_path):
    st = stages_from(stored(1, '14435', 'STATUS_FULL_TIME'), stored(2, '14435', 'STATUS_FULL_TIME'),
                     stored(3, '14435', 'STATUS_FULL_TIME', (3, 0)), tmp_path=tmp_path)
    r = scope.rule('mex.1', {'stage_type': '14435', 'stage_slug': 'torneo-apertura-2026'}, st)
    assert r['extra_time'] == 'none' and r['basis'] == 'stage_draws' and r['draws'] == 2
    # one draw is not enough; an extra-time finish anywhere in the stage makes it possible
    st = stages_from(stored(1, '9', 'STATUS_FULL_TIME'), tmp_path=tmp_path / 'b')
    assert scope.rule('mex.1', {'stage_type': '9'}, st)['extra_time'] == 'possible'
    st = stages_from(stored(1, '8', 'STATUS_FULL_TIME'), stored(2, '8', 'STATUS_FULL_TIME'),
                     stored(3, '8', 'STATUS_FINAL_AET', period=4, clock="120'"), tmp_path=tmp_path / 'c')
    r = scope.rule('mex.1', {'stage_type': '8'}, st)
    assert r['extra_time'] == 'possible' and 0 < r['p_extra_time'] < 1


def test_first_leg_earlier_seasons_and_unseen_stage(tmp_path):
    assert scope.rule('mex.1', {'stage_type': 'x', 'leg': 1}, {})['basis'] == 'first_leg'
    st = stages_from(stored(1, '13000', 'STATUS_FULL_TIME', slug='torneo-apertura-2025'),
                     stored(2, '13000', 'STATUS_FULL_TIME', slug='torneo-apertura-2025'), tmp_path=tmp_path)
    r = scope.rule('mex.1', {'stage_type': '14435', 'stage_slug': 'torneo-apertura-2026'}, st)
    assert r['extra_time'] == 'none' and r['basis'] == 'same_stage_earlier_seasons'
    # a second leg is never proven by draws (an aggregate tie can go on)
    assert scope.rule('mex.1', {'stage_type': '13000', 'leg': 2}, st)['extra_time'] == 'possible'
    r = scope.rule('mex.1', {}, {})
    assert r['extra_time'] == 'possible' and r['p_basis'] == 'prior' and r['p_extra_time'] == 0.25


def test_platform_rules():
    assert scope.settlement('prizepicks', 'none') == scope.settlement('underdog', 'none') == 'regulation'
    assert scope.settlement('prizepicks', 'possible') == 'regulation_plus_extra_time'
    assert scope.settlement('underdog', 'possible') == 'regulation'      # Underdog never counts extra time
    assert scope.settlement('other', 'possible') is None


def soccer(comp='mex.1', rule=None, platform='prizepicks'):
    offer, facts = fixture()
    offer['platform'] = platform
    facts['identity']['competition'] = comp
    if rule is not None:
        facts['scope_rule'] = rule
    return offer, facts


def test_unlocked_fixture_grades_and_regulation_leagues_unchanged():
    offer, facts = soccer(rule={'version': scope.VERSION, 'extra_time': 'none', 'basis': 'stage_draws',
                                'stage': 'torneo-apertura', 'stage_type': '14435'})
    f, reason = model.make_forecast(offer, facts, NOW)
    assert reason is None and f['grade'] and f['settlement_scope'] == 'regulation'
    assert f['scope_rule']['basis'] == 'stage_draws' and 'extra_time' not in f
    # same games under a regulation league give the same chance; that line carries none of the new keys
    eng, efacts = soccer('eng.1', rule={'extra_time': 'possible', 'p_extra_time': .5})
    g, _ = model.make_forecast(eng, efacts, NOW)
    assert g['p_side'] == f['p_side'] and not {'scope_rule', 'settlement_scope', 'extra_time'} & set(g)
    assert model.fit(*soccer(), NOW)[1] == 'extra_time_scope_unverified'          # no rule attached yet


def test_prizepicks_knockout_uses_the_tested_regulation_predictive():
    # walk-forward 2026-11-20: the 30-minute mixture failed -> ET_SHARE 0: same chance as regulation, but settled on
    # the extra-time box total first
    rule = {'extra_time': 'possible', 'p_extra_time': 0.3, 'basis': 'knockout_or_unseen_stage'}
    assert scope.ET_SHARE == 0.0
    reg, _ = model.fit(*soccer(rule=dict(rule, extra_time='none')), NOW)
    pp, reason = model.fit(*soccer(rule=rule), NOW)
    assert reason is None and pp['p_over'] == reg['p_over'] and pp['distribution'] == reg['distribution']
    assert pp['settle_scopes'] == ['full_game', 'regulation'] and pp['extra_time']['share_of_regulation_rate'] == 0.0


def test_extra_time_mixture_for_prizepicks_regulation_for_underdog(monkeypatch):
    monkeypatch.setattr(scope, 'ET_SHARE', 1 / 3)      # the machinery kept for a re-test
    rule = {'extra_time': 'possible', 'p_extra_time': 0.3, 'basis': 'knockout_or_unseen_stage'}
    reg, _ = model.fit(*soccer(rule=dict(rule, extra_time='none')), NOW)
    pp, reason = model.fit(*soccer(rule=rule), NOW)
    assert reason is None and pp['distribution'].endswith('extra-time-mixture')
    assert pp['settle_scopes'] == ['full_game', 'regulation'] and pp['extra_time']['p'] == 0.3
    assert pp['projection'] == pytest.approx(reg['projection'] * (1 + 0.3 / 3), rel=1e-6)
    assert pp['p_over'] > reg['p_over']
    assert sum(pp['p_' + s] for s in ('over', 'under', 'push')) == pytest.approx(1)
    ud, _ = model.fit(*soccer(rule=rule, platform='underdog'), NOW)
    assert ud['p_over'] == reg['p_over'] and ud['settlement_scope'] == 'regulation' and 'settle_scopes' not in ud
    bad, why = model.fit(*soccer(rule=dict(rule, p_extra_time=None)), NOW)
    assert bad is None and why == 'extra_time_scope_unverified'


def test_settle_prefers_extra_time_box_totals(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    offer, facts = soccer(rule={'extra_time': 'possible', 'p_extra_time': 0.3})
    ident = facts['identity'] = {'competition': 'mex.1', 'event_id': 'soccer:espn:mex.1:5:5',
                                 'player_id': 'soccer:player:espn:9'}
    facts['event']['event_id'] = ident['event_id']
    (tmp_path / 'soccer').mkdir()
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': [offer]}))
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(offer): facts}})
    assert model.publish('soccer', NOW)['published'] == 1
    known = '2026-11-18T21:00:00Z'
    with closing(store.connect(isolated_database(tmp_path, 'mex.1'))) as c:
        store.put_many(c, [Event(ident['event_id'], 'soccer', 2026, 'unknown', None, None, offer['start'], 'espn',
                                 status='final', observed_at=known, known_at=known),
                           StatObservation(ident['event_id'], 'soccer', ident['player_id'], 'player', 'full_game',
                                           'yes', 'shots', 'count', 4, 'played', 'espn', '5', True, known, known)])
    assert model.settle('soccer', '2026-11-18T22:00:00Z')['added'] == 1
    with closing(model.connect('soccer')) as c:
        review = json.loads(c.execute('SELECT payload FROM reviews').fetchone()[0])
    assert review['actual'] == 4 and review['outcome'] == 'over'


def test_scoreboard_carries_stage_facts_and_tennis_major():
    event = {'id': '1', 'uid': 's:600~l:760~e:1', 'season': {'type': 14435, 'slug': 'torneo-apertura-2026'},
             'competitions': [{'id': '1', 'date': '2026-11-21T22:50Z', 'leg': {'value': 2, 'displayValue': '2nd Leg'},
                               'status': {'period': 5, 'displayClock': "120'",
                                          'type': {'name': 'STATUS_FINAL_PEN', 'state': 'post', 'completed': True}},
                               'competitors': [{'id': 'a', 'type': 'team', 'homeAway': 'home', 'team': {'displayName': 'A', 'abbreviation': 'A'}},
                                               {'id': 'b', 'type': 'team', 'homeAway': 'away', 'team': {'displayName': 'B', 'abbreviation': 'B'}}]}]}
    m = parse_scoreboard({'events': [event]}, 'mex.1', NOW).matches[0]
    assert (m['stage_type'], m['stage_slug'], m['leg'], m['period'], m['clock']) == (
        '14435', 'torneo-apertura-2026', 2, 5, "120'")
    assert scope.outcome_code(m) == 'E'
    assert formats.tour_rule('wta', {'major': True})['best_of'] == 3
    assert formats.tour_rule('atp', {'major': False, 'name': 'Nitto ATP Finals'})['best_of'] == 3
    assert formats.tour_rule('atp', {'major': True}) is None
    assert formats.tour_rule('atp', {'major': False, 'name': 'Next Gen ATP Finals'}) is None
    assert formats.tour_rule('atp', {}) is None and formats.tour_rule('atp_challenger', {'major': False}) is None


def test_tennis_full_match_uses_tour_rule_only_when_format_missing():
    offer, facts = fixture('tennis')
    offer.update(stat='games_won', scope='full_game', line=12.5)
    facts.update(scope='full_game')
    facts['identity']['competition'] = 'atp'
    for g in facts['games']:
        g.update(source='espn', value=g['value'] + 10, best_of=3)
    assert model.fit(offer, facts, NOW)[1] == 'match_format_missing'
    facts['format_rule'] = formats.tour_rule('atp', {'major': False, 'name': 'X'})
    f, reason = model.fit(offer, facts, NOW)
    assert reason is None and f['context_used'] == {'best_of': 3} and f['format_rule']['best_of'] == 3


def test_deficit_summaries_only_unread_finals_of_offered_teams():
    def m(i, team, checked=None, status='final'):
        return {'event_id': f'soccer:espn:aus.1:{i}:{i}', 'competition': 'aus.1', 'status': status,
                'start': f'2026-11-{i:02}T10:00:00Z', 'participants': [{'id': team}, {'id': 'z'}],
                'summary_checked_at': checked}
    pending = [m(i, 'off') for i in range(1, 9)] + [m(10, 'other'), m(11, 'off', status='scheduled')]
    assert len(summary_queue(pending, {'off'}, 0, 2)) == 2                    # unchanged without deficit slots
    got = summary_queue(pending, {'off'}, 0, 2, 4)
    assert len(got) == 6 and all(x['participants'][0]['id'] == 'off' and x['status'] == 'final' for x in got[2:])


def test_end_to_end_liga_mx_line_is_graded_with_and_without_stage_evidence(tmp_path, monkeypatch):
    """Store -> research.publish (attaches the fixture's scope rule from stages.json) -> forecasts.publish: a mex.1
    full-game line that was extra_time_scope_unverified gets a letter; pending_by names competition/stat/platform."""
    from sports.tests.test_expansion_guard_1118 import _store, NOW as GNOW
    from sports.expansion_history import atomic_json
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    root, offers = _store(tmp_path, 'mex.1', [])
    matches = json.loads((root / 'current-matches.json').read_text(encoding='utf-8'))
    matches[0].update(stage_type='14435', stage_slug='torneo-apertura-2026')
    atomic_json(root / 'current-matches.json', matches)
    monkeypatch.setattr(boards, 'board', lambda req: (200, {'offers': offers}))

    def shots():
        research._read.cache_clear()
        research.publish('soccer')
        out = model.publish('soccer', GNOW)
        doc = json.loads((root / 'forecasts.json').read_text(encoding='utf-8'))
        return [f for f in doc['forecasts'].values() if f['stat'] == 'shots'], out
    # no stored stage evidence yet: the fixture may reach extra time; PrizePicks counts it -> the tested regulation
    # predictive, settled on the extra-time box total first. Graded either way.
    first, _ = shots()
    assert len(first) == 1 and first[0]['grade'] and first[0]['scope_rule']['extra_time'] == 'possible'
    assert first[0]['settle_scopes'] == ['full_game', 'regulation']
    scope.update_stages(root, {'mex.1': {m['event_id']: m for m in (
        stored(1, '14435', 'STATUS_FULL_TIME'), stored(2, '14435', 'STATUS_FULL_TIME'))}})
    second, out = shots()
    assert len(second) == 1 and second[0]['grade'] and second[0]['settlement_scope'] == 'regulation'
    assert second[0]['scope_rule']['basis'] == 'stage_draws' and second[0]['p_side'] == first[0]['p_side']
    assert out['pending_by'] and all(k.split('|')[1] == 'mex.1' for k in out['pending_by'])
