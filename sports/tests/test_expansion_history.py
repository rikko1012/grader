from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sports import expansion_history as history, expansion_board as boards, store
from sports.contracts import Event, StatObservation, now_utc
from sports.expansion import parse_scoreboard, isolated_database


def tennis_match():
    return {'event_id':'atp:espn:atp:tour:10', 'competition':'atp', 'start':'2026-11-18T14:00:00Z',
            'participants':[{'id':'atp:player:espn:1','name':'Carlos Alcaraz'},
                            {'id':'atp:player:espn:2','name':'Daniil Medvedev'}]}


def tennis_offer():
    return {'sport':'tennis','tour':'atp','player':'Carlos Alcaraz','opponent':'Daniil Medvedev',
            'start':'2026-11-18T14:00:00Z'}


def test_exact_tennis_resolution_requires_tour_opponent_start_and_unique_match():
    offer, match = tennis_offer(), tennis_match()
    assert history.resolve(offer,[match])['player_id']=='atp:player:espn:1'
    for change in ({'tour':'wta'},{'opponent':'Other Opponent'},{'player':'C. Alcaraz'},
                   {'start':'2026-11-19T14:00:00Z'},{'sport':'soccer'}):
        assert history.resolve({**offer, **change},[match]) is None
    assert history.resolve(offer,[match, {**match,'event_id':'other'}]) is None
    assert history.resolve(offer,[match,match]) is not None


def test_soccer_same_name_is_not_enough_team_opponent_and_roster_required():
    match={'event_id':'soccer:espn:eng.1:1:1','competition':'eng.1','start':'2026-11-18T14:00:00Z',
           'participants':[{'id':'t1','name':'Austria','abbreviation':'AUT'},
                           {'id':'t2','name':'Bosnia','abbreviation':'BIH'}],
           'players':[{'id':'p1','name':'Test Player','team_id':'t1'}]}
    offer={'sport':'soccer','player':'Test Player','team':'Austria','teams':['AUT','BIH'],'start':match['start']}
    assert history.resolve(offer,[match])['player_id']=='p1'
    for change in ({'team':'Bosnia'},{'teams':['AUT','OTHER']},{'teams':[]},{'player':'Player'}):
        assert history.resolve({**offer, **change},[match]) is None
    copy=deepcopy(match);copy['players'].append({'id':'p2','name':'Test Player','team_id':'t1'})
    assert history.resolve(offer,[copy]) is None


def test_summary_rejects_actual_other_competition():
    from sports.expansion_soccer import parse_summary
    from sports.tests.test_expansion_soccer import summary
    body=summary();body['header']['league']={'slug':'uefa.champions'}
    assert not parse_summary(body,'eng.1','1',now_utc()).records


def scoreboard(scores=((6,6),(4,3))):
    return {'events':[{'id':'tournament','competitions':[{'id':'m','date':'2026-11-16T12:00Z',
        'status':{'type':{'completed':True,'name':'STATUS_FINAL','state':'post'}},
        'competitors':[{'id':str(i+1),'type':'athlete','athlete':{'displayName':f'Player {i}'},
                        'linescores':[{'value':n} for n in score]} for i,score in enumerate(scores)]}]}]}


def test_tennis_totals_are_exact_and_do_not_invent_serve_stats():
    feed=parse_scoreboard(scoreboard(),'atp',now_utc())
    rows={r.stat:r.value for r in feed.records if isinstance(r,StatObservation)
          and r.participant_id=='atp:player:espn:1' and r.scope=='full_game'}
    assert rows=={'games_won':12,'games_played':19,'sets_won':2,'sets_played':2,'tie_breakers_played':0}
    assert 'aces' not in rows


@pytest.mark.parametrize('scores', [((6,4),(4,6)),((6,6,6),(4,4,4,4)),((6,6,4),(4,4,6)),((6,7),(4,4)),((6,10),(4,8))])
def test_inconsistent_or_unfinished_sets_cannot_settle(scores):
    feed=parse_scoreboard(scoreboard(scores),'atp',now_utc())
    assert not [r for r in feed.records if isinstance(r,StatObservation)]


def test_tennis_placeholders_never_enter_history():
    body=scoreboard();body['events'][0]['competitions'][0]['competitors'][0]['id']='-3'
    assert not parse_scoreboard(body,'atp',now_utc()).records


def test_history_uses_exact_stat_period_current_revision_and_known_time(tmp_path):
    path=isolated_database(tmp_path,'atp'); con=store.connect(path)
    pid='atp:player:espn:1';eid='atp:espn:atp:tournament:m'
    old='2026-11-16T18:00:00Z';new='2026-11-16T19:00:00Z'
    event=Event(eid,'atp',2026,'unknown',None,None,'2026-11-16T12:00:00Z','espn',status='final',observed_at=old,known_at=old)
    def obs(value,scope='full_game',known=old):
        return StatObservation(eid,'atp',pid,'player',scope,'no','games_won','count',value,'played','espn','m',True,known,known)
    store.put_many(con,[event,obs(11),obs(6,'set1')]);store.put_many(con,[obs(12,known=new)]);con.close()
    first=history._history(str(path),('v1',),pid,'games_won','full_game','2026-11-16T18:30:00Z')
    latest=history._history(str(path),('v2',),pid,'games_won','full_game','2026-11-16T20:00:00Z')
    assert [g['value'] for g in first]==[11]
    assert [g['value'] for g in latest]==[12]
    assert not history._history(str(path),('v3',),pid,'aces','full_game','2026-11-16T20:00:00Z')
    assert not history._history(str(path),('v4',),pid,'games_won','full_game','2026-11-16T11:00:00Z')


