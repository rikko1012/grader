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
