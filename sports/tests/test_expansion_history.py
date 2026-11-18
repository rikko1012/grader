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
    assert len(calls)==2 and result['errors']==['HttpError:503:site.api.espn.com']
    assert (tmp_path/'tennis/wta/canonical.db').exists()
    assert not (tmp_path/'tennis/atp/canonical.db').exists()


def _rows(path):
    import sqlite3
    con=sqlite3.connect(path)
    try:return con.execute('SELECT record_id,revision,seen_count,last_seen_at FROM records ORDER BY rid').fetchall()
    finally:con.close()


def test_budget_env_default_override_and_bad_value(monkeypatch,tmp_path):
    for name in ('SCOUT_TENNIS_HISTORY_MAX_MB','SCOUT_SOCCER_HISTORY_MAX_MB','SCOUT_EXPANSION_HISTORY_MAX_MB'):
        monkeypatch.delenv(name,raising=False)
    assert history.storage_budget_bytes('tennis')==4096*1024*1024
    monkeypatch.setenv('SCOUT_EXPANSION_HISTORY_MAX_MB','512')
    monkeypatch.setenv('SCOUT_TENNIS_HISTORY_MAX_MB','1')
    assert history.storage_budget_bytes('tennis')==1024*1024
    assert history.storage_budget_bytes('soccer')==512*1024*1024
    for bad in ('lots','0','-5',''):
        monkeypatch.setenv('SCOUT_TENNIS_HISTORY_MAX_MB',bad)
        monkeypatch.setenv('SCOUT_EXPANSION_HISTORY_MAX_MB',bad)
        assert history.storage_budget_bytes('tennis')==4096*1024*1024
    (tmp_path/'atp').mkdir();(tmp_path/'atp/canonical.db').write_bytes(b'x'*10)
    (tmp_path/'atp/canonical.db-wal').write_bytes(b'x'*5);(tmp_path/'atp/matches.json').write_bytes(b'x'*99)
    assert history.storage_bytes(tmp_path)==15 and history.storage_bytes(tmp_path/'missing')==0


def test_persist_is_idempotent_and_a_changed_fact_is_a_revision(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    body=scoreboard()
    monkeypatch.setattr(history.net,'get_json',lambda url,**kw:body)
    first=history.collect('tennis',max_requests=2)
    path=tmp_path/'tennis/atp/canonical.db';before=_rows(path)
    assert first['records']>0 and first['unchanged']==0 and before
    second=history.collect('tennis',max_requests=2)
    assert second['records']==0 and second['unchanged']>=len(before) and second['errors']==[]
    assert _rows(path)==before  # no row, sighting count or sighting time was rewritten
    body=scoreboard(((6,7),(4,5)))
    third=history.collect('tennis',max_requests=2)
    assert third['records']>0 and max(r[1] for r in _rows(path))==2


def test_budget_reached_skips_backfill_keeps_live_and_exits_zero(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('SCOUT_TENNIS_HISTORY_MAX_MB','1')
    (tmp_path/'tennis/wta').mkdir(parents=True)
    (tmp_path/'tennis/wta/canonical.db-wal').write_bytes(b'\0'*(1024*1024+1))
    history.atomic_json(tmp_path/'tennis/history-state.json',{'offset':22})
    today=datetime.now(timezone.utc)
    def board(day,mid):
        body=scoreboard();c=body['events'][0]['competitions'][0]
        c['id']=mid;c['date']=day.strftime('%Y-%m-%dT%H:%MZ');return body
    calls=[]
    def get(url,**kw):
        calls.append((url,dict(kw.get('params') or {})))
        if 'espn' not in url:return {} if 'allscores' in url else []
        if '/atp/' in url:return board(today-timedelta(hours=1),'live')
        return board(today-timedelta(days=40),'old')
    monkeypatch.setattr(history.net,'get_json',get)
    result=history.collect('tennis')
    boards_asked=[p['dates'] for u,p in calls if 'scoreboard' in u]
    assert len(boards_asked)==2 and len(set(boards_asked))==1  # live window only, no backfill range
    assert len([u for u,_ in calls if 'allscores' in u])==1
    assert result['degraded']=='storage_budget' and result['errors']==[] and result['budget_mb']==1
    assert result['records']>0 and result['scoreboards_ok']==2
    import json
    state=json.loads((tmp_path/'tennis/history-state.json').read_text())
    assert state['offset']==22 and state['degraded']=='storage_budget'
    assert any('live' in r[0] for r in _rows(tmp_path/'tennis/atp/canonical.db'))
    # the 40-day-old match adds no rows while full, but its catalog entry (JSON) still refreshes
    assert not [r for r in _rows(tmp_path/'tennis/wta/canonical.db') if ':old' in r[0]]
    assert json.loads((tmp_path/'tennis/current-matches.json').read_text())
    assert 'research' in result


def test_main_exit_codes(monkeypatch,capsys):
    import sys
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1')
    monkeypatch.setattr(sys,'argv',['x','tennis'])
    base={'matches':0,'errors':[],'scoreboards_ok':2,'requests':3}
    def run(**change):
        monkeypatch.setattr(history,'collect',lambda sport:{**base,**change})
        history.main()
    run();run(matches=4)
    run(errors=['HttpError'])  # quiet day, one detail source failed
    run(errors=['ValueError'],scoreboards_ok=0,degraded='storage_budget')
    with pytest.raises(SystemExit) as stop:
        run(errors=['HttpError','HttpError'],scoreboards_ok=0)
    assert stop.value.code==1


def test_tennis_live_window_is_one_date_and_soccer_asks_for_the_whole_slate(monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    asked=[]
    def get(url,**kw):
        asked.append((url,dict(kw.get('params') or {})));return {}
    monkeypatch.setattr(history.net,'get_json',get)
    history.collect('tennis',max_requests=2)
    today=f'{datetime.now(timezone.utc).date():%Y%m%d}'
    assert [p['dates'] for _,p in asked]==[today,today]  # a range selects tournaments by START date
    assert all(p['limit']==200 for _,p in asked)
    asked.clear()
    history.collect('soccer',max_requests=1)
    assert asked[0][1]=={'dates':today,'limit':1000}


def test_error_labels_name_host_status_and_held_requests():
    net=history.net
    assert history.error_label(net.HttpError(403,'https://api.thescore.com/atp/events'))=='HttpError:403:api.thescore.com'
    assert history.error_label(net.HttpError(429,'https://www.fotmob.com/api/data/x',not_before=1,held=True))=='HttpError:held:www.fotmob.com'
    assert history.error_label(ValueError('roster team mismatch'))=='ValueError:roster team mismatch'
    assert history.error_label(ValueError())=='ValueError' and history.error_label(OSError('disk'))=='OSError'