def test_collection_request_budget_and_sport_storage(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    calls=[]
    def get(url,**kw):
        calls.append(url)
        return scoreboard()
    monkeypatch.setattr(history.net,'get_json',get)
    result=history.collect('tennis',max_requests=2)
    assert len(calls)==2 and result['requests']==2
    assert (tmp_path/'tennis/atp/canonical.db').exists()
    assert (tmp_path/'tennis/wta/canonical.db').exists()
    assert not (tmp_path/'canonical.db').exists()
    assert not (tmp_path/'soccer').exists()


def test_history_counts_a_game_once_and_excludes_disagreeing_sources(tmp_path):
    path=isolated_database(tmp_path,'atp'); con=store.connect(path)
    pid='atp:player:espn:1'; eid='atp:espn:atp:tournament:m'
    stamp='2026-11-16T18:00:00Z'
    event=Event(eid,'atp',2026,'unknown',None,None,'2026-11-16T12:00:00Z','espn',
                status='final',observed_at=stamp,known_at=stamp)
    def obs(value,source,known=stamp):
        return StatObservation(eid,'atp',pid,'player','full_game','no','aces','count',value,
                               'played',source,'m',True,known,known)
    store.put_many(con,[event,obs(8,'espn'),obs(8,'thescore')])
    assert len(history._history(str(path),('same',),pid,'aces','full_game','2026-11-16T18:30:00Z'))==1
    store.put_many(con,[obs(9,'thescore','2026-11-16T19:00:00Z')]);con.close()
    assert not history._history(str(path),('different',),pid,'aces','full_game','2026-11-16T20:00:00Z')


def test_history_cannot_query_arbitrary_players_or_unreleased_sport(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    monkeypatch.delenv('SCOUT_TENNIS_ENABLED',raising=False)
    assert history.history(SimpleNamespace(query={'sport':'tennis','offer_id':'x'}))[0]==404


def test_provider_hold_survives_separate_jobs(monkeypatch,tmp_path):
    from sports import expansion_sources, net
    import time
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    until=time.time()+3600
    def first(url,**kw):
        net.hold('example.test',until)
        raise net.HttpError(429,url,not_before=until)
    monkeypatch.setattr(net,'get_json',first)
    with pytest.raises(net.HttpError):
        expansion_sources.get_json('soccer','https://example.test/lines')
    monkeypatch.setattr(net,'_HOLD',{})
    def second(url,**kw):
        assert net.holds()['example.test']==until
        return {}
    monkeypatch.setattr(net,'get_json',second)
    expansion_sources.get_json('tennis','https://example.test/lines')


def test_future_soccer_uid_resolves_competition_and_conflicting_proof_is_rejected():
    assert history.soccer_competition({'uid':'s:600~l:630~e:123','links':[]})=='bra.1'
    assert history.soccer_competition({'uid':'s:600~l:630~e:123','links':[
        {'href':'https://www.espn.com/soccer/table/_/league/eng.1'}]}) is None
    assert history.soccer_competition({'uid':'s:600~l:99999~e:123'}) is None


def test_team_roster_fallback_links_identity_without_inventing_stats(tmp_path):
    import json
    match={'event_id':'soccer:espn:bra.1:123:123','competition':'bra.1',
           'start':now_utc(),'status':'scheduled', 'participants':[
               {'id':'soccer:team:espn:1','name':'Home FC','short_name':'Home'},
               {'id':'soccer:team:espn:2','name':'Away FC','short_name':'Away'}]}
    offer={'sport':'soccer','teams':['Home','Away'],'team':'Home','player':'Test Player','start':match['start']}
    history.atomic_json(tmp_path/'board.json',{'offers':[offer]})
    calls=[];saved=[]
    def get(url, params):
        calls.append(url)
        if url.endswith('/roster'):return {'team':{'id':'1'},'athletes':[{'id':'3','displayName':'Test Player'}]}
        return {'team':{'id':'1'},'events':[]}
    history.collect_soccer_teams({'bra.1':{'123':match}},get,lambda *a:saved.append(a),tmp_path)
    assert history.resolve(offer,[match])['player_id']=='soccer:player:espn:3'
    assert not saved  # Current roster is not a completed game's stat line.
    assert len(calls)==2
    match.pop('players')
    history.collect_soccer_teams({'bra.1':{'123':match}},get,lambda *a:saved.append(a),tmp_path)
    assert len(calls)==2 and history.resolve(offer,[match])
    assert match['players'][0]['identity_source']=='espn_team_roster'


def test_one_tour_outage_does_not_block_the_other(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        if '/atp/' in url:raise history.net.HttpError(503,url)
        return scoreboard()
    monkeypatch.setattr(history.net,'get_json',get)
    result=history.collect('tennis',max_requests=2)
    assert len(calls)==2 and result['errors']==['HttpError']
    assert (tmp_path/'tennis/wta/canonical.db').exists()
    assert not (tmp_path/'tennis/atp/canonical.db').exists()


# ---- 2026-11-18: history jobs exited 1 forever once the isolated stores passed a fixed 128 MB cap ----------------
def _fill(path, mb):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:                      # sparse: size counts toward the budget, no real disk used
        f.seek(mb * 1024 * 1024 - 1)
        f.write(b'\0')


def test_over_budget_store_keeps_live_results_pauses_backfill_and_exits_0(monkeypatch, tmp_path, capsys):
    import json
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    monkeypatch.setenv('SCOUT_EXPANSION_HISTORY_MB', '16')
    _fill(tmp_path / 'tennis/old/canonical.db', 20)    # over the 16 MB budget, under 1.5x
    calls = []
    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        return scoreboard() if 'espn' in url else {}
    monkeypatch.setattr(history.net, 'get_json', get)
    monkeypatch.setattr('sys.argv', ['x', 'tennis'])
    history.main()                                     # no SystemExit: success
    result = json.loads(capsys.readouterr().out)
    assert result['records'] > 0 and result['storage'] == 'backfill_paused'
    assert result['phases'] == {'live': 'ok', 'backfill': 'paused'}
    assert result['ok_requests'] == result['requests']          # the fake theScore body is an empty dict (ValueError there)
    assert (tmp_path / 'tennis/atp/canonical.db').exists()
    state = json.loads((tmp_path / 'tennis/history-state.json').read_text())
    assert state['offset'] == 1 and state['stored_at'] == result['finished_at']   # cursor never skips a window
    espn_dates = [p['dates'] for u, p in calls if 'site.api.espn.com' in u and 'scoreboard' in u]
    assert espn_dates and all('-' in d for d in espn_dates)
    assert history.results_at('tennis') == result['finished_at']


def test_full_store_writes_nothing_but_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_EXPANSION_HISTORY_MB', '16')
    _fill(tmp_path / 'tennis/old/canonical.db', 30)    # past 1.5x
    monkeypatch.setattr(history.net, 'get_json', lambda url, **kw: scoreboard() if 'espn' in url else {})
    result = history.collect('tennis', max_requests=3)
    assert result['records'] == 0 and result['storage'] == 'full'
    assert not (tmp_path / 'tennis/atp/canonical.db').exists()


def test_live_window_starts_yesterday(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    calls = []
    def get(url, params=None, **kw):
        calls.append(dict(params or {}))
        return {'events': []}
    monkeypatch.setattr(history.net, 'get_json', get)
    history.collect('soccer', max_requests=4)
    today = datetime.now(timezone.utc).date()
    assert [c['dates'] for c in calls[:4]] == [f'{today+timedelta(days=i):%Y%m%d}' for i in range(-1, 3)]
    calls.clear()
    history.collect('tennis', max_requests=1)
    assert calls[0]['dates'] == f'{today-timedelta(days=1):%Y%m%d}-{today+timedelta(days=2):%Y%m%d}'


def test_every_request_failing_still_exits_1(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    def get(url, **kw):
        raise history.net.HttpError(429, url)
    monkeypatch.setattr(history.net, 'get_json', get)
    monkeypatch.setattr('sys.argv', ['x', 'tennis'])
    with pytest.raises(SystemExit) as e:
        history.main()
    assert e.value.code == 1


def test_challenger_cursor_moves_only_when_its_own_backfill_was_fetched(tmp_path):
    import json
    from sports import expansion_challenger as ch
    def failing(url, params, timeout=10):
        if 'allscores' in url and \
                datetime.strptime(params['startDate'], '%d/%m/%Y').date() < datetime.now(timezone.utc).date() - timedelta(days=1):
            raise history.net.HttpError(None, url)       # the older (backfill) window times out
        return {'games': []}
    ch.collect({}, failing, lambda *a: True, tmp_path)
    assert json.loads((tmp_path / 'challenger-state.json').read_text())['_offset'] == 1
    ch.collect({}, lambda url, params, timeout=10: {'games': []}, lambda *a: True, tmp_path)
    assert json.loads((tmp_path / 'challenger-state.json').read_text())['_offset'] == 8
    # a backfill the storage budget declines does not move the cursor either
    ch.collect({}, lambda url, params, timeout=10: {'games': []}, lambda *a: True, tmp_path, lambda *a: False)
    assert json.loads((tmp_path / 'challenger-state.json').read_text())['_offset'] == 15   # no feeds -> nothing declined
