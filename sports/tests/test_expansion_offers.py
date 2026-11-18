import json
from copy import deepcopy
from types import SimpleNamespace
import sqlite3

import pytest

from sports import expansion_offers as offers, expansion_board as board

NOW = '2026-11-17T12:00:00Z'


def pp():
    return {'data':[{'type':'projection','id':'o1','attributes':{'stat_type':'Shots','line_score':2.5,
        'start_time':'2026-11-18T12:00:00Z','status':'pre_game','projection_type':'Single Stat',
        'allowed_wager_types':'over','odds_type':'standard'},'relationships':{
        k:{'data':{'type':k,'id':v}} for k,v in [('new_player','p1'),('game','g1'),('duration','d1')]}}],
        'included':[{'type':'new_player','id':'p1','attributes':{'league':'SOCCER','name':'Player', 'position':'Forward'}},
                    {'type':'duration','id':'d1','attributes':{'name':'Full'}},
                    {'type':'game','id':'g1','attributes':{}}]}


def test_real_side_and_unknown_price_preserved():
    row = offers.prizepicks(pp(), 'soccer', NOW)[0]
    assert row['sides'] == {'over': {'multiplier':None}}
    assert row['forecast'] is None
    assert row['source_updated_at'] is None
    assert row['role'] == 'Forward'


@pytest.mark.parametrize('field,value',[('line_score',None),('line_score',True),('line_score',float('nan')),
    ('start_time','2026-11-17T11:59:59Z'),('start_time','2026-11-18T12:00:00'),
    ('allowed_wager_types','unknown'),('is_live',True),('is_promo',True),('flash_sale_line_score',1.5)])
def test_invalid_or_non_pregame_offer_excluded(field,value):
    b=pp();b['data'][0]['attributes'][field]=value
    assert offers.prizepicks(b,'soccer',NOW)==[]


def test_other_sport_and_unknown_period_excluded():
    b=pp(); b['included'][0]['attributes']['league']='NBA'
    assert not offers.prizepicks(b,'soccer',NOW)
    b=pp(); b['included'][1]['attributes']['name']='Extra time'
    assert not offers.prizepicks(b,'soccer',NOW)


def test_quote_revisions_keep_return_to_old_price(tmp_path):
    a=offers.prizepicks(pp(),'soccer',NOW)[0]
    board.save('soccer',[a],{},NOW,tmp_path)
    board.save('soccer',[a],{},NOW,tmp_path)
    b=deepcopy(a);b['line']=3.5;b=offers.finish({k:v for k,v in b.items() if k!='revision'})
    board.save('soccer',[b],{},NOW,tmp_path)
    board.save('soccer',[a],{},NOW,tmp_path)
    with sqlite3.connect(tmp_path/'quotes.db') as con:
        assert con.execute('SELECT revision FROM revisions ORDER BY id').fetchall()==[(a['revision'],),(b['revision'],),(a['revision'],)]
    with pytest.raises(ValueError,match='another sport'):
        board.save('tennis',[],{},NOW,tmp_path)


def test_api_disabled_and_malformed_query(monkeypatch):
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED',raising=False)
    assert board.board(SimpleNamespace(query={'sport':'soccer'}))[0]==404
    assert board.board(SimpleNamespace(query={'sport':'../nba'}))[0]==400


def test_mixed_batch_refused(tmp_path):
    row=offers.prizepicks(pp(),'soccer',NOW)[0]
    with pytest.raises(ValueError,match='mixed sport'):
        board.save('tennis',[row],{},NOW,tmp_path)
    assert not (tmp_path/'quotes.db').exists()


