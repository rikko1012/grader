import copy
import json
from datetime import datetime, timezone

import pytest
import slip_quality as sq
import workspace_api as wa
from workspace_price_slips import draft, positive_pool
from tests.test_slip_quality import rec


def market(mode='arb'):
    row={'leg_id':'nba|e|p|points|full_game|20.5','sport':'nba','event_id':'e',
         'player':'Player','event':'A vs B','stat':'points','stat_label':'Points','period_label':'Full game',
         'line':20.5,'start_utc':'2099-01-01T00:00:00Z'}
    legs=[{'book':b,'book_label':b,'kind':'sportsbook','side':side,'odds':2.1,
           'american':110,'seen_at':'2026-11-13T00:00:00Z','flags':[]}
          for b,side in [('fanduel','over'),('betrivers','under')]]
    if mode=='arb':row['legs']=legs
    else:row.update(legs[0],ev=.05)
    return {'sports':{'nba':{'arbitrage' if mode=='arb' else 'ev':[row]}},'method':{}},row


NOW=datetime(2026,11,13,0,1,tzinfo=timezone.utc).timestamp()


def test_pair_is_two_separate_slips_with_cent_exact_stakes():
    d,r=market();out=draft(d,'arb',r['leg_id'],'fanduel,betrivers','101.01',now=NOW)
    assert len(out['slips'])==2
    assert sum(x['stake'] for x in out['slips'])==pytest.approx(101.01)
    assert out['profit_if_either_wins']==pytest.approx(5.04)
    assert out['minimum_return']==106.05


@pytest.mark.parametrize('change', ['stale','prediction','flag','started','bad_odds','samebook','negative'])
def test_pair_rejects_invalid_or_unprofitable_prices(change):
    d,r=market();books='fanduel,betrivers'
    if change=='stale':r['legs'][0]['seen_at']='2020-01-01T00:00:00Z'
    if change=='prediction':r['legs'][0]['kind']='prediction'
    if change=='flag':r['legs'][0]['flags']=['Stale leg']
    if change=='started':r['start_utc']='2020-01-01T00:00:00Z'
    if change=='bad_odds':r['legs'][0]['odds']=float('nan')
    if change=='samebook':r['legs'][1]['book']='fanduel';books='fanduel,fanduel'
    if change=='negative':r['legs'][0]['odds']=1.8
    with pytest.raises(ValueError):draft(d,'arb',r['leg_id'],books,100,now=NOW)


@pytest.mark.parametrize('stake',['NaN','Infinity','0','100001','bad'])
def test_invalid_budget(stake):
    d,r=market()
    with pytest.raises(ValueError):draft(d,'arb',r['leg_id'],'fanduel,betrivers',stake,now=NOW)


def test_changed_price_refuses_copy_or_generation():
    d,r=market()
    with pytest.raises(ValueError,match='price changed'):
        draft(d,'arb',r['leg_id'],'fanduel,betrivers',100,now=NOW,expected=[['fanduel','over',2.2],['betrivers','under',2.1]])


def test_straight_ev_slip_preserves_book_side_line_and_price():
    d,r=market('ev');out=draft(d,'ev',r['leg_id'],'fanduel:over',25,now=NOW)
    assert out['slips'][0]['line']==20.5 and out['slips'][0]['odds']==2.1
    assert out['slips'][0]['stake']==25 and out['minimum_return'] is None


def test_ev_generator_intersection_does_not_change_model_or_fill_with_negative_legs(monkeypatch):
    records=[rec('p'+str(i),'e'+str(i),.61,book='underdog',start='2099-01-01T00:00:00Z') for i in range(6)]
    legs=[sq.make_leg(r,None) for r in records]
    rows=[dict(key='|'.join((l['sport'],l['book'],l['row_id'])),side=l['side'],line=l['line'],basis='market',ev=.1,p_market=.67) for l in legs[:4]]
    rows+= [dict(rows[0],key='|'.join((legs[4]['sport'],legs[4]['book'],legs[4]['row_id'])),ev=-.1)]
    doc={'rows':rows,'as_of':datetime.now(timezone.utc).isoformat()};
    for r in rows:r['sources']=[dict(kind='sportsbook',age_s=30)]
    original=copy.deepcopy(legs)
    selected=positive_pool(legs,doc)
    assert len(selected)==4 and all(l['p']==.61 and l['rank_p']==.67 for l in selected)
    assert legs==original
    monkeypatch.setattr(wa,'_slip_table',lambda s:None)
    monkeypatch.setattr(wa,'_slip_pool',lambda *args:(legs,{}))
    monkeypatch.setattr(wa,'_wire_slip',lambda s,*args:dict(s))
    opts=dict(platform='underdog',entry_type='standard',count=2,size=2,sports=('cs2',),mix='single',same_match=False,
              no_repeat=True,lines='standard',research=True,exclude=(),side='any')
    status,body=wa._generate(None,opts,doc);out=json.loads(body)
    assert status==200 and len(out['slips'])==2
    assert len({l['key'] for s in out['slips'] for l in s['legs']})==4
    assert all('all_hit' not in s and 'ev' not in s for s in out['slips'])


def test_ev_source_must_stay_fresh_through_generated_cache_window():
    r=rec('p','e',.61,book='underdog',start='2099-01-01T00:00:00Z')
    l=sq.make_leg(r,None)
    row=dict(key='|'.join((l['sport'],l['book'],l['row_id'])),side=l['side'],line=l['line'],
             basis='market',ev=.1,p_market=.67,sources=[dict(kind='exchange',age_s=290)])
    doc=dict(rows=[row],as_of=datetime.now(timezone.utc).isoformat())
    assert positive_pool([l],doc)==[]
    row['sources'][0]['age_s']=30
    assert len(positive_pool([l],doc))==1
    doc['as_of']='2020-01-01T00:00:00Z'
    assert positive_pool([l],doc)==[]
