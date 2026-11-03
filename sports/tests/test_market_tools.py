from types import SimpleNamespace as NS
from sports.market_tools import build
from sports.api_market_tools import view

NOW='2026-11-03T12:00:00Z'


def item(book,oid='id',line=20.5):
    o=NS(platform=book,event_id='nba:event',player_id='nba:p',stat='points',availability='open',
         listed_start_utc='2026-11-03T20:00:00Z',observed_at=NOW,source_updated_at=NOW,
         extras=(('jurisdiction','Ohio'),('feed',book)),rule_version='reviewed-1',league='nba',scope='full_game',line=line,
         sides=(NS(side='over',offered=True,price=2.1),NS(side='under',offered=True,price=2.1)))
    return oid,o,{}


def test_no_house_rule_assumptions_or_invented_ev():
    d=build([item('a'),item('b')],NOW)
    assert d['ev']==d['arbitrage']==[]
    assert d['excluded']['Settlement rules or jurisdiction not verified']==2


def test_verified_exact_rules_find_balanced_quote_comparison():
    d=build([item('a'),item('b')],NOW,rules={'a|reviewed-1':'same','b|reviewed-1':'same'})
    assert len(d['arbitrage'])==2 and d['ev']==[]
    assert d['arbitrage'][0]['stake_shares']==[.5,.5]
    assert not d['arbitrage'][0]['execution_guaranteed']


def test_stale_different_line_and_thin_exchange_do_not_form_arbitrage():
    a=item('a');b=item('b',line=21.5);c=item('polymarket')
    a[1].observed_at='2026-11-03T11:00:00Z'
    d=build([a,b,c],NOW,rules={x+'|reviewed-1':'same' for x in ('a','b','polymarket')})
    assert not d['arbitrage']


def test_validated_probability_requires_exact_offer_revision_and_identity():
    p={'validated':True,'offer_revision':'id','identity':['nba','nba:event','nba:p','points','full_game',20.5],
       'p_over':.6,'p_under':.4,'model_version':'tested'}
    d=build([item('a')],NOW,{'id':p},{'a|reviewed-1':'same'})
    assert len(d['ev'])==1 and abs(d['ev'][0]['ev']-.26)<1e-9
    p['offer_revision']='old'
    assert not build([item('a')],NOW,{'id':p},{'a|reviewed-1':'same'})['ev']


def test_limit_refuses_partial_feed_and_route_default_off(monkeypatch):
    assert build([item('a'),item('b')],NOW,limit=1)['state']=='unavailable'
    monkeypatch.delenv('SCOUT_MARKET_TOOLS',raising=False)
    assert view(NS(query={'league':'nba'}))[0]==404