def test_stale_snapshot_refused(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','1')
    monkeypatch.setattr(board,'root',lambda _:tmp_path)
    row=offers.prizepicks(pp(),'soccer',NOW)[0]
    board.save('soccer',[row],{},'2000-01-01T00:00:00Z',tmp_path)
    assert board.board(SimpleNamespace(query={'sport':'soccer'}))[0]==503


def test_unchanged_response_saves_bandwidth_but_never_hides_line_change(monkeypatch,tmp_path):
    from datetime import datetime, timedelta, timezone
    from sports.contracts import now_utc, utc
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','1')
    monkeypatch.setattr(board,'root',lambda _:tmp_path)
    now=now_utc();body=pp()
    body['data'][0]['attributes']['start_time']=utc(datetime.now(timezone.utc)+timedelta(hours=2))
    row=offers.prizepicks(body,'soccer',now)[0]
    source={'prizepicks':{'ok':True,'checked_at':now,'offers':1}}
    board.save('soccer',[row],source,now,tmp_path)
    _,full=board.board(SimpleNamespace(query={'sport':'soccer'}))
    query={'sport':'soccer','version':full['version']}
    _,same=board.board(SimpleNamespace(query=query))
    assert same['unchanged'] and 'offers' not in same
    body['data'][0]['attributes']['line_score']=4.5
    board.save('soccer',offers.prizepicks(body,'soccer',now),source,now,tmp_path)
    _,moved=board.board(SimpleNamespace(query=query))
    assert 'unchanged' not in moved and moved['offers'][0]['line']==4.5
    board.save('soccer',[],{'prizepicks':{'ok':False,'checked_at':now}},now,tmp_path)
    _,removed=board.board(SimpleNamespace(query=query))
    assert 'unchanged' not in removed and removed['offers']==[]


def test_shared_response_cache_expires_at_game_start_and_quote_expiry(monkeypatch,tmp_path):
    from datetime import datetime, timedelta, timezone
    from sports.contracts import utc
    base=datetime.now(timezone.utc).replace(microsecond=0)
    class Clock(datetime):
        current=base
        @classmethod
        def now(cls,tz=None):return cls.current
    monkeypatch.setattr(board,'datetime',Clock)
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','1')
    monkeypatch.setattr(board,'root',lambda _:tmp_path)
    body=pp();body['data'][0]['attributes']['start_time']=utc(base+timedelta(seconds=1))
    first=offers.prizepicks(body,'soccer',utc(base))[0]
    second={**first,'offer_id':'older','start':utc(base+timedelta(hours=2)),
            'observed_at':utc(base-timedelta(seconds=299))}
    board.save('soccer',[first,second],{},utc(base),tmp_path)
    req=SimpleNamespace(query={'sport':'soccer'})
    code,encoded,_=board.http_board(req)
    assert code==200 and len(json.loads(encoded)['offers'])==2
    assert board.http_board(req)[1] is encoded
    Clock.current=base+timedelta(seconds=2)
    assert json.loads(board.http_board(req)[1])['offers']==[]
    Clock.current=base+timedelta(seconds=301)
    assert board.http_board(req)[0]==503


# ---- 2026-11-18 collection fixes: PrizePicks tennis labels / missing wager field, MLS, Underdog soccer ----------
def pp_tennis(stat='Total Games Won', allowed=None, odds='standard', duration='Full', description='Flavio Cobolli',
              teams=None):
    b = pp()
    a = b['data'][0]['attributes']
    a.update(stat_type=stat, odds_type=odds, description=description)
    if allowed is None:
        a.pop('allowed_wager_types')
    else:
        a['allowed_wager_types'] = allowed
    b['included'][0]['attributes'] = {'league': 'TENNIS', 'display_name': 'Frances Tiafoe', 'position': 'Player'}
    b['included'][1]['attributes'] = {'name': duration}
    if teams:
        b['included'][2]['attributes'] = {'metadata': {'game_info': {'teams': {
            'away': {'abbreviation': teams[0]}, 'home': {'abbreviation': teams[1]}}}}}
    return b


@pytest.mark.parametrize('label,stat,scope,duration', [('Total Games Won', 'games_won', 'full_game', 'Full'),
    ('Total Sets', 'sets_played', 'full_game', 'Full'), ('Total Tie Breaks', 'tie_breakers_played', 'full_game', 'Full'),
    ('1st Set Total Games', 'games_played', 'set1', '1st Set')])
def test_prizepicks_tennis_labels_map_to_stored_stats(label, stat, scope, duration):
    row = offers.prizepicks(pp_tennis(label, duration=duration), 'tennis', NOW)[0]
    assert (row['stat'], row['scope']) == (stat, scope)
    assert row['opponent'] == 'Flavio Cobolli' and 'tour' in row and row['tour'] is None


def test_prizepicks_missing_wager_field_is_both_sides_only_on_standard_lines():
    assert offers.prizepicks(pp_tennis(), 'tennis', NOW)[0]['sides'] == {'over': {'multiplier': None},
                                                                        'under': {'multiplier': None}}
    assert offers.prizepicks(pp_tennis(odds='demon'), 'tennis', NOW) == []      # side unknown: never invented
    assert offers.prizepicks(pp_tennis(odds='goblin'), 'tennis', NOW) == []
    assert list(offers.prizepicks(pp_tennis(odds='demon', allowed='over'), 'tennis', NOW)[0]['sides']) == ['over']
    assert offers.prizepicks(pp_tennis('Fantasy Score'), 'tennis', NOW) == []    # still unmapped


def test_prizepicks_tennis_opponent_must_agree_and_never_a_doubles_pair():
    row = offers.prizepicks(pp_tennis(teams=('Frances Tiafoe', 'Flavio Cobolli'), description=None), 'tennis', NOW)[0]
    assert row['opponent'] == 'Flavio Cobolli'
    row = offers.prizepicks(pp_tennis(teams=('Frances Tiafoe', 'Flavio Cobolli'), description='Ben Shelton'), 'tennis', NOW)[0]
    assert row['opponent'] is None
    assert offers.prizepicks(pp_tennis(description='Luz O / Matos R'), 'tennis', NOW)[0]['opponent'] is None
    assert offers.prizepicks(pp_tennis('1st Set Total Games', duration='Full'), 'tennis', NOW) == []


def test_prizepicks_mls_players_are_soccer():
    b = pp(); b['included'][0]['attributes']['league'] = 'MLS'
    assert offers.prizepicks(b, 'soccer', NOW)[0]['sport'] == 'soccer'
    b = pp(); b['included'][1]['attributes']['name'] = '1st Set'
    assert offers.prizepicks(b, 'soccer', NOW) == []


def ud_soccer(stat='period_1_2_shots_attempted', choices=('higher',), team='h'):
    return {'games': [{'id': 9, 'sport_id': 'FIFA', 'status': 'scheduled', 'scheduled_at': '2026-11-20T19:30:00Z',
                       'home_team_id': 'h', 'away_team_id': 'a', 'title': 'BAY vs KOE',
                       'full_team_names_title': 'Bayern München vs 1. FC Köln',
                       'short_title': 'Bayern vs Köln',
                       'abbreviated_title': 'BAY vs KOE', 'sport_group_name': 'Bundesliga'}],
            'players': [{'id': 'p', 'sport_id': 'FIFA', 'first_name': 'Harry', 'last_name': 'Kane', 'team_id': team,
                         'position_display_name': 'Forward'}],
            'appearances': [{'id': 'ap', 'player_id': 'p', 'match_id': 9, 'team_id': team}],
            'over_under_lines': [{'id': 'L1', 'status': 'active', 'stat_value': '6.5', 'line_type': 'balanced',
                'over_under': {'category': 'player_prop', 'appearance_stat': {'appearance_id': 'ap', 'stat': stat,
                                                                              'display_stat': 'Shots Attempted'}},
                'options': [{'choice': c, 'status': 'active', 'payout_multiplier': '0.86',
                             'updated_at': '2026-11-18T16:43:33Z'} for c in choices]}]}


def test_underdog_soccer_keeps_only_listed_sides_and_exact_team_names():
    row = offers.underdog_soccer(ud_soccer(), NOW)[0]
    assert row['sides'] == {'over': {'multiplier': 0.86}} and row['stat'] == 'shots' and row['scope'] == 'full_game'
    assert row['teams'] == ['Bayern München', '1. FC Köln'] and row['team'] == 'Bayern München'
    assert row['team_aliases'] == [['Bayern München', 'Bayern', 'BAY'], ['1. FC Köln', 'Köln', 'KOE']]
    assert offers.underdog_soccer(ud_soccer(team='a'), NOW)[0]['team'] == '1. FC Köln'
    for stat in ('period_1_2_goals_assists', 'period_1_goals', 'period_1_2_cards'):
        assert offers.underdog_soccer(ud_soccer(stat), NOW) == []
    assert offers.underdog_soccer(ud_soccer(team='x'), NOW) == []


def test_underdog_soccer_identity_needs_both_teams_exactly():
    from sports import expansion_history as history
    offer = offers.underdog_soccer(ud_soccer(), NOW)[0]
    match = {'event_id': 'ger.1:espn:1:x', 'competition': 'ger.1', 'start': '2026-11-20T19:30:00Z',
             'participants': [{'id': 'T1', 'name': 'Bayern Munich', 'abbreviation': 'BAY', 'short_name': 'Bayern'},
                              {'id': 'T2', 'name': '1. FC Cologne', 'abbreviation': 'KOE', 'short_name': 'Köln'}],
             'players': [{'id': 'P9', 'name': 'Harry Kane', 'team_id': 'T1'}]}
    assert history.resolve(offer, [match])['player_id'] == 'P9'
    other = dict(match, participants=[match['participants'][0], {'id': 'T3', 'name': 'Mainz', 'abbreviation': 'M05'}])
    assert history.resolve(offer, [other]) is None


def test_prizepicks_tennis_without_tour_resolves_exact_pair_only():
    from sports import expansion_history as history
    offer = offers.prizepicks(pp_tennis(), 'tennis', NOW)[0]
    offer['start'] = '2026-11-18T14:00:00Z'
    m = {'event_id': 'atp:espn:atp:t:1', 'competition': 'atp', 'start': '2026-11-18T14:30:00Z',
         'participants': [{'id': 'A', 'name': 'Frances Tiafoe'}, {'id': 'B', 'name': 'Flavio Cobolli'}]}
    assert history.resolve(offer, [m])['player_id'] == 'A'
    assert history.resolve(offer, [m, dict(m, event_id='atp:365scores:atp_challenger:1:2',
                                            competition='atp_challenger')]) is None    # two answers: abstain
    assert history.resolve(dict(offer, opponent=None), [m]) is None
    assert history.resolve(dict(offer, platform='underdog'), [m]) is None          # Underdog keeps its tour rule


def test_board_fit_drops_only_hidden_prizepicks_variants_latest_first():
    rows = [{'platform': 'prizepicks', 'variant': v, 'start': s, 'pad': 'x' * 100}
            for v, s in [('standard', '1'), ('demon', '1'), ('goblin', '3'), ('demon', '2')]]
    rows.append({'platform': 'underdog', 'variant': 'balanced', 'start': '9', 'pad': 'x' * 100})
    import json
    size = lambda r: len(json.dumps(r, separators=(',', ':'))) + 1
    kept = board.fit(rows, limit=size(rows[0]) + size(rows[1]) + size(rows[4]))
    assert [(r['platform'], r['variant'], r['start']) for r in kept] == [
        ('prizepicks', 'standard', '1'), ('prizepicks', 'demon', '1'), ('underdog', 'balanced', '9')]
    assert board.fit(rows, limit=10**6) is rows


def test_quotes_job_requests_mls_and_underdog_soccer_only_when_flagged(monkeypatch, tmp_path):
    from sports import expansion_odds
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    calls = []
    def get_json(sport, url, params=None, **kw):
        calls.append((url, dict(params or {})))
        return {}
    monkeypatch.setattr('sports.expansion_sources.get_json', get_json)
    monkeypatch.setattr(expansion_odds, 'collect', lambda sport, get, directory: ([], {}))
    snap = board.collect('soccer')
    assert [p.get('league_id') or p.get('sport_id') for _, p in calls] == [82]          # defaults: unchanged
    # Kalshi (price reference) reports itself; with no fixture catalogue it sends no request at all.
    assert set(snap['sources']) == {'prizepicks', 'kalshi'} and snap['sources']['kalshi']['skipped'] == 'no_fixture'
    assert snap['reference_prices'] == []
    calls.clear(); monkeypatch.setenv('SCOUT_SOCCER_UNDERDOG', '1'); monkeypatch.setenv('SCOUT_SOCCER_PP_MLS', '1')
    snap = board.collect('soccer')
    assert [p.get('league_id') or p.get('sport_id') for _, p in calls] == [82, 6, 'FIFA']
    assert snap['sources']['underdog']['ok'] and snap['sources']['prizepicks_mls']['ok']
    calls.clear(); board.collect('tennis')
    assert [p.get('league_id') or p.get('sport_id') for _, p in calls] == [5, 'TENNIS']


def ud_tennis(**game):
    return {'players': [{'id': 'p1', 'sport_id': 'TENNIS', 'first_name': 'Frances', 'last_name': 'Tiafoe'}],
            'appearances': [{'id': 'a1', 'player_id': 'p1', 'match_id': 'g1'}],
            'solo_games': [{'id': 'g1', 'sport_group_name': 'ATP Challenger', 'scheduled_at': '2026-11-18T14:00:00Z',
                            'status': 'scheduled', 'title': 'Tiafoe vs Jacquet', 'home_player_id': 'p1',
                            'away_player_id': 'p2', 'home_player_name': '(4) Frances Tiafoe',
                            'away_player_name': '(2) Kyrian Jacquet', **game}],
            'over_under_lines': [{'id': 'l1', 'status': 'active', 'stat_value': '21.5', 'line_type': 'balanced',
                'over_under': {'category': 'player_prop', 'appearance_stat': {'appearance_id': 'a1', 'stat': 'games_played'}},
                'options': [{'choice': 'higher', 'status': 'active', 'payout_multiplier': '1.0',
                             'updated_at': '2026-11-17T11:00:00Z'}]}]}


def test_underdog_tennis_opponent_without_own_line_comes_from_the_game():
    from sports import expansion_history as history
    row = offers.underdog(ud_tennis(), NOW)[0]
    assert (row['player'], row['opponent'], row['tour']) == ('Frances Tiafoe', 'Kyrian Jacquet', 'atp')   # seed prefix dropped
    m = {'event_id': 'atp:espn:atp:t:1', 'competition': 'atp', 'start': '2026-11-18T14:00:00Z',
         'participants': [{'id': 'A', 'name': 'Frances Tiafoe'}, {'id': 'B', 'name': 'Kyrian Jacquet'}]}
    assert history.resolve(row, [m])['player_id'] == 'A'
    swapped = ud_tennis(home_player_id='p2', away_player_id='p1', home_player_name='Kyrian Jacquet', away_player_name='x')
    assert offers.underdog(swapped, NOW)[0]['opponent'] == 'Kyrian Jacquet'
    body = ud_tennis()                                 # the players table still wins when it has the opponent
    body['players'].append({'id': 'p2', 'sport_id': 'TENNIS', 'first_name': 'K.', 'last_name': 'Jacquet'})
    assert offers.underdog(body, NOW)[0]['opponent'] == 'K. Jacquet'
    for missing in ('', None, '(2) '):                 # no name anywhere: the offer stays unlinked, nothing is guessed
        row = offers.underdog(ud_tennis(away_player_name=missing), NOW)[0]
        assert row['opponent'] == '' and history.resolve(row, [m]) is None
